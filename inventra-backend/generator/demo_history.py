"""Writes data/seed/demo_decision_history.json: a few DEMO decisions approved before the planning day,
with their actuals, so the Decision Log and Track record have something to show.

Everything here is synthetic and labelled demo history in the API and UI. Outcome labels, forecast
error, interval coverage, bias and suggestions are NOT stored here: the server computes them from
these records. Run: python3 generator/demo_history.py  (fixed seed, identical output every run)
"""
import json
from pathlib import Path

import numpy as np

rng = np.random.default_rng(20260927)
OUT = Path(__file__).resolve().parent.parent / "data" / "seed" / "demo_decision_history.json"


def daily(total, days=30, noise=0.35):
    """Integer daily series with Poisson-gamma noise that sums exactly to `total`."""
    rate = total / days
    raw = rng.poisson(rng.gamma(1 / noise ** 2, rate * noise ** 2, days))
    raw = np.maximum(raw, 0).astype(float)
    raw = raw / raw.sum() * total if raw.sum() else np.full(days, rate)
    out = np.floor(raw).astype(int)
    for i in np.argsort(-(raw - out))[: int(total - out.sum())]:
        out[i] += 1
    return out.tolist()


def record(**k):
    f = k["forecast"]
    k["forecast_daily_p50"] = [round(f["p50"] / 30, 3)] * 30
    k["baseline_daily"] = [round(k.pop("baseline_total") / 30, 3)] * 30
    if k.get("actuals"):
        k["actuals"]["demand_daily"] = daily(k["actuals"]["demand"])
    return {"demo": True, "approver": "Sara", **k}


