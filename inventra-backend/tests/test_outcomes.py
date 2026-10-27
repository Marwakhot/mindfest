"""Decision Log and outcome review: labels and track record are computed, suggestions need the planner."""
from fastapi.testclient import TestClient
import pytest

from inventra.api.main import app


@pytest.fixture(scope="module")
def c():
    with TestClient(app) as client:
        client.post("/reset")
        yield client


def test_log_mixes_demo_history_and_real_decisions(c):
    L = c.get("/decision-log").json()
    ids = [r["id"] for r in L["rows"]]
    assert {"DL-001", "DL-002", "DL-003", "DL-004", "DL-005"} <= set(ids)
    assert all(r["demo"] for r in L["rows"] if r["id"].startswith("DL-"))
    assert L["counts"]["reviewed"] == 4 and L["counts"]["awaiting"] == 1
    d5 = next(r for r in L["rows"] if r["id"] == "DL-005")
    assert d5["review"] == {"status": "awaiting", "day": 7}


def test_outcome_labels_follow_the_rules(c):
    by = {r["id"]: r["review"] for r in c.get("/decision-log").json()["rows"] if r["review"]["status"] == "reviewed"}
    assert by["DL-001"]["label"] == "Inconclusive"
    assert by["DL-002"]["label"] == "Hit" and by["DL-002"]["forecast"]["inside"]
    assert by["DL-003"]["label"] == "Right call, unlucky"
    assert set(by["DL-003"]["breached"]) == {"Shipment delay", "Lead time"}
    assert by["DL-004"]["label"] == "Hit"
    assert 0 < by["DL-003"]["wape"] < 1 and by["DL-003"]["realised_share"] == pytest.approx(1100 / 3900, abs=1e-3)


def test_track_record_needs_sample_size(c):
    t = c.get("/track-record").json()
    assert t["reviewed"] == 4 and not t["headline_available"] and t["confidence"] == "Low"
    assert t["interval_coverage"] == {"inside": 4, "n": 4, "target_pct": 80}
    assert t["bias"]["Chargers"]["Abu Dhabi"]["n"] == 2 and t["bias"]["Chargers"]["Abu Dhabi"]["bias_pct"] < 0
    assert t["bias"]["Cables"]["Abu Dhabi"]["bias_pct"] is None      # n < 2: too few to judge
    assert t["labels"] == {"Hit": 2, "Right call, unlucky": 1, "Wrong call": 0, "Inconclusive": 1}


def test_suggestion_applies_only_on_approval(c):
    s = c.get("/decision-log").json()["suggestions"]
    sug = next(x for x in s if x["sku"] == "CHG-01" and x["location"] == "Abu Dhabi")
    assert sug["current"] == 60 and sug["proposed"] == 70 and sug["status"] == "Pending"
    assert c.post(f"/suggestions/{sug['suggestion_id']}/review", json={"action": "reject"}).status_code == 422
    r = c.post(f"/suggestions/{sug['suggestion_id']}/review", json={"action": "approve"}).json()
    assert r["suggestion"]["status"] == "Approved" and "correction" in r["what_changed"]["cause"]
    cor = c.get("/corrections").json()["corrections"][-1]
    assert cor["record_id"] == "safety_stock:CHG-01|Abu Dhabi" and cor["old_value"] == "60" and cor["new_value"] == "70"
    assert c.post(f"/suggestions/{sug['suggestion_id']}/review", json={"action": "approve"}).status_code == 409


def test_explain_keyless_template(c):
    x = c.post("/decision-log/DL-003/explain").json()
    assert "Right call, unlucky" in x["template"] and x["facts"]["reasons"]
