"""Real-time paper trader.

Runs from before the open to after the close. Just after each 5-minute candle
closes (hh:m0:20 / hh:m5:20) it fetches the candles that exist at that moment,
lets every active variant decide on exactly that information, and writes each
new decision (entry, exit, skip) to a timestamped journal:

  state/live/<date>.jsonl       one JSON line per decision, with decided_at
  reports/live/<date>.md        open positions, closed trades, decision log

Decisions come from the same engine as the end-of-day record, fed only
completed candles, so the journal and the final ledger always agree. The loop
is restart-safe: after a crash it replays the day so far and only journals
decisions it hasn't journaled before.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from . import clock, report
from .config import Settings, make_option_source, make_source
from .families import DataHub, DayResult, kind, simulate
from .state import Store

log = logging.getLogger(__name__)

START = (9, 14)          # start polling just before the open
STOP = (15, 36)          # after the last candle (15:25-15:30) is final
POLL_DELAY_S = 20        # wait after a candle closes so the data provider has it


def next_poll(now: datetime, interval: int = 5) -> datetime:
    base = now.replace(second=0, microsecond=0)
    minute = (base.minute // interval + 1) * interval
    nxt = base.replace(minute=0) + timedelta(minutes=minute)
    return nxt + timedelta(seconds=POLL_DELAY_S)


def _events(variant: str, res: DayResult) -> list[dict]:
    """Stable, de-duplicable decisions contained in a replay result."""
    ev = []
    for t in res.trades:
        ev.append({"key": f"{variant}|entry|{t.symbol}|{t.entry_time}", "type": "ENTRY", "variant": variant,
                   "symbol": t.symbol, "bar_time": t.entry_time, "price": t.entry, "qty": t.qty, "side": t.side,
                   "reason": t.reason})
        ev.append({"key": f"{variant}|exit|{t.symbol}|{t.exit_time}", "type": "EXIT", "variant": variant,
                   "symbol": t.symbol, "bar_time": t.exit_time, "price": t.exit, "qty": t.qty,
                   "reason": t.exit_reason, "net": t.net})
    for o in res.open_positions:
        ev.append({"key": f"{variant}|entry|{o['symbol']}|{o['entry_time']}", "type": "ENTRY", "variant": variant,
                   "symbol": o["symbol"], "bar_time": o["entry_time"], "price": o["entry"], "qty": o["qty"],
                   "side": o.get("side", 1), "stop": o["stop"], "target": o["target"]})
    for o in res.pending_orders:
        ev.append({"key": f"{variant}|order|{o['symbol']}|{o['decided_bar']}", "type": "ORDER", "variant": variant,
                   "symbol": o["symbol"], "bar_time": o["decided_bar"], "side": o["side"],
                   "price": o["order"], "reason": o.get("reason", "")})
    for n in res.notes:
        ev.append({"key": f"{variant}|note|{n}", "type": "NOTE", "variant": variant, "reason": n})
    return ev


class LiveTrader:
    def __init__(self, s: Settings, source=None, option_source=None):
        self.s = s
        src = source or make_source(s)
        self.hub = DataHub(s, src, option_source or make_option_source(s, src))
        self.store = Store(s.state_dir)
        self.day: date | None = None
        self.seen: set[str] = set()

    @property
    def journal_path(self) -> Path:
        return Path(self.s.state_dir) / "live" / f"{self.day.isoformat()}.jsonl"

    def _load_journal(self) -> list[dict]:
        if not self.journal_path.exists():
            return []
        return [json.loads(line) for line in self.journal_path.read_text().splitlines() if line.strip()]

    def tick(self) -> list[dict]:
        """One decision round on the candles available right now. Returns new decisions."""
        now = clock.now_ist()
        if self.day != now.date():
            self.day = now.date()
            self.seen = {e["key"] for e in self._load_journal()}
        reg = self.store.load_registry(self.s.strategies)
        active = reg.active()
        if not self.hub.load([self.day], {kind(v.family) for v in active}, min_bars=1):
            log.info("no completed candles yet")
            return []
        results: dict[str, DayResult] = {}
        for v in active:
            ctx = self.hub.context(v.family, self.day)
            if ctx is None:
                continue
            try:
                results[v.id] = simulate(v.family, v.params, ctx, self.store.equity(v.id, self.s.capital),
                                         self.s, variant=v.id, final=False)
            except Exception:   # one broken variant must not stop the others
                log.exception("%s: decision round failed", v.id)
        new = []
        for vid, res in results.items():
            for e in _events(vid, res):
                if e["key"] not in self.seen:
                    self.seen.add(e["key"])
                    e["decided_at"] = now.strftime("%H:%M:%S")
                    new.append(e)
        if new:
            self.journal_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.journal_path, "a") as f:
                for e in new:
                    f.write(json.dumps(e, default=str) + "\n")
                    log.info("%s %-6s %-18s %s %s", e["decided_at"], e["type"], e["variant"],
                             e.get("symbol", ""), e.get("reason", "") or e.get("price", ""))
        report.write_live(self.s.reports_dir, self.day, now, results, journal=self._load_journal())
        return new

    def run(self, until: tuple[int, int] = STOP) -> None:
        """Decide on every completed candle until `until` (default: after the close).
        The day is recorded only when the run reaches the close, so a run can be split
        into several processes (e.g. two GitHub jobs) that continue one journal."""
        while True:
            now = clock.now_ist()
            if now.weekday() >= 5 or (now.hour, now.minute) >= min(until, STOP):
                break
            if (now.hour, now.minute) < START:
                time.sleep(min(60.0, (now.replace(hour=START[0], minute=START[1], second=0) - now).total_seconds()))
                continue
            try:
                self.tick()
            except Exception:
                log.exception("tick failed - will retry on the next candle")
            wait = (next_poll(clock.now_ist(), self.s.interval) - clock.now_ist()).total_seconds()
            time.sleep(max(1.0, wait))
        from .runner import run_day

        today = clock.today_ist()
        if clock.is_final(today) and today.weekday() < 5:
            run_day(self.s, today)


# ------------------------------------------------------------- supervision
def _pid_file(s: Settings) -> Path:
    return Path(s.cache_dir) / "live.pid"


def running_pid(s: Settings) -> int | None:
    try:
        pid = int(_pid_file(s).read_text())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def ensure_running(s: Settings, argv: list[str]) -> tuple[int, bool]:
    """Start the live loop in the background unless it already runs. Returns (pid, started)."""
    pid = running_pid(s)
    if pid:
        return pid, False
    log_path = Path(s.cache_dir) / "live.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [sys.executable, "-m", "algo.cli", *argv, "trade-live"],
        stdout=open(log_path, "a"), stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        start_new_session=True, cwd=os.getcwd(),
    )
    _pid_file(s).write_text(str(proc.pid))
    return proc.pid, True


def stop(s: Settings) -> bool:
    pid = running_pid(s)
    if pid:
        os.kill(pid, signal.SIGTERM)
        return True
    return False
