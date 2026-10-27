"""Scenario Lab tabs: settings scoped to supplied events, discount maths, human review guards, compare."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from inventra.api.main import app

DROPS = Path(__file__).resolve().parent.parent / "data" / "inbox_drops"


@pytest.fixture(scope="module")
def c():
    with TestClient(app) as client:
        client.post("/reset")
        client.post("/data/upload/signals", files={"file": ("signals_SIG-001.csv", (DROPS / "signals_SIG-001.csv").read_bytes(), "text/csv")})
        yield client


def ws(c, sku="CHG-01", loc="Abu Dhabi"):
    return c.get("/lab/workspace", params={"sku": sku, "location": loc}).json()


def test_workspace_seeds_do_nothing_and_two_suggestions(c):
    w = ws(c)
    assert [t["letter"] for t in w["tabs"]][:3] == ["0", "A", "B"]
    assert w["tabs"][0]["name"] == "Do nothing" and w["tabs"][0]["pinned"]
    assert all(t["source"] == "Suggested" for t in w["tabs"][1:3])
    ev = w["events"][0]
    assert ev["signal_id"] == "SIG-001" and ev["untouched"] == 12


def test_tab_results_are_engine_statistics(c):
    a = next(t for t in ws(c)["tabs"] if t["letter"] == "A")
    r = c.get(f"/lab/tabs/{a['tab_id']}").json()["results"]
    s = r["summary"]
    assert s["net_range"][0] <= s["net"] <= s["net_range"][1]
    assert 0 <= s["stockout_focus"]["after"] <= s["stockout_focus"]["before"] <= 100
    assert abs(sum(b["value"] for b in r["bridge"]) - r["bridge_net"]) < 1.0      # the bridge adds up to the net
    assert len(r["projection"]["option"]) == 31 and r["projection"]["safety_stock"] == 60
    assert r["breaking_points"]["most_sensitive"] and r["checks"]["passes"]
    assert {x["tag"] for x in r["assumptions"]} <= {"Data", "Calculated", "Assumption"}


def test_settings_only_apply_supplied_events(c):
    t = c.post("/lab/tabs", json={"sku": "CHG-01", "location": "Abu Dhabi", "decision": {"type": "reorder", "location": "Abu Dhabi", "qty": 300}}).json()
    r = c.patch(f"/lab/tabs/{t['tab_id']}", json={"settings": {"route_delay": "severe"}})
    assert r.status_code == 422 and "No supplied shipping-route event" in r.json()["error"]
    base = t["results"]["timing"]["arrival_day"]
    sev = c.patch(f"/lab/tabs/{t['tab_id']}", json={"settings": {"supply_shortage": "severe", "shipment_delay_days": 3}}).json()
    assert sev["results"]["timing"]["arrival_day"] == base + 5 + 3      # severe = max delay (12) vs likely (7), plus 3 days
    assert "Supply shortage severe" in sev["chips"]
    ei = c.get("/lab/event-impact", params={"sku": "CHG-01", "location": "Abu Dhabi"}).json()
    row = next(x for x in ei["rows"] if x["tab_id"] == t["tab_id"])
    assert row["arrival_with"] > row["arrival_without"] and row["net_with"] < row["net_without"] and row["alert"]


def test_discount_net_and_uplift_breaking_point(c):
    t = c.post("/lab/tabs", json={"sku": "KBD-04", "location": "Dubai",
                                  "decision": {"type": "discount", "location": "Dubai", "units": 50, "depth_pct": 15, "uplift_pct": 20}}).json()
    r = t["results"]
    assert any(b["label"] == "Discount margin given up" and b["value"] < 0 for b in r["bridge"])
    assert any(a["label"].startswith("Sell-through uplift") and a["tag"] == "Assumption" for a in r["assumptions"])
    assert r["breaking_points"]["rows"][0]["variable"] == "Assumed sell-through uplift"


def test_discount_formula_matches_engine():
    from inventra.service import get_service
    eng, _ = get_service().ensure()
    d = {"type": "discount", "sku": "KBD-04", "location": "Dubai", "units": 50, "depth_pct": 15.0, "uplift_pct": 20.0}
    b, o = eng.simulate("KBD-04", []), eng.simulate("KBD-04", [d])
    n = eng.net("KBD-04", o, b)
    m = eng.state.margin("KBD-04")
    expected = m * (o.total("sold") - b.total("sold")) - (o.total("holding") - b.total("holding")) \
        - (o.total("ageing_cost") - b.total("ageing_cost")) - o.discount_cost
    assert n["net_benefit_aed"] == pytest.approx(expected, abs=0.01) and o.discount_cost > 0


def test_review_guards_and_compare(c):
    w = ws(c)
    a, b = [t for t in w["tabs"] if t["letter"] in ("A", "B")]
    cmp = c.get("/lab/compare", params={"a": a["tab_id"], "b": b["tab_id"]}).json()
    assert "beats" in cmp["verdict"] and len(cmp["rows"]) == 8
    assert c.get("/lab/compare", params={"a": w["tabs"][0]["tab_id"], "b": a["tab_id"]}).status_code == 422
    assert c.post(f"/lab/tabs/{a['tab_id']}/review", json={"action": "approve"}).status_code == 409          # no hash
    assert c.post(f"/lab/tabs/{b['tab_id']}/review", json={"action": "send_back"}).status_code == 422        # no reason
    sb = c.post(f"/lab/tabs/{b['tab_id']}/review", json={"action": "send_back", "reason": "check truck"}).json()
    assert sb["tab"]["status"] == "Sent back"
    h = c.get(f"/lab/tabs/{a['tab_id']}").json()["results"]["input_hash"]
    ok = c.post(f"/lab/tabs/{a['tab_id']}/review", json={"action": "approve", "expected_input_hash": h, "reason": "approved in lab"})
    assert ok.status_code == 200 and ok.json()["decision"]["status"] == "Approved"
    assert ok.json()["decision"]["receipt"]["lab"]["tab_id"] == a["tab_id"]
    locked = c.patch(f"/lab/tabs/{a['tab_id']}", json={"settings": {"demand": "high"}})
    assert locked.status_code == 409 and "Fork" in locked.json()["error"]
    f = c.post(f"/lab/tabs/{a['tab_id']}/fork").json()
    assert f["forked_from"] == a["tab_id"] and f["status"] in ("Draft", "Ready")


def test_harmful_tab_cannot_be_approved(c):
    t = c.post("/lab/tabs", json={"sku": "CHG-01", "location": "Abu Dhabi", "decision": {"type": "transfer", "from": "Dubai", "to": "Abu Dhabi", "qty": 270}}).json()
    assert t["results"]["checks"]["robustness"] == "Harmful" and t["status"] == "Draft"
    r = c.post(f"/lab/tabs/{t['tab_id']}/review", json={"action": "approve", "expected_input_hash": t["results"]["input_hash"], "reason": "x"})
    assert r.status_code == 409


def test_chat_needs_key(c):
    t = ws(c)["tabs"][-1]
    assert c.post(f"/lab/tabs/{t['tab_id']}/chat", json={"message": "What if I transfer 50?"}).status_code == 409
