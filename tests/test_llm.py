import json
from types import SimpleNamespace

import anthropic

from algo import llm
from algo.strategies import REGISTRY


def test_propose_parses_structured_output(monkeypatch):
    space = REGISTRY["orb"].space
    payload = {"review": "ok", "proposals": [{"rationale": "tighter", "params": {"vol_mult": 2.0}}]}
    captured = {}

    class FakeMessages:
        def create(self, **kw):
            captured.update(kw)
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(payload))])

    class FakeClient:
        def __init__(self, *a, **k):
            self.beta = SimpleNamespace(messages=FakeMessages())

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    review, props = llm.propose("claude-opus-5-5", "orb", "desc", space, {"or_bars": 3}, {}, 3)
    assert review == "ok" and props[0]["params"]["vol_mult"] == 2.0
    schema = captured["output_config"]["format"]["schema"]
    assert set(schema["properties"]["proposals"]["items"]["properties"]["params"]["properties"]) == set(space)
    assert captured["model"] == "claude-opus-5-5" and captured["fallbacks"] == "default"


def test_propose_handles_refusal(monkeypatch):
    class FakeClient:
        def __init__(self, *a, **k):
            self.beta = SimpleNamespace(messages=SimpleNamespace(
                create=lambda **kw: SimpleNamespace(stop_reason="refusal", content=[])))

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    assert llm.propose("m", "orb", "d", REGISTRY["orb"].space, {}, {}, 2) == ("", [])
