"""End-to-end run of the demo script (design doc section 12) through the API, plus guardrails."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from inventra.api.main import app

DROPS = Path(__file__).resolve().parent.parent / "data" / "inbox_drops"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        assert c.post("/reset").status_code == 200
        yield c


def test_mode_is_keyless_and_never_leaks_key(client):
    r = client.get("/settings/mode").json()
    assert r["mode"] == "keyless" and r["typed_questions"] is False
    assert "key" not in str(r).lower().replace("keyless", "").replace("keyed", "")


def test_plan_and_validation(client):
    plan = client.get("/plan").json()
    assert plan["queue"] and "kpis" in plan
    assert plan["kpis"]["after"]["shortage_units"] < plan["kpis"]["before"]["shortage_units"]
    v = client.get("/data/validation").json()
    gap = [i for i in v["issues"] if i["check"] == "missing_days" and i.get("location") == "Sharjah" and "sku" not in i]
    assert gap and gap[0]["days"] == list(range(61, 68))
    mism = [i for i in v["issues"] if i["check"] == "cycle_count_mismatch" and i["sku"] == "CHG-01"]
    assert mism and mism[0]["recorded_available"] == 100 and mism[0]["counted_units"] == 160


def test_forecast_shows_stockout_correction(client):
    f = client.get("/forecast", params={"sku": "CBL-03", "location": "Dubai"}).json()["rows"][0]
    assert f["stockout_correction"]["corrected_total"] > f["stockout_correction"]["observed_total"]
    assert f["stockout_correction"]["out_of_stock_days"] == list(range(74, 82))
    assert len(f["chart"]["history"]) == 90 and len(f["chart"]["forecast"]) == 30


def test_bad_file_is_quarantined_and_nothing_replans(client):
    before = client.get("/plan").json()["kpis"]
    data = (DROPS / "inventory_update_bad.csv").read_bytes()
    r = client.post("/data/upload/inventory", files={"file": ("inventory_update_bad.csv", data, "text/csv")})
    assert r.status_code == 422
    ev = r.json()
    assert ev["status"] == "Quarantined"
    text = " ".join(ev["errors"])
    for needle in ("unknown SKU CHG-09", "unknown location Ajman", "negative available", "reserved 35 above available 20", "duplicate key"):
        assert needle in text
    assert client.get("/plan").json()["kpis"] == before


def test_upload_rejects_runtime_tables(client):
    r = client.post("/data/upload/decisions", files={"file": ("d.csv", b"a,b\n1,2\n", "text/csv")})
    assert r.status_code == 404


def test_signal_drop_replans_and_scopes(client):
    data = (DROPS / "signals_SIG-001.csv").read_bytes()
    ev = client.post("/data/upload/signals", files={"file": ("signals_SIG-001.csv", data, "text/csv")}).json()
    assert ev["status"] == "Loaded" and ev["what_changed"]["changes"]
    imp = client.get("/signals/SIG-001/impact").json()
    hit = sorted(s["sku"] for s in imp["skus"] if s["affected"])
    assert hit == ["CHG-01", "CHG-02", "CHG-03", "CHG-04", "PWB-01", "PWB-02", "PWB-03", "PWB-04"]
    cable = next(s for s in imp["skus"] if s["sku"] == "CBL-01")
    assert "semiconductor" in cable["reason"]
    po104 = next(p for p in imp["pos"] if p["po_id"] == "PO-104")
    assert po104["affected"] and po104["shifted_eta_likely"] == "2026-11-23"
    resent = (DROPS / "signals_SIG-001_resent.csv").read_bytes()
    assert resent == data
    ev2 = client.post("/data/upload/signals", files={"file": ("signals_SIG-001_resent.csv", resent, "text/csv")}).json()
    assert ev2["status"] == "Duplicate"
    feed = client.get("/inbox/events").json()
    assert {e["status"] for e in feed["events"]} >= {"Loaded", "Quarantined", "Duplicate"} and feed["alert_badge"] >= 3


def test_sig002_scoped_to_route(client):
    imp = client.get("/signals/SIG-002/impact").json()
    assert sorted(p["po_id"] for p in imp["pos"] if p["affected"]) == ["PO-105", "PO-106"]
    assert next(p for p in imp["pos"] if p["po_id"] == "PO-109")["reason"] == "not on route R2"


def test_scenario_tree_and_path_approval_guardrails(client):
    t = client.post("/scenario/trees", json={"sku": "CHG-01", "location": "Abu Dhabi"}).json()
    tid = t["tree_id"]
    suggested = [n for n in t["nodes"] if n["source"] == "Suggested"]
    assert len(suggested) == 2
    root = next(n for n in t["nodes"] if n["parent_id"] is None)
    naive = client.post(f"/scenario/trees/{tid}/nodes", json={"parent_id": root["node_id"], "decision": {
        "type": "transfer", "sku": "CHG-01", "from": "Dubai", "to": "Abu Dhabi", "qty": 270}}).json()
    assert naive["robustness"] == "Harmful"
    # Harmful paths can never be approved
    r = client.post("/decisions", json={"action": "approve_path", "tree_id": tid, "node_id": naive["node_id"],
                                         "expected_input_hash": t["plan_input_hash"]})
    assert r.status_code == 409
    # approval needs the hash the planner saw
    best = suggested[0]
    r = client.post("/decisions", json={"action": "approve_path", "tree_id": tid, "node_id": best["node_id"]})
    assert r.status_code == 409
    # a stacked reorder on top of the suggested transfer is evaluated cumulatively
    child = client.post(f"/scenario/trees/{tid}/nodes", json={"parent_id": best["node_id"], "source": "Controls", "decision": {
        "type": "reorder", "sku": "CHG-01", "location": "Abu Dhabi", "qty": 100}}).json()
    assert "net_vs_parent_aed" in child["results"]["totals"]
    r = client.post("/decisions", json={"action": "approve_path", "tree_id": tid, "node_id": best["node_id"],
                                         "expected_input_hash": t["plan_input_hash"], "reason": "split covers the tender"})
    assert r.status_code == 200, r.json()
    dec = r.json()["decisions"][0]
    assert dec["status"] == "Approved" and dec["receipt"]["staff_instructions"]
    assert dec["receipt"]["draft_only"] is True
    # second approval for the same cell is refused
    r = client.post("/decisions", json={"action": "approve_path", "tree_id": tid, "node_id": best["node_id"],
                                         "expected_input_hash": client.get(f"/scenario/trees/{tid}").json()["plan_input_hash"]})
    assert r.status_code == 409


def test_correction_needs_reason_and_evidence_then_marks_stale(client):
    rid = "inventory:CHG-01|Abu Dhabi"
    r = client.post("/corrections", json={"record_id": rid, "field": "available", "new_value": 160, "reason": ""})
    assert r.status_code == 422
    r = client.post("/corrections", json={"record_id": rid, "field": "available", "new_value": 160, "reason": "cycle count"})
    assert r.status_code == 422 and "suggested_evidence_record_id" in r.json()
    ev = r.json()["suggested_evidence_record_id"]
    r = client.post("/corrections", json={"record_id": rid, "field": "available", "new_value": 160,
                                          "reason": "Cycle count at 15:30 found 160", "evidence_record_id": ev})
    assert r.status_code == 200
    wc = r.json()["what_changed"]
    assert any(s["sku"] == "CHG-01" for s in wc["decisions_turned_stale"])
    log = client.get("/corrections").json()["corrections"]
    assert log[-1]["old_value"] == "100" and log[-1]["new_value"] == "160"
    stale = client.get("/decisions", params={"status": "Stale"}).json()["decisions"]
    rec = client.post(f"/decisions/{stale[0]['decision_id']}/recalculate").json()
    assert rec["recommendation"] and "next_step" in rec
    assert client.get("/decisions", params={"status": "Stale"}).json()["decisions"]  # recalculation never approves


def test_reject_needs_reason_and_rejected_option_cannot_be_approved(client):
    plan = client.get("/plan").json()
    rej = next(r for r in plan["rejected"])
    q = next(e for e in plan["queue"] + plan["no_action"] if e["sku"] == rej["sku"] and e["location"] == rej["location"])
    r = client.post("/decisions", json={"action": "approve", "sku": rej["sku"], "location": rej["location"],
                                         "option_id": rej["option_id"], "expected_input_hash": q["input_hash"]})
    assert r.status_code == 409
    top = plan["queue"][0]
    r = client.post("/decisions", json={"action": "reject", "sku": top["sku"], "location": top["location"],
                                         "option_id": top["top"]["option_id"]})
    assert r.status_code == 422


def test_draft_po_guardrail_and_override(client):
    r = client.post("/drafts/po", json={"sku": "PWB-01", "location": "Dubai"}).json()
    assert r["draft_po"]["status"].startswith("Draft") and r["guardrail"]["in_range"]
    hi = r["guardrail"]["safe_range"]["max_qty_max_cover_on_arrival"] + 200
    r2 = client.post("/drafts/po", json={"sku": "PWB-01", "location": "Dubai", "qty": hi}).json()
    assert not r2["guardrail"]["in_range"] and r2["guardrail"]["risk"]["units_above_max_cover"] == 200
    r3 = client.post("/drafts/po", json={"sku": "PWB-01", "location": "Dubai", "qty": hi, "save": True,
                                         "expected_input_hash": r2["input_hash"]})
    assert r3.status_code == 422
    r4 = client.post("/drafts/po", json={"sku": "PWB-01", "location": "Dubai", "qty": hi, "save": True,
                                         "expected_input_hash": r2["input_hash"], "override_reason": "Promotion next month"})
    assert r4.status_code == 200 and r4.json()["decision"]["status"] == "Overridden"


def test_typed_question_hidden_without_key(client):
    tid = client.post("/scenario/trees", json={"sku": "MSE-01", "location": "Abu Dhabi"}).json()["tree_id"]
    r = client.post("/scenario/parse", json={"tree_id": tid, "question": "What if we reorder 50 mice?"})
    assert r.status_code == 409


def test_explanation_keyless_shows_template_and_facts(client):
    plan = client.get("/plan").json()
    e = plan["queue"][0]
    r = client.get("/explain/recommendation", params={"sku": e["sku"], "location": e["location"]}).json()
    assert r["mode"] == "keyless" and r["template"] and r["facts"]["facts"]


def test_signal_edit_requires_reason_and_replans(client):
    r = client.patch("/signals/SIG-003", json={"status": "Confirmed"})
    assert r.status_code == 422
    r = client.patch("/signals/SIG-003", json={"status": "Confirmed", "reason": "Tender award letter received"})
    assert r.status_code == 200 and r.json()["what_changed"]


def test_replay_and_checklist(client):
    r = client.post("/evaluation/replay").json()
    assert r["constraint_breaches"]["count"] == 0, r["constraint_breaches"]
    assert r["inventra"]["lost_sales"] < r["baseline"]["lost_sales"]
    assert len(r["checklist"]) == 12
    by = {c["case_id"]: c for c in r["checklist"]}
    for cid in (2, 3, 4, 7, 8, 9, 10, 12):
        assert by[cid]["result"] == "handled", by[cid]
    assert client.get("/evaluation/report").status_code == 200


def test_audit_trail_records_actions(client):
    acts = {a["action"] for a in client.get("/audit").json()["audit"]}
    assert {"correction", "decision_approved", "decision_stale", "signal_edit", "inbox_quarantined"} <= acts


def test_inbox_detects_table_from_header(client):
    from inventra.service import get_service
    from inventra.watcher import ingest
    data = (DROPS / "inventory_update_bad.csv").read_bytes() + b"\n"  # new content hash
    ev = ingest(get_service(), "inventory_update_bad_2.csv", data, origin="inbox")
    assert ev["target_table"] == "inventory" and ev["status"] == "Quarantined"
    assert any("unknown location Ajman" in e for e in ev["errors"])
