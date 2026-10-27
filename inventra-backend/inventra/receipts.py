"""Decision Receipts (R2): one format everywhere (design doc section 5)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import candidates as cand
from .engine import Engine

FORMULAS = {
    "stock_path": "S_t(P, delta) = Free + sum(inbound arriving by day t at ETA + delta) - sum_{i<=t} d_i(P)",
    "spare": "Spare_src = max(0, min_{t<=H} S_t(P90, delta_max) - SafetyStock)",
    "need": "Need_dst = max(0, max_{L<=t<=H} (SafetyStock - S_t(P50, delta_likely))), rounded up to pack size",
    "split": "Q_total = min(Need_dst, sum Spare_src); sources by per-unit then fixed lane cost; legs rounded down to pack",
    "net": "Net = Margin x dLostSales - dHolding - dAgeing - DirectCost (P50 demand, likely delay)",
    "lead": "L_supplier = mean(past lead times) + delta_likely",
    "order_by": "OrderBy = Breach date (P50, likely delay) - L_supplier - review buffer",
    "reorder_qty": "Q_order = d(P50) x (L_supplier + R) + SafetyStock - (Free + Open inbound), rounded up to max(MOQ, pack), capped at max_cover_days of P50 cover on arrival",
    "rule": "Recommended only if Economic (Net > 0 at P50, likely delay), Safe (no new safety-stock breach at P90) and Feasible (reserved untouched, legs within spare, arrival inside horizon)",
    "robustness": "3 x 3 grid: demand P10/P50/P90 x delay min/likely/max, each case vs Do nothing in the same case",
    "urgency": "Slack = days until buffer breach (P50, likely) - lead days of fastest feasible fix",
}

REORDER_ASSUMPTION = ("Purchase cost is not counted as a loss because ordered units remain saleable stock; "
                      "over-ordering is penalised through holding cost, ageing cost and the max-cover cap.")


def _src(eng: Engine, rid: Optional[str]) -> Dict[str, Any]:
    lin = eng.state.lineage.get(rid or "", {})
    out = {"record_id": rid, "source_file": lin.get("source_file"), "row": lin.get("row"), "as_of": lin.get("as_of")}
    if rid in eng.state.corrected_fields:
        out["corrected_fields"] = eng.state.corrected_fields[rid]
    return out


def inputs(eng: Engine, sku: str, decisions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    st = eng.state
    rows: List[Dict[str, Any]] = []
    for loc in eng.locs:
        inv = st.inventory.get((sku, loc))
        if inv:
            src = _src(eng, inv["record_id"])
            rows.append({"label": f"{loc} available", "value": inv["available"], "unit": "units", "tag": "Data", **src})
            rows.append({"label": f"{loc} reserved (never moved)", "value": inv["reserved"], "unit": "units", "tag": "Data", **src})
            rows.append({"label": f"{loc} free", "value": st.free(sku, loc), "unit": "units", "tag": "Estimate",
                         "formula": "Available - Reserved", "inputs": [inv["record_id"]]})
        rows.append({"label": f"{loc} safety stock", "value": st.safety.get((sku, loc)), "unit": "units", "tag": "Data",
                     **_src(eng, f"safety_stock:{sku}|{loc}")})
        fc = eng.fc[(sku, loc)]
        for p in ("P10", "P50", "P90"):
            rows.append({"label": f"{loc} {p} demand, {eng.H} days", "value": round(fc.total(p), 1), "unit": "units",
                         "tag": "Estimate", "formula": "Section 7 forecast", "confidence": fc.confidence,
                         "signals": fc.signal_ids})
        rows.append({"label": f"{loc} spare", "value": round(eng.spare(sku, loc), 1), "unit": "units", "tag": "Estimate",
                     "formula": FORMULAS["spare"]})
    srow = st.skus[sku]
    rows.append({"label": "Margin per unit", "value": round(st.margin(sku), 2), "unit": "AED", "tag": "Data",
                 "formula": "unit_price - unit_cost", **_src(eng, f"sku_master:{sku}")})
    rows.append({"label": "Pack size", "value": srow["pack_size"], "unit": "units", "tag": "Data", **_src(eng, f"sku_master:{sku}")})
    rows.append({"label": "MOQ", "value": srow["moq"], "unit": "units", "tag": "Data", **_src(eng, f"sku_master:{sku}")})
    for a in eng.inbound(sku):
        po = next(p for p in st.inbound if p["po_id"] == a["po_id"])
        rows.append({"label": f"{po['po_id']} into {po['location']}", "value": po["qty"], "unit": "units", "tag": "Data",
                     "eta": str(po["eta"]), "signal_delay_days": a["delay"], "signals": a["signals"],
                     "delay_tag": "Assumption" if a["signals"] else None, **_src(eng, po["record_id"])})
    for po in st.inbound:
        if po["sku"] == sku and not eng.counts_inbound(po):
            rows.append({"label": f"{po['po_id']} into {po['location']} (Unconfirmed, excluded)", "value": po["qty"],
                         "unit": "units", "tag": "Unverified", **_src(eng, po["record_id"])})
    used_lanes = {(l["from"], l["to"]) for d in decisions if d["type"] == "transfer" for l in d["legs"]}
    for (a, b) in sorted(used_lanes):
        lane = eng.lane(a, b)
        rid = f"transfer_lanes:{a}|{b}"
        rows.append({"label": f"Lane {a} -> {b}", "value": {"fixed_cost_aed": lane["fixed_cost_aed"],
                     "cost_per_unit_aed": lane["cost_per_unit_aed"], "lead_days": lane["lead_days"]}, "tag": "Data", **_src(eng, rid)})
    for loc in eng.locs:
        rows.append({"label": f"Holding cost {loc}", "value": st.holding.get(loc), "unit": "AED per unit-day", "tag": "Data",
                     **_src(eng, f"holding_costs:{loc}")})
    for s in st.signals:
        if s.get("include") and (s["signal_id"] in sum((eng.fc[(sku, l)].signal_ids for l in eng.locs), [])
                                 or any(s["signal_id"] in a["signals"] for a in eng.inbound(sku))
                                 or s["signal_id"] in eng.supplier_lead(sku)[1]["signals"]):
            rows.append({"label": f"Signal {s['signal_id']} ({s['type']})", "value": {
                k: s.get(k) for k in ("delay_min", "delay_likely", "delay_max", "uplift_min", "uplift_likely", "uplift_max")
                if s.get(k) is not None}, "status": s["status"],
                "tag": "Data" if s["status"] == "Confirmed" else "Assumption", **_src(eng, s["record_id"])})
    for m in eng.state.params_meta:
        rows.append({"label": f"Parameter {m['name']}", "value": m["value"], "unit": m.get("unit"), "tag": "Assumption",
                     "changed_by": m.get("changed_by"), "changed_at": str(m.get("changed_at"))})
    return rows


def lead_breakdown(eng: Engine, d: Dict[str, Any]) -> Dict[str, Any]:
    t = d["type"]
    if t == "transfer":
        return {"legs": [{"from": l["from"], "to": l["to"], "lane_lead_days": int(eng.lane(l["from"], l["to"])["lead_days"]),
                          "dispatch_date": str(eng.state.plan_date(0)),
                          "arrival_date": str(eng.state.plan_date(int(eng.lane(l["from"], l["to"])["lead_days"])))}
                         for l in d["legs"]]}
    if t == "reorder":
        L, info = eng.supplier_lead(d["sku"])
        return {**info, "review_buffer_days": eng.p("review_buffer_days"), "order_day": d.get("order_day", 0),
                "order_date": str(eng.state.plan_date(int(d.get("order_day", 0)))),
                "arrival_date": str(eng.state.plan_date(int(d.get("order_day", 0)) + L))}
    if t in ("bring_forward", "delay_inbound", "reduce_inbound"):
        po = next(p for p in eng.state.inbound if p["po_id"] == d["po_id"])
        before = next((a for a in eng.inbound(d["sku"]) if a["po_id"] == d["po_id"]), None)
        after = next((a for a in eng.inbound(d["sku"], "likely", [d]) if a["po_id"] == d["po_id"]), None)
        return {"po_id": po["po_id"], "eta": str(po["eta"]),
                "signal_delay_days": before["delay"] if before else 0,
                "arrival_before": str(eng.state.plan_date(before["day"])) if before else None,
                "arrival_after": str(eng.state.plan_date(after["day"])) if after else None}
    return {}


def staff_instructions(eng: Engine, d: Dict[str, Any], picked: List[Dict[str, Any]] = ()) -> List[str]:
    st = eng.state
    sku = d.get("sku")
    name = st.skus[sku]["name"] if sku in st.skus else sku
    t = d["type"]
    out = []
    if t == "transfer":
        for leg in d["legs"]:
            lt = int(eng.lane(leg["from"], leg["to"])["lead_days"])
            batches = next((p["batches"] for p in picked if p["from"] == leg["from"] and p["to"] == leg["to"]), [])
            pick = (", picking batches oldest first: " + ", ".join(f"{b['batch_id']} ({b['units']:.0f})" for b in batches)) if batches else ""
            out.append(f"Move {leg['qty']} units of {sku} ({name}) from {leg['from']} to {leg['to']}: dispatch by "
                       f"{st.plan_date(0)}, arrive by {st.plan_date(lt)}{pick}. Do not touch reserved stock.")
    elif t == "reorder":
        L = eng.supplier_lead(sku)[0]
        out.append(f"Prepare a draft PO for {d['qty']} units of {sku} ({name}) to {d['location']} with "
                   f"{st.skus[sku]['supplier']}; order by {st.plan_date(int(d.get('order_day', 0)))}, expected arrival "
                   f"{st.plan_date(int(d.get('order_day', 0)) + L)}. Draft only: Inventra never sends purchase orders.")
    elif t == "bring_forward":
        out.append(f"Ask the supplier to bring {d['po_id']} forward by {d['days']} days (expedite cost from the PO record).")
    elif t == "reduce_inbound":
        out.append(f"Ask the supplier to reduce {d['po_id']} to {d['new_qty']} units.")
    elif t == "delay_inbound":
        out.append(f"Ask the supplier to delay {d['po_id']} by {d['days']} days.")
    elif t == "discount":
        out.append(f"Mark down the {d['units']} oldest units of {sku} ({name}) at {d['location']} by {d['depth_pct']:g}%, "
                   f"picking oldest batches first. The {d['uplift_pct']:g}% sell-through uplift is an assumption, not data.")
    elif t == "flag_clearance":
        out.append(f"Flag {sku} ({name}) at {d['location']} for clearance review (no pricing model in Inventra).")
    return out


def constraints(chosen: Dict[str, Any]) -> Dict[str, Any]:
    chk = chosen.get("checks") or {}
    return {
        "safety_stock": chk.get("safe", {"pass": None}),
        "reserved_stock": {"pass": not any("reserved" in f for f in (chk.get("feasible") or {}).get("failures", [])),
                           "rule": "Reserved stock is never moved"},
        "availability": chk.get("feasible", {"pass": None}),
        "lead_time": {"pass": "arrives after horizon" not in (chk.get("feasible") or {}).get("labels", []),
                      "rule": "Arrival inside the horizon"},
        "economic": {k: v for k, v in (chk.get("economic") or {}).items()},
    }


def build(eng: Engine, sku: str, loc: str, chosen: Dict[str, Any], options: List[Dict[str, Any]],
          path: List[Dict[str, Any]] = (), tree_assumptions: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    d = chosen["decision"]
    others = []
    for o in options:
        if o.get("option_id") == chosen.get("option_id"):
            continue
        why = o.get("rejection_reason")
        if not why:
            if o.get("status") in ("Alternative", "Passes"):
                why = (f"ranked lower: net {o.get('net_benefit_aed')} AED vs {chosen.get('net_benefit_aed')} AED "
                       f"for the chosen option")
            else:
                why = o.get("note") or o.get("status")
        others.append({"label": o["label"], "status": o.get("status"), "net_benefit_aed": o.get("net_benefit_aed"),
                       "failing_checks": o.get("failing_checks"), "why": why,
                       "robustness": (o.get("robustness") or {}).get("label") if isinstance(o.get("robustness"), dict) else o.get("robustness")})
    rob = chosen.get("robustness") or {}
    formulas = [FORMULAS["stock_path"], FORMULAS["net"], FORMULAS["rule"], FORMULAS["robustness"], FORMULAS["urgency"]]
    if d["type"] == "transfer":
        formulas += [FORMULAS["spare"], FORMULAS["need"]] + ([FORMULAS["split"]] if d.get("variant") == "split" else [])
    if d["type"] == "reorder":
        formulas += [FORMULAS["lead"], FORMULAS["order_by"], FORMULAS["reorder_qty"]]
    if d["type"] == "discount":
        formulas += ["Discount: demand x (1 + assumed uplift) while discounted units remain; "
                     "margin given up = discounted units sold x unit price x depth"]
    receipt = {
        "sku": sku, "sku_name": eng.state.skus[sku]["name"], "location": loc, "planning_day": str(eng.state.day90),
        "chosen": {"label": chosen["label"], "decision": d, "net_benefit_aed": chosen.get("net_benefit_aed"),
                   "status": chosen.get("status")},
        "path": [{"label": cand.label(eng, x), "decision": x} for x in path] if path else None,
        "rejected_options": others,
        "inputs": inputs(eng, sku, list(path) or [d]),
        "constraints_checked": constraints(chosen),
        "formulas": formulas,
        "lead_time_breakdown": lead_breakdown(eng, d),
        "robustness": {"label": rob.get("label"), "failing_cases": rob.get("failing_cases")} if rob else None,
        "staff_instructions": [s for x in (list(path) or [d]) for s in staff_instructions(eng, x, chosen.get("picked_batches") or [])],
        "assumptions": ([REORDER_ASSUMPTION] if d["type"] == "reorder" else [])
        + ([f"Assumed sell-through uplift of {d['uplift_pct']:g}% while discounted (no price-elasticity data)"]
           if d["type"] == "discount" else []),
        "input_hash": eng.sku_input_hash(sku),
        "draft_only": True,
    }
    if tree_assumptions:
        receipt["tested_under_tree_assumptions"] = tree_assumptions
    return receipt
