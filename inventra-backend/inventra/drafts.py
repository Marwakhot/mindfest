"""Draft PO with soft guardrail (N4). Draft POs are never sent anywhere."""
from __future__ import annotations

from typing import Any, Dict, Optional

from . import candidates as cand
from . import plan as planmod
from . import receipts
from .engine import ceil_pack, floor_pack
from .service import HITLError, Service, require_reason


def draft_po(svc: Service, sku: str, location: str, qty: Optional[int] = None, save: bool = False,
             override_reason: Optional[str] = None, expected_input_hash: Optional[str] = None) -> Dict[str, Any]:
    with svc.lock:
        eng, plan = svc.ensure()
        st = eng.state
        if (sku, location) not in st.inventory:
            raise HITLError(f"Unknown SKU-location {sku} / {location}", 404)
        srow = st.skus[sku]
        pack, moq = srow["pack_size"], srow["moq"]
        L, lead = eng.supplier_lead(sku)
        R = eng.p("review_period_days")
        ro = cand.reorder_plan(eng, sku, location)
        order_day = ro["decision"]["order_day"] if ro else 0
        free = st.free(sku, location)
        ss = st.safety.get((sku, location), 0)
        open_in = sum(a["qty"] for a in eng.inbound(sku) if a["loc"] == location)
        d10 = float(eng.demand(sku, location, "P10").mean())
        d50 = eng.d50(sku, location)
        need10 = d10 * (L + R) + ss - (free + open_in)
        low = max(moq, ceil_pack(need10, pack)) if need10 > 0 else 0
        arrival = order_day + L
        path = eng.stock_path(sku, location, "P50", "likely")
        s_arr = max(0.0, float(path[min(arrival, eng.H) - 1])) if arrival >= 1 else float(free)
        high = floor_pack(eng.p("max_cover_days") * d50 - s_arr, pack)
        prefill = ro["decision"]["qty"] if ro else low
        q = int(qty if qty is not None else prefill)
        if q <= 0:
            raise HITLError("Quantity must be positive", 422)
        decision = {"type": "reorder", "sku": sku, "location": location, "qty": q, "order_day": order_day}
        in_range = low <= q <= high
        risk: Dict[str, Any] = {}
        if q < low:
            lost_q = eng.simulate(sku, [decision]).locs[location].lost
            lost_low = eng.simulate(sku, [dict(decision, qty=low)]).locs[location].lost
            risk = {"units_below_p10_need": low - q, "extra_lost_sales_p50": round(lost_q - lost_low, 1),
                    "tag": "Estimate"}
        elif q > high:
            hold_q = eng.simulate(sku, [decision]).total("holding")
            hold_hi = eng.simulate(sku, [dict(decision, qty=max(high, 0))]).total("holding")
            risk = {"units_above_max_cover": q - high, "extra_holding_cost_aed_in_horizon": round(hold_q - hold_hi, 2),
                    "tag": "Estimate"}
            if high < moq:
                risk["note"] = f"MOQ {moq} is above the max-cover cap ({high}); any order creates excess"
        out = {
            "draft_po": {"status": "Draft - not sent", "sku": sku, "name": srow["name"], "location": location,
                         "supplier": srow["supplier"], "qty": q, "prefilled_qty": prefill,
                         "order_by_date": str(st.plan_date(order_day)), "expected_arrival": str(st.plan_date(arrival)),
                         "lead_time": lead},
            "guardrail": {"safe_range": {"min_qty_covers_p10_need": low, "max_qty_max_cover_on_arrival": high},
                          "in_range": in_range, "risk": risk or None,
                          "formula": "min = d(P10) x (L + R) + SafetyStock - (Free + Open inbound); "
                                     "max = max_cover_days x d(P50) - projected stock on arrival"},
            "reorder_plan": ro,
            "input_hash": eng.sku_input_hash(sku),
        }
        if not in_range:
            out["actions"] = {
                "test_in_scenario_lab": {"endpoint": "POST /scenario/trees", "body": {"sku": sku, "location": location},
                                         "then": {"endpoint": "POST /scenario/trees/{id}/nodes",
                                                  "builder": {"type": "reorder", "sku": sku, "location": location, "qty": q}}},
                "override": "Resend with save=true and override_reason to record an Overridden decision",
            }
        if not save:
            return out
        # Saving records a decision: the planner, not the system, takes it
        if expected_input_hash != eng.sku_input_hash(sku):
            raise HITLError("Inputs changed since this draft was computed (or expected_input_hash missing). Review and retry.",
                            409, {"current_input_hash": eng.sku_input_hash(sku)})
        status = "Approved"
        if not in_range:
            override_reason = require_reason(override_reason, "save a draft PO outside the safe range")
            status = "Overridden"
        active = [d for d in svc.decisions() if d["status"] in planmod.ACTIVE and d["sku"] == sku and d["location"] == location]
        if active:
            raise HITLError(f"{sku} at {location} already has an approved decision ({active[0]['decision_id']}).", 409)
        opt = cand.evaluate(eng, sku, decision)
        rob = (opt.get("robustness") or {}).get("label")
        if rob == "Harmful":
            raise HITLError("This order is Harmful under the robustness grid and cannot be saved.", 409)
        if status == "Approved" and (opt["status"] == "Rejected" or rob == "Fragile"):
            override_reason = require_reason(override_reason, "save a draft PO that fails a check or is Fragile ("
                                             + (opt.get("rejection_reason") or "Fragile") + ")")
            status = "Overridden"
        options = (plan["results"].get((sku, location)) or {}).get("options", [])
        receipt = receipts.build(eng, sku, location, opt, options)
        receipt["draft_po"] = out["draft_po"]
        receipt["guardrail"] = out["guardrail"]
        dec = svc._record_decision(sku, location, None, options or [opt], opt, status, override_reason, receipt)
        out["decision"] = dec
        out["what_changed"] = svc.replan(f"draft PO {status.lower()} {dec['decision_id']}")
        return out
