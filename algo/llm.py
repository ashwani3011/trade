"""Claude as a research assistant for the improvement loop.

Claude never places or sizes trades. It reads performance data and proposes
new parameter sets, which then face the same walk-forward gate and live paper
probation as randomly generated candidates.
"""
from __future__ import annotations

import json
import logging
import os

log = logging.getLogger(__name__)

SYSTEM = """You are a quantitative trading researcher reviewing intraday strategies on NSE cash equities \
(5-minute bars, MIS, Rs 20,000 paper capital, realistic Indian costs and slippage already applied).
Propose parameter changes that plausibly improve robust, after-cost expectancy - not ones that merely fit noise.
Prefer changes with a market-structure rationale (e.g. filtering low-volume breakouts, avoiding overextended \
opening ranges). Only use the parameters and value ranges given. Be concise."""


def _schema(space: dict[str, tuple]) -> dict:
    props = {}
    for name, spec in space.items():
        kind = spec[0]
        if kind == "float":
            props[name] = {"type": "number", "description": f"range {spec[1]}..{spec[2]}"}
        elif kind == "int":
            props[name] = {"type": "integer", "description": f"range {spec[1]}..{spec[2]}"}
        elif kind == "bool":
            props[name] = {"type": "boolean"}
        else:
            props[name] = {"type": "string", "enum": list(spec[1])}
    proposal = {
        "type": "object",
        "properties": {
            "rationale": {"type": "string"},
            "params": {"type": "object", "properties": props, "required": sorted(props), "additionalProperties": False},
        },
        "required": ["rationale", "params"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "review": {"type": "string"},
            "proposals": {"type": "array", "items": proposal},
        },
        "required": ["review", "proposals"],
        "additionalProperties": False,
    }


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def propose(
    model: str,
    family: str,
    description: str,
    space: dict[str, tuple],
    champion_params: dict,
    evidence: dict,
    n: int,
) -> tuple[str, list[dict]]:
    """Returns (review text, list of {rationale, params}). Empty on any failure."""
    import anthropic

    client = anthropic.Anthropic()
    prompt = (
        f"Strategy family: {family}\nDescription: {description}\n\n"
        f"Tunable parameter space (name: spec):\n{json.dumps({k: list(v) for k, v in space.items()}, indent=1)}\n\n"
        f"Current champion params:\n{json.dumps(champion_params, indent=1)}\n\n"
        f"Evidence (backtest + live paper results, trade breakdowns):\n{json.dumps(evidence, indent=1, default=str)}\n\n"
        f"Write a short review of what is and isn't working, then propose {n} distinct parameter sets to test."
    )
    try:
        resp = client.beta.messages.create(
            model=model,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM,
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": _schema(space)}},
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIError as exc:
        log.warning("LLM proposal request failed for %s: %s", family, exc)
        return "", []
    if resp.stop_reason in ("refusal", "max_tokens"):
        log.warning("LLM proposal for %s stopped with %s", family, resp.stop_reason)
        return "", []
    text = "".join(b.text for b in resp.content if b.type == "text")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        log.warning("LLM returned non-JSON output for %s", family)
        return "", []
    return data.get("review", ""), list(data.get("proposals", []))[:n]
