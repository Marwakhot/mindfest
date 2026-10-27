"""Keyed mode with a mocked provider: verify, retry once with the error named, then fall back."""
import dataclasses

import pytest

from inventra import config
from inventra.llm import client, explain
from inventra.service import HITLError, Service
from inventra.storage import Store

FACTS = {"facts": [{"id": "F1", "label": "Recommended option", "value": "Transfer 180 Dubai -> Abu Dhabi", "tag": "Estimate"},
                   {"id": "F2", "label": "Net benefit vs Do nothing", "value": 4728, "unit": "AED", "tag": "Estimate"}],
         "reasons": [{"id": "R1", "text": "Abu Dhabi falls below safety stock", "tag": "Estimate"}]}
GOOD = "Move 180 units from Dubai to Abu Dhabi [F1]. This is needed because Abu Dhabi runs short [R1]. Net benefit is 4728 AED [F2]."
BAD = "Move 200 units from Dubai to Abu Dhabi [F1]. This is needed because Abu Dhabi runs short [R1]. Net benefit is 4728 AED [F2]."


@pytest.fixture
def keyed(monkeypatch, tmp_path):
    s = dataclasses.replace(config.settings, llm_api_key="test", llm_base_url="http://llm.invalid", llm_model="m")
    monkeypatch.setattr(explain, "settings", s)
    monkeypatch.setattr(client, "settings", s)
    return Service(Store(tmp_path / "t.duckdb"))


def test_verified_and_cached(keyed, monkeypatch):
    calls = []
    monkeypatch.setattr(client, "chat", lambda m, **k: calls.append(m) or GOOD)
    r = explain.explain(keyed, "recommendation", FACTS, 30)
    assert r["verified"] and r["badge"] == "All 2 figures verified against source data"
    explain.explain(keyed, "recommendation", FACTS, 30)
    assert len(calls) == 1  # second call served from the cache


def test_retry_names_error_then_passes(keyed, monkeypatch):
    replies = iter([BAD, GOOD])
    seen = []
    monkeypatch.setattr(client, "chat", lambda m, **k: seen.append(m) or next(replies))
    r = explain.explain(keyed, "recommendation", FACTS, 30)
    assert r["verified"] and "200" in seen[1][-1]["content"]


def test_two_failures_fall_back_to_facts_table(keyed, monkeypatch):
    monkeypatch.setattr(client, "chat", lambda m, **k: BAD)
    r = explain.explain(keyed, "recommendation", FACTS, 30)
    assert not r["verified"] and r["text"] is None and r["facts"] and r["validator_errors"]


def test_provider_down_falls_back(keyed, monkeypatch):
    def boom(*a, **k):
        raise client.LLMUnavailable("timeout")
    monkeypatch.setattr(client, "chat", boom)
    r = explain.explain(keyed, "recommendation", FACTS, 30)
    assert r["text"] is None and "facts table" in r["fallback"]


def test_parse_rejects_unknown_sku_and_extra_fields(keyed, monkeypatch):
    tree = {"sku": "PWB-01", "location": "Abu Dhabi", "assumptions": {"demand": "P50", "signals": {}, "lanes": [], "stock_corrections": []}}
    monkeypatch.setattr(client, "chat", lambda m, **k: '{"decision": {"type": "reorder", "sku": "PWB-99", "location": "Abu Dhabi", "qty": 50}}')
    with pytest.raises(HITLError):
        explain.parse_question(keyed, tree, "What if we reorder 50 power banks for Abu Dhabi?")
    monkeypatch.setattr(client, "chat", lambda m, **k: '{"decision": {"type": "reorder", "sku": "PWB-01", "location": "Abu Dhabi", "qty": 50, "run": true}}')
    with pytest.raises(HITLError):
        explain.parse_question(keyed, tree, "What if we reorder 50 power banks for Abu Dhabi?")
    monkeypatch.setattr(client, "chat", lambda m, **k: '{"decision": {"type": "reorder", "sku": "PWB-01", "location": "Abu Dhabi", "qty": 50}}')
    r = explain.parse_question(keyed, tree, "What if we also reorder 50 power banks for Abu Dhabi?")
    assert r["supported"] and r["builder"]["decision"]["qty"] == 50
    monkeypatch.setattr(client, "chat", lambda m, **k: '{"unsupported": true, "missing_input": "competitor prices"}')
    r = explain.parse_question(keyed, tree, "What if a competitor cuts prices?")
    assert not r["supported"] and r["message"] == "This can't be tested with the available data"
