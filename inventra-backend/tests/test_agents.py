"""Agents: the copilot and the watcher agent investigate with Lab tabs and never approve anything."""
import json
from pathlib import Path

import pytest

from inventra.agent import copilot, runner, tools, watch
from inventra.llm import client as llm_client
from inventra.service import get_service
from inventra.watcher import ingest

DROPS = Path(__file__).resolve().parent.parent / "data" / "inbox_drops"


@pytest.fixture(scope="module")
def svc():
    s = get_service()
    s.reset()
    return s


def _decisions(svc):
    return len(svc.decisions())


def test_copilot_rule_based_opens_tabs_and_briefs(svc):
    before = _decisions(svc)
    run = copilot.start(svc, "CHG-01", "Abu Dhabi", background=False)
    assert run["status"] == "done" and run["mode"] == "rule-based"
    tools_used = [s["tool"] for s in run["steps"]]
    assert tools_used[:3] == ["get_cell", "get_network", "list_options"]
    assert "create_scenario" in tools_used and "stress_test" in tools_used
    assert run["tabs"] and run["recommended_tab_id"] in [t["tab_id"] for t in run["tabs"]]
    assert "Nothing is approved" in run["answer"]
    assert _decisions(svc) == before  # the agent never records a decision
    ws = [t for t in svc.store.query("SELECT source, name FROM lab_tabs WHERE sku = 'CHG-01' AND location = 'Abu Dhabi'")]
    assert any(t["source"] == "Agent" and t["name"].startswith("Agent · ") for t in ws)
    assert runner.get_run(svc, run["run_id"])["status"] == "done"


def test_copilot_reuses_tabs_on_second_run(svc):
    n = svc.store.count("lab_tabs")
    run = copilot.start(svc, "CHG-01", "Abu Dhabi", background=False)
    assert svc.store.count("lab_tabs") == n
    assert any("already tests" in s["note"] for s in run["steps"])


def test_tools_refuse_other_workspaces_and_limit_scenarios(svc):
    from inventra.lab import Lab
    ctx = tools.Ctx(svc, "CHG-01", "Abu Dhabi", "test")
    other = next(t for t in Lab(svc).workspace("PWB-01", "Dubai")["tabs"] if not t["pinned"])
    out, _, ok = tools.execute(ctx, "stress_test", {"tab_id": other["tab_id"], "settings": {}})
    assert not ok and "not in the" in out["error"]
    mine = [t for t in Lab(svc).tabs("CHG-01", "Abu Dhabi") if t["letter"] != "0"][:2]
    out, _, ok = tools.execute(ctx, "compare_scenarios", {"tab_a": mine[0]["letter"], "tab_b": "tab " + mine[1]["letter"].lower()})
    assert ok and out["a"]["tab_id"] == mine[0]["tab_id"]  # the agent may name tabs by letter
    out, _, ok = tools.execute(ctx, "approve", {})
    assert not ok and "unknown tool" in out["error"]
    out, _, ok = tools.execute(ctx, "get_cell", {"sku": "CHG-01", "units": 5})
    assert not ok and "bad arguments" in out["error"]
    ctx.tabs = [{"tab_id": str(i)} for i in range(tools.MAX_SCENARIOS)]
    out, _, ok = tools.execute(ctx, "create_scenario", {"option_id": "x"})
    assert not ok and "limit" in out["error"]


def test_unfinished_run_from_a_stopped_server_does_not_block_the_copilot(svc):
    run = runner.new_run(svc, "copilot", "CBL-02", "Abu Dhabi", "test")
    assert runner.get_run(svc, run["run_id"])["status"] == "queued"
    runner._LIVE.discard(run["run_id"])  # as if the server restarted before it ran
    assert runner.get_run(svc, run["run_id"])["status"] == "failed"
    assert copilot.start(svc, "CBL-02", "Abu Dhabi", background=False)["status"] == "done"


