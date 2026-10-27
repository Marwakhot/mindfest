"""Section 6 formulas on the section 10.4 worked example (unit-test fixture).

Fixture: flat daily demand, margin AED 35, pack size 10, PO-104 100 units into Abu Dhabi on day 20
from the semiconductor supplier. Forecast values are fixed inputs so the formulas are tested exactly.
"""
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from inventra import candidates as cand
from inventra.engine import Engine
from inventra.forecast import CellForecast, History
from inventra.state import State

H = 30
DAY90 = date(2026, 10, 27)
P50 = {"Dubai": 200, "Abu Dhabi": 350, "Sharjah": 150}
P90 = {"Dubai": 260, "Abu Dhabi": 430, "Sharjah": 200}
FREE = {"Dubai": 500, "Abu Dhabi": 100, "Sharjah": 300}
SS = {"Dubai": 60, "Abu Dhabi": 60, "Sharjah": 40}
PARAMS = {"horizon_days": 30, "max_cover_days": 45, "ageing_days": 60, "aged_days": 90,
          "ageing_cost_pct_per_30_days": 1.5, "review_buffer_days": 2, "review_period_days": 7,
          "base_rate_window_days": 28, "low_confidence_band_widening": 25.0,
          "confidence_thresholds": {"medium_pct": 10, "low_pct": 25, "min_units": 20}, "urgency_soon_days": 5,
          "freshness_days": 2, "baseline_reorder_days": 30, "backtest_step_days": 15}
SIG001 = {"signal_id": "SIG-001", "type": "supply", "components": ["power_management_ic"], "routes": [], "skus": [],
          "locations": [], "delay_min": 3.0, "delay_likely": 7.0, "delay_max": 12.0, "uplift_min": None,
          "uplift_likely": None, "uplift_max": None, "start_day": 1, "end_day": 30, "status": "Unverified",
          "include": True, "record_id": "signals:SIG-001"}


def fixture_engine(with_signal: bool, ad_free: int = 100) -> Engine:
    free = dict(FREE, **{"Abu Dhabi": ad_free})
    inventory = {("CHG-01", l): {"available": free[l], "reserved": 0, "as_of": datetime(2026, 10, 27, 6),
                                 "record_id": f"inventory:CHG-01|{l}"} for l in free}
    batches = pd.DataFrame([{"sku": "CHG-01", "location": l, "batch_id": f"B-{l}", "units": free[l],
                             "received_date": date(2026, 10, 1), "age_days": 26} for l in free])
    lanes = {}
    for a, b, f, u, lt in [("Dubai", "Sharjah", 100, 0.5, 1), ("Dubai", "Abu Dhabi", 250, 1.5, 1),
                           ("Sharjah", "Abu Dhabi", 400, 5.0, 2)]:
        lanes[(a, b)] = lanes[(b, a)] = {"fixed_cost_aed": f, "cost_per_unit_aed": u, "lead_days": lt}
    st = State(
        day90=DAY90, params=PARAMS,
        skus={"CHG-01": {"sku": "CHG-01", "name": "USB-C 20W charger", "category": "Chargers",
                         "components": ["power_management_ic", "housing"], "unit_cost": 40.0, "unit_price": 75.0,
                         "pack_size": 10, "moq": 100, "supplier": "SUP-CHG", "inbound_route": "R1"}},
        sales=pd.DataFrame(columns=["date", "sku", "location", "units", "in_stock_flag", "day"]),
        inventory=inventory, counts=pd.DataFrame(), batches=batches, safety={("CHG-01", l): SS[l] for l in SS},
        inbound=[{"po_id": "PO-104", "sku": "CHG-01", "location": "Abu Dhabi", "qty": 100, "eta": date(2026, 11, 16),
                  "eta_day": 20, "supplier": "SUP-CHG", "route": "R1", "status": "Confirmed", "changeable": False,
                  "expedite_days": None, "expedite_cost_aed": None, "record_id": "inbound_orders:PO-104"}],
        lead_history=pd.DataFrame({"supplier": ["SUP-CHG"], "sku": ["CHG-01"], "order_date": [date(2026, 9, 1)],
                                   "received_date": [date(2026, 9, 14)]}),
        lanes=lanes, holding={"Dubai": 0.04, "Abu Dhabi": 0.035, "Sharjah": 0.03},
        signals=[dict(SIG001)] if with_signal else [], params_meta=[],
    )
    hist = History(np.zeros(90), np.ones(90), np.zeros(90, bool), np.zeros(90, bool))
    cells = {("CHG-01", l): CellForecast(sku="CHG-01", location=l, base=np.full(H, P50[l] / H), m10=0.8,
                                         m90=P90[l] / P50[l], uplift={k: [0.0] * H for k in ("min", "likely", "max")},
                                         signal_ids=[], confidence="High", confidence_detail={}, observed_total=0,
                                         corrected_total=0, oos_days=[], imputed_days=[], corrected_series=np.zeros(90),
                                         history=hist, weekly_pattern_used=False) for l in P50}
    return Engine(st, {"cells": cells, "meta": {}})