RECORDS = [
    record(id="DL-001", sku="CBL-01", location="Abu Dhabi", label="Transfer 90 USB-C cables Dubai → Abu Dhabi",
           decision={"type": "transfer", "legs": [{"from": "Dubai", "to": "Abu Dhabi", "qty": 90}]},
           approved_at="2026-07-29T09:40:00", snapshot="v2026-07-29a",
           settings={"shipment_delay_days": 0, "demand": "expected"},
           forecast={"p10": 120, "p50": 150, "p90": 190}, baseline_total=135,
           expected_net=800, expected_range=[-100, 1500], breaking_points=4,
           assumptions=[
               {"item": "Shipment delay", "assumed": 0, "unit": "days", "breaks_at": 4, "worse": "above", "tag": "Assumption"},
               {"item": "Transfer cost", "assumed": 385, "unit": "AED", "breaks_at": 700, "worse": "above", "tag": "Data"},
               {"item": "Abu Dhabi demand level", "assumed": "P10–P90", "unit": "units", "kind": "demand", "tag": "Calculated"}],
           actuals={"demand": 168, "assumptions": {"Shipment delay": 0, "Transfer cost": 385},
                    "realised_net": 300, "untouched_comparison": 3,
                    "distortion": "A supplied sales note records a competitor promotion in Abu Dhabi on days 12 to 18, which distorts cable demand in the review window."}),
    record(id="DL-002", sku="CHG-02", location="Abu Dhabi", label="Split 80 + 70 USB-C 65W to Abu Dhabi",
           decision={"type": "transfer", "legs": [{"from": "Dubai", "to": "Abu Dhabi", "qty": 80}, {"from": "Sharjah", "to": "Abu Dhabi", "qty": 70}]},
           approved_at="2026-08-14T11:05:00", snapshot="v2026-08-14a",
           settings={"shipment_delay_days": 0, "demand": "expected"},
           forecast={"p10": 300, "p50": 345, "p90": 430}, baseline_total=310,
           expected_net=4700, expected_range=[3100, 6000], breaking_points=5,
           assumptions=[
               {"item": "Shipment delay", "assumed": 0, "unit": "days", "breaks_at": 3, "worse": "above", "tag": "Assumption"},
               {"item": "Transfer cost", "assumed": 1080, "unit": "AED", "breaks_at": 2400, "worse": "above", "tag": "Data"},
               {"item": "Abu Dhabi demand level", "assumed": "P10–P90", "unit": "units", "kind": "demand", "tag": "Calculated"}],
           actuals={"demand": 372, "assumptions": {"Shipment delay": 1, "Transfer cost": 1080},
                    "realised_net": 4400, "untouched_comparison": 4, "distortion": None}),
    record(id="DL-003", sku="CHG-01", location="Abu Dhabi", label="External order 300 USB-C 20W for Abu Dhabi",
           decision={"type": "reorder", "location": "Abu Dhabi", "qty": 300},
           approved_at="2026-09-02T10:20:00", snapshot="v2026-09-02b",
           settings={"shipment_delay_days": 0, "demand": "expected"},
           forecast={"p10": 260, "p50": 345, "p90": 470}, baseline_total=318,
           expected_net=3900, expected_range=[2400, 5000], breaking_points=5,
           assumptions=[
               {"item": "Shipment delay", "assumed": 0, "unit": "days", "breaks_at": 2, "worse": "above", "tag": "Assumption"},
               {"item": "Lead time", "assumed": 12, "unit": "days", "breaks_at": 14, "worse": "above", "tag": "Calculated"},
               {"item": "Order cost", "assumed": 4200, "unit": "AED", "breaks_at": 5000, "worse": "above", "tag": "Data"},
               {"item": "Abu Dhabi demand level", "assumed": "P10–P90", "unit": "units", "kind": "demand", "tag": "Calculated"}],
           actuals={"demand": 385, "assumptions": {"Shipment delay": 3, "Lead time": 15, "Order cost": 4200},
                    "realised_net": 1100, "untouched_comparison": 3, "distortion": None}),
    record(id="DL-004", sku="PWB-01", location="Dubai", label="Transfer 60 power banks Sharjah → Dubai",
           decision={"type": "transfer", "legs": [{"from": "Sharjah", "to": "Dubai", "qty": 60}]},
           approved_at="2026-09-20T14:30:00", snapshot="v2026-09-20a",
           settings={"shipment_delay_days": 0, "demand": "expected"},
           forecast={"p10": 110, "p50": 130, "p90": 160}, baseline_total=124,
           expected_net=650, expected_range=[200, 1100], breaking_points=4,
           assumptions=[
               {"item": "Shipment delay", "assumed": 0, "unit": "days", "breaks_at": 5, "worse": "above", "tag": "Assumption"},
               {"item": "Transfer cost", "assumed": 130, "unit": "AED", "breaks_at": 420, "worse": "above", "tag": "Data"},
               {"item": "Dubai demand level", "assumed": "P10–P90", "unit": "units", "kind": "demand", "tag": "Calculated"}],
           actuals={"demand": 127, "assumptions": {"Shipment delay": 0, "Transfer cost": 130},
                    "realised_net": 700, "untouched_comparison": 5, "distortion": None}),
    record(id="DL-005", sku="PWB-02", location="Sharjah", label="Discount 120 power banks 15% at Sharjah",
           decision={"type": "discount", "location": "Sharjah", "units": 120, "depth_pct": 15, "uplift_pct": 20},
           approved_at="2026-10-20T09:15:00", snapshot="v2026-10-20a",
           settings={"shipment_delay_days": 0, "demand": "expected"},
           forecast={"p10": 30, "p50": 38, "p90": 52}, baseline_total=33,
           expected_net=1300, expected_range=[200, 2100], breaking_points=2,
           assumptions=[
               {"item": "Sell-through uplift", "assumed": 20, "unit": "%", "breaks_at": 9, "worse": "below", "tag": "Assumption"},
               {"item": "Sharjah demand level", "assumed": "P10–P90", "unit": "units", "kind": "demand", "tag": "Calculated"}],
           actuals=None),
]

OUT.write_text(json.dumps({"note": "DEMO HISTORY: synthetic decisions for the Decision Log and Track record. "
                                   "Not real records; labelled demo in the API and UI.",
                           "records": RECORDS}, indent=1, ensure_ascii=False))
print(f"wrote {OUT} ({len(RECORDS)} records)")
