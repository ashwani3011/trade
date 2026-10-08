"""Real-money execution of one variant (pdb.base) on Dhan, mirroring its live decisions.

The owner asked for this explicitly on 2026-10-08. Every other variant stays paper.

Each candle the live loop simulates the variant on completed candles (paper). This desk
copies that into real MIS orders and manages them on its own terms:
  * a new paper ORDER -> a market entry now, sized from config/real.yaml (Rs 200 risk),
    then a stop-loss order at Dhan at the strategy's stop, so the stop holds even if the
    server dies
  * the paper position closes (target, stop, squareoff) or paper never took the entry
    -> cancel the stop and exit at market
  * at the square-off time, or on a kill, everything is exited
Limits: max entries a day, max open positions, and realised loss plus open risk never
beyond the daily loss limit. Before any market exit the broker's net position is checked,
so an exit can never open a reverse position.

config/real.yaml is re-read every candle (mode off | shadow | live, kill). In shadow mode
the same flow runs against ShadowBroker, which fills from candles and sends nothing.
The desk only runs on the configured host (the Oracle VM).

State: state/real/<date>.json (positions), state/real/<date>.jsonl (events),
reports/real/<date>.md (summary). No secrets or fund balances are written.
"""
from __future__ import annotations

import json
import logging
import math
import os
import socket
import time
from dataclasses import dataclass, field, fields
from datetime import date, datetime
from pathlib import Path
from typing import Callable

import pandas as pd
import requests
import yaml

from .costs import CostModel

log = logging.getLogger(__name__)

BASE_URL = "https://api.dhan.co/v2"
FILL_WAIT_S = 15         # how long to wait for a market order to fill
DONE = {"TRADED", "REJECTED", "CANCELLED", "EXPIRED"}


@dataclass
class RealConfig:
    mode: str = "off"
    kill: bool = False
    variant: str = "pdb.base"
    host: str = "paper-trader"
    capital: float = 20000.0
    risk_per_trade: float = 200.0
    max_entries_per_day: int = 5
    max_positions: int = 3
    daily_loss_limit: float = 600.0
    max_leverage: float = 5.0
    last_entry: str = "14:30"
    squareoff: str = "15:10"
    sl_limit_buffer_pct: float = 0.5

    @classmethod
    def load(cls, path: str | Path = "config/real.yaml") -> "RealConfig":
        p = Path(path)
        raw = (yaml.safe_load(p.read_text()) or {}) if p.exists() else {}
        known = {f.name for f in fields(cls)}
        cfg = cls(**{k: v for k, v in raw.items() if k in known})
        cfg.mode = str(cfg.mode).lower()
        if cfg.mode not in ("off", "shadow", "live"):
            log.error("real.yaml: unknown mode %r - treated as off", cfg.mode)
            cfg.mode = "off"
        return cfg


def _hm(s: str) -> tuple[int, int]:
    h, m = s.split(":")
    return int(h), int(m)


def round_tick(price: float, tick: float) -> float:
    return round(round(price / tick) * tick, 2)


# ---------------------------------------------------------------- brokers
class BrokerError(RuntimeError):
    pass