def test_llm_fallback_still_opens_tabs_after_llm_used_the_limit(svc, monkeypatch):
    """The LLM opens tabs then fails; the rule-based fallback must still test options, not hit the tab limit."""
    from inventra.config import settings
    monkeypatch.setattr(type(settings), "keyed", property(lambda self: True))
    opts = tools.list_options(tools.Ctx(svc, "CHG-01", "Abu Dhabi", "x"))[0]["options"]

    def fake(messages, specs, max_tokens=800, model=None):
        n = sum(m["role"] == "tool" for m in messages)
        if n < tools.MAX_SCENARIOS:
            o = opts[n % len(opts)]
            return {"content": "", "tool_calls": [{"id": f"c{n}", "function": {"name": "create_scenario", "arguments": json.dumps(
                {"option_id": o["option_id"], "settings": {"demand": ["low", "expected", "high"][n % 3]}})}}]}
        raise llm_client.LLMUnavailable("timeout")

    monkeypatch.setattr(llm_client, "chat_tools", fake)
    run = copilot.start(svc, "CHG-01", "Abu Dhabi", "help", background=False)
    assert run["mode"] == "rule-based (LLM fallback)" and run["recommended_tab_id"]
    assert "no option to test" not in run["answer"]


def test_disagreeing_with_the_engine_must_be_explained(svc, monkeypatch):
    """The agent recommends the lower-net tab: silently is sent back; naming the engine's pick is accepted, both recorded."""
    from inventra.config import settings
    monkeypatch.setattr(type(settings), "keyed", property(lambda self: True))
    opts = [o for o in tools.list_options(tools.Ctx(svc, "CHG-01", "Abu Dhabi", "x"))[0]["options"]
            if o["status"] in ("Recommended", "Alternative") and o["type"] != "wait"][:2]
    sent_back = []

    def fake(messages, specs, max_tokens=800, model=None):
        n = sum(m["role"] == "tool" for m in messages)
        call = lambda name, args: {"content": "", "tool_calls": [{"id": f"c{n}", "function": {"name": name, "arguments": json.dumps(args)}}]}
        if n < 2:
            return call("create_scenario", {"option_id": opts[n]["option_id"]})
        tabs = [json.loads(m["content"]) for m in messages if m["role"] == "tool" and '"letter"' in m["content"]]
        best, other = sorted(tabs, key=lambda t: -t["net_benefit_aed"])
        if n == 2:
            return call("finish", {"answer": f"Use tab {other['letter']}.", "recommended_tab_id": other["tab_id"]})
        sent_back.append(messages[-1]["content"])
        return call("finish", {"answer": f"Use tab {other['letter']} over tab {best['letter']}: it is safer.", "recommended_tab_id": other["tab_id"]})

    monkeypatch.setattr(llm_client, "chat_tools", fake)
    run = copilot.start(svc, "CHG-01", "Abu Dhabi", "What is safest?", background=False)
    assert run["mode"] == "llm" and sent_back and "engine's pick" in sent_back[0]
    assert run["engine_tab_id"] and run["recommended_tab_id"] and run["engine_tab_id"] != run["recommended_tab_id"]
    assert runner.get_run(svc, run["run_id"])["engine_tab_id"] == run["engine_tab_id"]


def test_empty_reply_is_nudged_and_moves_to_the_backup_model(svc, monkeypatch):
    from inventra.config import settings
    monkeypatch.setattr(type(settings), "keyed", property(lambda self: True))
    object.__setattr__(settings, "llm_fallback_models", ["backup/model"])
    seen = []

    def fake(messages, specs, max_tokens=800, model=None):
        seen.append(model)
        if len(seen) == 1:
            return {"content": "", "tool_calls": []}  # the empty reply small models sometimes give
        cell = next((m for m in messages if m["role"] == "tool"), None)
        if cell is None:
            return {"content": "", "tool_calls": [{"id": "c1", "function": {"name": "get_cell", "arguments": "{}"}}]}
        return {"content": "", "tool_calls": [{"id": "c2", "function": {"name": "finish", "arguments": json.dumps({"answer": "CHG-01 is Short."})}}]}

    monkeypatch.setattr(llm_client, "chat_tools", fake)
    try:
        run = copilot.start(svc, "CHG-01", "Abu Dhabi", "Status?", background=False)
    finally:
        object.__setattr__(settings, "llm_fallback_models", [])
    assert run["mode"] == "llm" and seen[0] is None and seen[1:] == ["backup/model"] * (len(seen) - 1)