def options(eng):
    spare = {l: eng.spare("CHG-01", l) for l in eng.locs}
    return cand.cell_options(eng, "CHG-01", "Abu Dhabi", spare, cand.cell_status(eng, "CHG-01", "Abu Dhabi"))


def by_variant(res, variant):
    return [o for o in res["options"] if o["decision"].get("variant") == variant]


def test_spare_and_need_before_signal():
    e = fixture_engine(False)
    assert e.spare("CHG-01", "Dubai") == pytest.approx(180)      # 500 - 260 - 60
    assert e.spare("CHG-01", "Sharjah") == pytest.approx(60)     # 300 - 200 - 40
    assert e.spare("CHG-01", "Abu Dhabi") == 0
    assert e.need("CHG-01", "Abu Dhabi", 1)[0] == 210


def test_signal_moves_po_and_raises_need():
    e = fixture_engine(True)
    po = e.inbound("CHG-01")[0]
    assert po["day"] == 27 and po["signals"] == ["SIG-001"]   # PO-104 moves from day 20 to day 27
    assert e.need("CHG-01", "Abu Dhabi", 1)[0] == 270
    lead, info = e.supplier_lead("CHG-01")
    assert lead == 13 + 7 and info["signal_delay_days"] == 7


def test_before_signal_dubai_only_recommended_and_full_transfer_rejected():
    res = options(fixture_engine(False))
    top = res["top"]
    assert top["decision"]["type"] == "transfer" and top["decision"]["legs"] == [{"from": "Dubai", "to": "Abu Dhabi", "qty": 180}]
    assert top["robustness"]["label"] == "Robust"
    split = by_variant(res, "split")[0]
    assert split["net_benefit_aed"] < top["net_benefit_aed"]      # the Sharjah leg avoids no lost sales at P50
    assert split["decision"]["legs"][1] == {"from": "Sharjah", "to": "Abu Dhabi", "qty": 30}
    naive = by_variant(res, "naive")[0]
    assert naive["decision"]["legs"][0]["qty"] == 210 and naive["status"] == "Rejected"
    assert "Safe" in naive["failing_checks"]
    assert any("falls to 30" in f for f in naive["checks"]["safe"]["failures"])   # Dubai 500 - 210 - 260 = 30


def test_after_signal_split_recommended_and_full_transfer_harmful():
    res = options(fixture_engine(True))
    top = res["top"]
    assert top["decision"]["variant"] == "split"
    assert top["decision"]["legs"] == [{"from": "Dubai", "to": "Abu Dhabi", "qty": 180},
                                       {"from": "Sharjah", "to": "Abu Dhabi", "qty": 60}]
    assert top["robustness"]["label"] == "Robust"
    single = [o for o in by_variant(res, "single") if o["decision"]["legs"][0]["from"] == "Dubai"][0]
    assert single["robustness"]["label"] == "Robust" and single["net_benefit_aed"] < top["net_benefit_aed"]
    naive = by_variant(res, "naive")[0]
    assert naive["decision"]["legs"][0]["qty"] == 270
    assert naive["robustness"]["label"] == "Harmful"
    p90 = [c for c in naive["robustness"]["cases"] if c["demand"] == "P90" and c["delay"] == "likely"][0]
    assert p90["lost_sales"]["Dubai"] == pytest.approx(30, abs=0.5)        # 500 - 270 - 260


def test_correction_to_cycle_count_restores_dubai_only():
    e = fixture_engine(True, ad_free=160)
    assert e.need("CHG-01", "Abu Dhabi", 1)[0] == 210
    top = options(e)["top"]
    assert top["decision"]["legs"] == [{"from": "Dubai", "to": "Abu Dhabi", "qty": 180}]


def test_input_hash_changes_with_correction():
    assert fixture_engine(True).sku_input_hash("CHG-01") != fixture_engine(True, 160).sku_input_hash("CHG-01")


def test_net_benefit_formula():
    e = fixture_engine(False)
    d = {"type": "transfer", "variant": "single", "sku": "CHG-01", "legs": [{"from": "Dubai", "to": "Abu Dhabi", "qty": 180}]}
    base, opt = e.simulate("CHG-01", []), e.simulate("CHG-01", [d])
    n = e.net("CHG-01", opt, base)
    expected = 35 * (base.total("lost") - opt.total("lost")) - (opt.total("holding") - base.total("holding")) \
        - (opt.total("ageing_cost") - base.total("ageing_cost")) - (250 + 180 * 1.5)
    assert n["net_benefit_aed"] == pytest.approx(expected, abs=0.01)
    assert n["direct_cost_aed"] == 520


def test_reorder_order_by_and_quantity():
    e = fixture_engine(True)
    ro = cand.reorder_plan(e, "CHG-01", "Abu Dhabi")
    L = 20
    assert ro["lead"]["lead_days"] == L
    assert ro["order_by_day"] == ro["breach_day"] - L - 2
    raw = (350 / 30) * (L + 7) + 60 - (100 + 100)
    assert ro["raw_qty"] == pytest.approx(raw, abs=0.1)
    assert ro["decision"]["qty"] % 10 == 0 and ro["decision"]["qty"] >= 100