class DhanBroker:
    """Dhan v2 order API (orders, positions, funds). Uses the data source's token."""

    def __init__(self, source):
        self.src = source
        self.session = requests.Session()

    def _req(self, method: str, path: str, body: dict | None = None):
        headers = {"access-token": self.src.access_token, "client-id": self.src.client_id,
                   "Content-Type": "application/json", "Accept": "application/json"}
        resp = self.session.request(method, BASE_URL + path, json=body, headers=headers, timeout=20)
        if resp.status_code >= 400:
            raise BrokerError(f"{method} {path} {resp.status_code}: {resp.text[:300]}")
        return resp.json() if resp.text.strip() else {}

    def tick_size(self, symbol: str) -> float:
        try:
            df = self.src.master()
            row = df[(df["SEM_EXM_EXCH_ID"] == "NSE") & (df["SEM_INSTRUMENT_NAME"] == "EQUITY")
                     & (df["SEM_TRADING_SYMBOL"] == symbol)]
            v = float(row["SEM_TICK_SIZE"].iloc[0])
            v = v / 100 if v >= 1 else v       # the master lists ticks in paise
            return v if v > 0 else 0.05
        except Exception:
            return 0.05

    def place(self, symbol: str, side: int, qty: int, kind: str, tag: str,
              trigger: float | None = None, price: float | None = None) -> str:
        body = {
            "dhanClientId": self.src.client_id, "correlationId": tag,
            "transactionType": "BUY" if side > 0 else "SELL", "exchangeSegment": "NSE_EQ",
            "productType": "INTRADAY", "validity": "DAY", "securityId": self.src.security_id(symbol),
            "quantity": int(qty), "afterMarketOrder": False,
            "orderType": {"MARKET": "MARKET", "SL-M": "STOP_LOSS_MARKET", "SL": "STOP_LOSS"}[kind],
            "price": float(price or 0), "triggerPrice": float(trigger or 0),
        }
        try:
            r = self._req("POST", "/orders", body)
        except BrokerError:
            raise
        except Exception as exc:   # timeout: the order may or may not exist - never resend blindly
            oid = self.find(tag)
            if oid:
                return oid
            raise BrokerError(f"place {tag} failed: {exc}") from exc
        oid = str(r.get("orderId") or "")
        if not oid:
            raise BrokerError(f"place {tag}: no orderId in {str(r)[:200]}")
        return oid

    def status(self, order_id: str) -> dict:
        r = self._req("GET", f"/orders/{order_id}")
        if isinstance(r, list):
            r = r[0] if r else {}
        return {"status": str(r.get("orderStatus", "")).upper(), "filled": int(r.get("filledQty") or 0),
                "price": float(r.get("averageTradedPrice") or 0),
                "reason": str(r.get("omsErrorDescription") or "")}

    def cancel(self, order_id: str) -> None:
        self._req("DELETE", f"/orders/{order_id}")

    def find(self, tag: str) -> str | None:
        for o in self._req("GET", "/orders") or []:
            if str(o.get("correlationId")) == tag:
                return str(o.get("orderId"))
        return None

    def net_qty(self, symbol: str) -> int:
        """Net intraday quantity held at Dhan for this stock (matched by security id)."""
        sid = str(self.src.security_id(symbol))
        return sum(int(p.get("netQty") or 0) for p in self._req("GET", "/positions") or []
                   if str(p.get("securityId")) == sid and str(p.get("productType")) == "INTRADAY")

    def health(self) -> str:
        """Read-only checks: can we read funds, positions and orders? No amounts are returned."""
        parts = []
        for name, path in (("funds", "/fundlimit"), ("positions", "/positions"), ("orders", "/orders")):
            try:
                self._req("GET", path)
                parts.append(f"{name} OK")
            except Exception as exc:
                parts.append(f"{name} FAILED ({str(exc)[:120]})")
        return ", ".join(parts)