def test_grounding_rejects_invented_numbers():
    outputs = [json.dumps({"net_benefit_aed": 1840, "letter": "B", "sku": "CHG-01"})]
    assert runner.grounding_errors("Tab B nets AED 1,840 for CHG-01.", outputs) == []
    errs = runner.grounding_errors("Tab B nets AED 2,500 and fixes PWB-01.", outputs)
    assert any("2500" in e for e in errs) and any("PWB-01" in e for e in errs)


def test_watcher_agent_investigates_after_sig001(svc):
    before = _decisions(svc)
    data = (DROPS / "signals_SIG-001.csv").read_bytes()
    ev = ingest(svc, "signals_SIG-001.csv", data, origin="upload")
    assert ev["status"] == "Loaded"
    picked = watch.pick_cells(svc, ev["what_changed"])
    assert picked and all(r for _, _, r in picked)
    from inventra.config import settings
    object.__setattr__(settings, "agent_watch_enabled", True)
    try:
        runs = watch.on_loaded(svc, ev, background=False)
    finally:
        object.__setattr__(settings, "agent_watch_enabled", False)
    assert runs
    done = [runner.get_run(svc, r["run_id"]) for r in runs]
    assert all(r["kind"] == "watcher" and r["status"] == "done" and r["event_id"] == ev["event_id"] for r in done)
    assert all(r["trigger"].startswith("upload file signals_SIG-001.csv") and r["answer"].startswith(r["sku"]) for r in done)
    assert _decisions(svc) == before


def test_llm_loop_with_mocked_tool_calls(svc, monkeypatch):
    """A scripted model: reads the cell, opens the top option, then finishes with a grounded answer."""
    from inventra.config import settings
    monkeypatch.setattr(type(settings), "keyed", property(lambda self: True))
    turns = []

    def fake(messages, specs, max_tokens=800, model=None):
        turns.append(messages)
        n = sum(m["role"] == "tool" for m in messages)
        call = lambda name, args: {"content": "", "tool_calls": [{"id": f"c{n}", "type": "function",
                                                                  "function": {"name": name, "arguments": json.dumps(args)}}]}
        if n == 0:
            return call("get_cell", {})
        if n == 1:
            return call("list_options", {})
        if n == 2:
            opts = json.loads(messages[-1]["content"])["options"]
            return call("create_scenario", {"option_id": opts[0]["option_id"]})
        tab = json.loads(next(m["content"] for m in messages if m["role"] == "tool" and '"letter"' in m["content"]))
        if n == 3:  # first finish invents a number: the loop must send it back
            return call("finish", {"answer": f"Use tab {tab['letter']}: it nets AED 987654."})
        return call("finish", {"answer": f"Use tab {tab['letter']}: it nets AED {tab['net_benefit_aed']:,}.",
                               "recommended_tab_id": tab["tab_id"]})

    monkeypatch.setattr(llm_client, "chat_tools", fake)
    run = copilot.start(svc, "CHG-01", "Abu Dhabi", "What should I do here?", background=False)
    assert run["status"] == "done" and run["mode"] == "llm"
    assert [s["tool"] for s in run["steps"]] == ["get_cell", "list_options", "create_scenario"]
    assert "987654" not in run["answer"] and run["recommended_tab_id"]
    assert any("answer failed checks" in m["content"] for m in turns[-1] if m["role"] == "tool")