class ShadowBroker:
    """Sends nothing. Market orders fill at the last completed close; stop orders fill
    when a later completed candle trades through the trigger (at the open if it gapped)."""

    def __init__(self, bars: Callable[[str], pd.DataFrame | None], now: Callable[[], datetime],
                 tick_size: Callable[[str], float] | None = None):
        self.bars, self.now = bars, now
        self._tick = tick_size
        self.orders: dict[str, dict] = {}
        self.net: dict[str, int] = {}

    def tick_size(self, symbol: str) -> float:
        return self._tick(symbol) if self._tick else 0.05

    def restore(self, p: dict, placed: datetime) -> None:
        """Re-adopt an open shadow position after a restart (the broker lives in memory)."""
        self.net[p["symbol"]] = self.net.get(p["symbol"], 0) + p["side"] * p["qty"]
        if p.get("sl_id"):
            self.orders[p["sl_id"]] = {"symbol": p["symbol"], "side": -p["side"], "qty": p["qty"],
                                       "kind": p.get("sl_kind", "SL-M"), "trigger": p["sl_trigger"],
                                       "placed": placed, "status": "PENDING", "filled": 0, "price": 0.0,
                                       "tag": ""}

    def _last(self, symbol: str) -> float:
        b = self.bars(symbol)
        if b is None or b.empty:
            raise BrokerError(f"shadow: no candles for {symbol}")
        return float(b["close"].iloc[-1])

    def place(self, symbol, side, qty, kind, tag, trigger=None, price=None) -> str:
        oid = f"S{time.time_ns()}"
        o = {"symbol": symbol, "side": side, "qty": int(qty), "kind": kind, "trigger": trigger,
             "placed": self.now(), "status": "PENDING", "filled": 0, "price": 0.0, "tag": tag}
        self.orders[oid] = o
        if kind == "MARKET":
            self._fill(o, self._last(symbol))
        return oid

    def _fill(self, o: dict, px: float) -> None:
        o.update(status="TRADED", filled=o["qty"], price=round(px, 2))
        self.net[o["symbol"]] = self.net.get(o["symbol"], 0) + o["side"] * o["qty"]

    def status(self, order_id: str) -> dict:
        o = self.orders.get(order_id)
        if o is None:
            raise BrokerError(f"shadow: unknown order {order_id}")
        if o["status"] == "PENDING" and o["kind"] in ("SL-M", "SL"):
            b = self.bars(o["symbol"])
            if b is not None and not b.empty:
                start = pd.Timestamp(o["placed"]).floor("5min")
                later = b[b["time"] >= start]
                for _, r in later.iterrows():
                    hit = r["high"] >= o["trigger"] if o["side"] > 0 else r["low"] <= o["trigger"]
                    if hit:
                        gapped = (r["open"] - o["trigger"]) * o["side"] > 0
                        self._fill(o, r["open"] if gapped else o["trigger"])
                        break
        return {"status": o["status"], "filled": o["filled"], "price": o["price"], "reason": ""}

    def cancel(self, order_id: str) -> None:
        o = self.orders[order_id]
        if o["status"] == "PENDING":
            o["status"] = "CANCELLED"

    def find(self, tag: str) -> str | None:
        return next((k for k, o in self.orders.items() if o["tag"] == tag), None)

    def net_qty(self, symbol: str) -> int:
        return self.net.get(symbol, 0)

    def health(self) -> str:
        return "shadow broker"


# ------------------------------------------------------------------ desk
@dataclass
class RealDesk:
    state_dir: Path
    reports_dir: Path
    costs: CostModel
    interval: int = 5
    config_path: Path = Path("config/real.yaml")
    kill_file: Path = Path("KILL")
    live_broker_factory: Callable[[], object] | None = None
    hostname: str = field(default_factory=socket.gethostname)
    day: date | None = None
    positions: list[dict] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    _brokers: dict = field(default_factory=dict)
    _health_done: bool = False
    _off_logged: bool = False

    # ---------- persistence
    def _paths(self):
        d = self.day.isoformat()
        base = Path(self.state_dir) / "real"
        return base / f"{d}.json", base / f"{d}.jsonl", Path(self.reports_dir) / "real" / f"{d}.md"

    def _load(self) -> None:
        pos_path, ev_path, _ = self._paths()
        self.positions = json.loads(pos_path.read_text()) if pos_path.exists() else []
        self.events = ([json.loads(x) for x in ev_path.read_text().splitlines() if x.strip()]
                       if ev_path.exists() else [])

    def _save(self) -> None:
        pos_path, _, _ = self._paths()
        pos_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = pos_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.positions, indent=1, default=str))
        os.replace(tmp, pos_path)

    def _event(self, now: datetime, kind: str, msg: str, **kw) -> None:
        e = {"at": now.strftime("%H:%M:%S"), "type": kind, "msg": msg, **kw}
        self.events.append(e)
        _, ev_path, _ = self._paths()
        ev_path.parent.mkdir(parents=True, exist_ok=True)
        with open(ev_path, "a") as f:
            f.write(json.dumps(e, default=str) + "\n")
        (log.error if kind in ("ERROR", "ALERT") else log.info)("REAL %s %s", kind, msg)

    # ---------- helpers
    def _broker(self, mode: str, bars, now_fn):
        if mode not in self._brokers:
            if mode == "live":
                if self.live_broker_factory is None:
                    raise BrokerError("no live broker configured")
                self._brokers[mode] = self.live_broker_factory()
            else:
                live = self._broker("live", bars, now_fn) if self.live_broker_factory else None
                sb = ShadowBroker(bars, now_fn, live.tick_size if live else None)
                for p in self.open_positions():
                    if p["mode"] == "shadow":
                        t = datetime.strptime(p["entry_time"], "%H:%M:%S").time()
                        sb.restore(p, datetime.combine(self.day, t, tzinfo=now_fn().tzinfo))
                self._brokers[mode] = sb
        return self._brokers[mode]

    def _wait_fill(self, broker, oid: str) -> dict:
        deadline = time.time() + FILL_WAIT_S
        st = broker.status(oid)
        while st["status"] not in DONE and time.time() < deadline:
            time.sleep(1)
            st = broker.status(oid)
        return st

    def _tag(self, p: dict, suffix: str) -> str:
        return f"pdb{self.day:%m%d}{p['decided_bar'].replace(':', '')}{p['symbol'][:10]}{suffix}"[:25]

    def open_positions(self) -> list[dict]:
        return [p for p in self.positions if p["status"] == "open"]

    def realised(self) -> float:
        return round(sum(p.get("net") or 0.0 for p in self.positions if p["status"] == "closed"), 2)

    def _halt_path(self) -> Path:
        return self._paths()[0].with_suffix(".halt")

    def halted(self) -> bool:
        return self._halt_path().exists()

    def day_pnl(self, bars) -> float:
        """Realised net plus open positions marked to the last completed close."""
        total = self.realised()
        for p in self.open_positions():
            b = bars(p["symbol"])
            if b is not None and not b.empty:
                total += (float(b["close"].iloc[-1]) - p["entry_price"]) * p["side"] * p["qty"]
        return total

    def open_risk(self) -> float:
        return sum(abs(p["entry_price"] - p["stop"]) * p["qty"] for p in self.open_positions())

    def _close(self, p: dict, now: datetime, price: float | None, reason: str) -> None:
        p.update(status="closed", exit_time=now.strftime("%H:%M:%S"), exit_reason=reason)
        p.pop("exit_pending", None)
        if price:
            px = float(price)
            gross = (px - p["entry_price"]) * p["side"] * p["qty"]
            buy, sell = ((p["entry_price"], px) if p["side"] > 0 else (px, p["entry_price"]))
            cost = self.costs.round_trip(buy * p["qty"], sell * p["qty"])
            p.update(exit_price=round(px, 2), gross=round(gross, 2), costs=round(cost, 2), net=round(gross - cost, 2))
        else:
            p.update(exit_price=None, net=0.0)
        self._event(now, "EXIT", f"{p['symbol']} {reason} @ {p.get('exit_price')} net {p.get('net')}",
                    symbol=p["symbol"], mode=p["mode"])

    # ---------- the round
    def sync(self, day: date, now: datetime, res, bars: Callable[[str], pd.DataFrame | None]) -> None:
        cfg = RealConfig.load(self.config_path)
        if cfg.host and self.hostname.split(".")[0] != cfg.host:
            if not self._off_logged:
                log.info("real desk: host %s is not %s - not running here", self.hostname, cfg.host)
                self._off_logged = True
            return
        if self.day != day:
            self.day, self._brokers, self._health_done = day, {}, False
            self._load()
        now_fn = lambda: now  # noqa: E731
        kill = cfg.kill or self.kill_file.exists()
        if cfg.mode != "off" and not self._health_done:
            try:
                h = self._broker("live" if self.live_broker_factory else cfg.mode, bars, now_fn).health()
            except Exception as exc:
                h = f"FAILED ({exc})"
            self._event(now, "NOTE", f"mode {cfg.mode}; broker check: {h}")
            self._health_done = True
        if isinstance(self._brokers.get("shadow"), ShadowBroker):   # this candle's data and clock
            self._brokers["shadow"].now, self._brokers["shadow"].bars = now_fn, bars

        # 0) an entry interrupted by a restart: find it at the broker by its tag
        for p in [x for x in self.positions if x["status"] == "placing"]:
            self._recover(p, cfg, now, bars, now_fn)

        # 1) stops that filled at the broker
        for p in self.open_positions():
            if p.get("sl_id"):
                try:
                    st = self._broker(p["mode"], bars, now_fn).status(p["sl_id"])
                except Exception as exc:
                    self._event(now, "ERROR", f"{p['symbol']} stop status failed: {exc}")
                    continue
                if st["status"] == "TRADED":
                    self._close(p, now, st["price"], "stop (at Dhan)")
                elif st["status"] in DONE:
                    self._event(now, "ALERT", f"{p['symbol']} stop is {st['status']} at Dhan - placing it again")
                    p["sl_id"] = None

        # 2) live: a position already flat at Dhan (auto square-off, manual exit) is closed here
        if any(p["mode"] == "live" for p in self.open_positions()):
            b = self._broker("live", bars, now_fn)
            for p in [x for x in self.open_positions() if x["mode"] == "live"]:
                try:
                    if b.net_qty(p["symbol"]) * p["side"] > 0:
                        continue
                    if p.get("sl_id"):     # a leftover stop could open a new position later
                        b.cancel(p["sl_id"])
                except Exception as exc:
                    self._event(now, "ERROR", f"{p['symbol']} position check failed: {exc}")
                    continue
                self._close(p, now, None, "already flat at Dhan")
                self._event(now, "ALERT", f"{p['symbol']} was flat at Dhan - closed without a price, stop cancelled")

        # 3) exits: kill, daily loss, square-off, paper closed / skipped, earlier failed exits
        sq = now.time() >= datetime.strptime(cfg.squareoff, "%H:%M").time()
        if not self.halted() and self.day_pnl(bars) <= -cfg.daily_loss_limit:
            self._event(now, "ALERT", f"daily loss limit hit (Rs {self.day_pnl(bars):.0f}) - exiting all, "
                        "no more entries today")
            self._halt_path().write_text(now.isoformat())
        halted = self.halted()
        for p in self.open_positions():
            why = p.get("exit_pending")
            if kill:
                why = "kill switch"
            elif halted:
                why = "daily loss limit"
            elif sq:
                why = "squareoff"
            elif res is not None and not why:
                why = self._paper_exit(p, res)
            if why:
                self._exit(p, now, why, bars, now_fn)
            elif not p.get("sl_id"):
                self._place_stop(p, self._broker(p["mode"], bars, now_fn), cfg, now, bars, now_fn, exit_on_fail=False)

        # 4) new entries
        if res is not None and cfg.mode in ("shadow", "live") and not kill and not sq and not halted:
            for o in res.pending_orders:
                self._maybe_enter(o, cfg, now, bars, now_fn)

        self._save()
        self._report(cfg, now, kill)

    def _paper_exit(self, p: dict, res) -> str | None:
        """Why the real position should exit now, judged by the paper variant (None = hold)."""
        sym, exp = p["symbol"], p["expect_entry"]
        if any(o["symbol"] == sym and o["entry_time"] == exp for o in res.open_positions):
            return None
        for t in res.trades:
            if t.symbol == sym and t.entry_time == exp:
                return f"paper {t.exit_reason}"
        if any(o["symbol"] == sym and o["decided_bar"] == p["decided_bar"] for o in res.pending_orders):
            return None      # the fill candle hasn't completed yet
        return "paper did not take the entry"

    def _exit(self, p: dict, now: datetime, why: str, bars, now_fn) -> None:
        b = self._broker(p["mode"], bars, now_fn)
        try:
            if p.get("sl_id"):
                st = b.status(p["sl_id"])
                if st["status"] == "TRADED":
                    return self._close(p, now, st["price"], "stop (at Dhan)")
                if st["status"] not in DONE:
                    b.cancel(p["sl_id"])
                    st = self._wait_fill(b, p["sl_id"])
                    if st["status"] == "TRADED":
                        return self._close(p, now, st["price"], "stop (at Dhan)")
                    if st["status"] not in DONE:   # never exit while the stop may still fill
                        p["exit_pending"] = why
                        return self._event(now, "ALERT", f"{p['symbol']} stop cancel unconfirmed - retry next candle")
                p["sl_id"] = None
            net = b.net_qty(p["symbol"])
            if net * p["side"] <= 0:
                self._event(now, "ALERT", f"{p['symbol']}: nothing to exit at the broker")
                return self._close(p, now, None, f"{why} (already flat)")
            qty = min(p["qty"], abs(net))
            p["exit_attempts"] = p.get("exit_attempts", 0) + 1
            oid = b.place(p["symbol"], -p["side"], qty, "MARKET", self._tag(p, f"x{p['exit_attempts']}"))
            st = self._wait_fill(b, oid)
            if st["status"] == "TRADED":
                return self._close(p, now, st["price"], why)
            p["exit_pending"] = why
            self._event(now, "ALERT", f"{p['symbol']} exit not filled ({st['status']} {st['reason']}) - retry next candle")
        except Exception as exc:
            p["exit_pending"] = why
            self._event(now, "ALERT", f"{p['symbol']} exit failed: {exc} - retry next candle")
        if p["status"] == "open" and not p.get("sl_id") and why != "no stop possible":
            self._place_stop(p, b, None, now, bars, now_fn, exit_on_fail=False)   # never leave it unprotected

    def _maybe_enter(self, o: dict, cfg: RealConfig, now: datetime, bars, now_fn) -> None:
        key = f"{o['symbol']}|{o['decided_bar']}"
        if any(p["key"] == key for p in self.positions):
            return
        sym, side, stop, target = o["symbol"], int(o["side"]), float(o["stop"]), o.get("target")
        p = {"key": key, "symbol": sym, "side": side, "stop": stop, "target": target,
             "decided_bar": o["decided_bar"], "mode": cfg.mode, "status": "skipped",
             "expect_entry": (datetime.strptime(o["decided_bar"], "%H:%M")
                              + pd.Timedelta(minutes=self.interval)).strftime("%H:%M")}
        self.positions.append(p)

        def skip(why: str) -> None:
            p["skip_reason"] = why
            self._event(now, "SKIP", f"{sym} {o['decided_bar']}: {why}", symbol=sym)

        if (now.hour, now.minute) > _hm(cfg.last_entry):
            return skip(f"after last entry time {cfg.last_entry}")
        entries = [x for x in self.positions if x["status"] in ("open", "closed")]
        if len(entries) >= cfg.max_entries_per_day:
            return skip(f"max {cfg.max_entries_per_day} entries a day reached")
        if len(self.open_positions()) >= cfg.max_positions:
            return skip(f"max {cfg.max_positions} open positions")
        b = bars(sym)
        if b is None or b.empty:
            return skip("no candles")
        est = float(b["close"].iloc[-1])
        risk_ps = (est - stop) * side
        if risk_ps <= 0 or (target is not None and (float(target) - est) * side <= 0):
            return skip(f"price {est} already past stop/target")
        used = sum(x["entry_price"] * x["qty"] for x in self.open_positions())
        qty = min(math.floor(cfg.risk_per_trade / risk_ps),
                  math.floor(max(0.0, cfg.capital * cfg.max_leverage - used) / est))
        if qty < 1:
            return skip("quantity below 1")
        if self.realised() - qty * risk_ps < -cfg.daily_loss_limit:
            return skip(f"daily loss limit Rs {cfg.daily_loss_limit:.0f}: realised {self.realised():.0f} "
                        f"and this trade risks {qty * risk_ps:.0f}")

        broker = self._broker(cfg.mode, bars, now_fn)
        p.update(status="placing", qty=qty, est_price=est)
        self._save()
        try:
            oid = broker.place(sym, side, qty, "MARKET", self._tag(p, "e"))
            p["entry_id"] = oid
            st = self._wait_fill(broker, oid)
            if st["status"] != "TRADED" and st["filled"] > 0:
                try:
                    broker.cancel(oid)
                except Exception:
                    pass
            if st["filled"] <= 0:
                p["status"] = "rejected"
                return self._event(now, "ERROR", f"{sym} entry {st['status']}: {st['reason']}", symbol=sym)
        except Exception as exc:
            p["status"] = "rejected"
            return self._event(now, "ERROR", f"{sym} entry failed: {exc}", symbol=sym)

        p.update(status="open", qty=st["filled"], entry_price=round(st["price"], 2), entry_time=now.strftime("%H:%M:%S"))
        self._event(now, "ENTRY", f"{'BUY' if side > 0 else 'SELL'} {sym} {p['qty']} @ {p['entry_price']} "
                    f"(est {est:.2f}), stop {stop}, target {target} [{cfg.mode}]", symbol=sym)
        if (p["entry_price"] - stop) * side <= 0:
            return self._exit(p, now, "filled beyond the stop", bars, now_fn)
        self._place_stop(p, broker, cfg, now, bars, now_fn)

    def _recover(self, p: dict, cfg: RealConfig, now: datetime, bars, now_fn) -> None:
        if p["mode"] != "live":      # shadow orders lived in memory only
            p["status"] = "rejected"
            return self._event(now, "NOTE", f"{p['symbol']} interrupted shadow entry dropped")
        b = self._broker(p["mode"], bars, now_fn)
        try:
            oid = p.get("entry_id") or b.find(self._tag(p, "e"))
            st = b.status(oid) if oid else None
        except Exception as exc:
            return self._event(now, "ERROR", f"{p['symbol']} entry recovery failed: {exc} - retry next candle")
        if not st or st["filled"] <= 0:
            p["status"] = "rejected"
            return self._event(now, "NOTE", f"{p['symbol']} interrupted entry was not filled")
        p.update(status="open", entry_id=oid, qty=st["filled"], entry_price=round(st["price"], 2),
                 entry_time=now.strftime("%H:%M:%S"))
        self._event(now, "ENTRY", f"{p['symbol']} interrupted entry recovered: {p['qty']} @ {p['entry_price']}")
        self._place_stop(p, b, cfg, now, bars, now_fn)

    def _place_stop(self, p: dict, broker, cfg: RealConfig | None, now: datetime, bars, now_fn,
                    exit_on_fail: bool = True) -> None:
        cfg = cfg or RealConfig.load(self.config_path)
        tick = broker.tick_size(p["symbol"])
        trig = round_tick(p["stop"], tick)
        for kind in ("SL-M", "SL"):
            # a long's stop sells below the trigger, a short's buys above it
            price = round_tick(trig * (1 - p["side"] * cfg.sl_limit_buffer_pct / 100), tick) if kind == "SL" else None
            try:
                oid = broker.place(p["symbol"], -p["side"], p["qty"], kind, self._tag(p, "s" if kind == "SL-M" else "l"),
                                   trigger=trig, price=price)
                st = broker.status(oid)
                if st["status"] in ("REJECTED", "CANCELLED", "EXPIRED"):
                    self._event(now, "ERROR", f"{p['symbol']} {kind} stop {st['status']}: {st['reason']}")
                    continue
                p.update(sl_id=oid, sl_kind=kind, sl_trigger=trig)
                self._event(now, "STOP", f"{p['symbol']} {kind} at {trig}" + (f" limit {price}" if price else "")
                            + f" (tick {tick})", symbol=p["symbol"])
                return
            except Exception as exc:
                self._event(now, "ERROR", f"{p['symbol']} {kind} stop failed: {exc}")
        if exit_on_fail:
            self._event(now, "ALERT", f"{p['symbol']}: no stop could be placed - exiting now")
            self._exit(p, now, "no stop possible", bars, now_fn)
        else:
            self._event(now, "ALERT", f"{p['symbol']}: open WITHOUT a stop at Dhan - retrying next candle")

    # ---------- report
    def _report(self, cfg: RealConfig, now: datetime, kill: bool) -> None:
        _, _, rep = self._paths()
        rep.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"# Real trading ({cfg.variant}) - {self.day} (as of {now:%H:%M:%S} IST)", "",
                 f"Mode **{cfg.mode}**{' - KILL SWITCH ON' if kill else ''}"
                 f"{' - STOPPED FOR THE DAY (loss limit)' if self.halted() else ''}. Capital Rs {cfg.capital:.0f}, "
                 f"risk Rs {cfg.risk_per_trade:.0f}/trade, max {cfg.max_entries_per_day} entries, "
                 f"daily loss limit Rs {cfg.daily_loss_limit:.0f}. Shadow rows are simulated, not sent.", "",
                 f"Realised net: **Rs {self.realised():.2f}**, open risk Rs {self.open_risk():.2f}", "",
                 "| symbol | mode | side | qty | decided | entry | stop | exit | reason | net |",
                 "|---|---|---|---|---|---|---|---|---|---|"]
        for p in self.positions:
            if p["status"] == "skipped":
                continue
            lines.append(f"| {p['symbol']} | {p['mode']} | {'long' if p['side'] > 0 else 'short'} | {p.get('qty', '')} | "
                         f"{p['decided_bar']} | {p.get('entry_price', '')} | {p.get('sl_trigger', p['stop'])} | "
                         f"{p.get('exit_price', '') if p['status'] == 'closed' else p['status']} | "
                         f"{p.get('exit_reason', p.get('exit_pending', ''))} | {p.get('net', '')} |")
        lines += ["", "## Events", ""] + [f"- {e['at']} **{e['type']}** {e['msg']}" for e in self.events]
        rep.write_text("\n".join(lines) + "\n")
