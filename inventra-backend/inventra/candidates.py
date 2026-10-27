"""Option generation and evaluation (design doc sections 6.2, 6.3, 6.5 - 6.7)."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .engine import EPS, LOST_EPS, Engine, ceil_pack, floor_pack
from .state import stable_hash

URGENCY_RANK = {"Critical": 0, "Soon": 1, "OK": 2}


# ---------------------------------------------------------------------- labels
def label(eng: Engine, d: Dict[str, Any]) -> str:
    t = d["type"]
    if t == "transfer":
        legs = d["legs"]
        if d.get("variant") == "split":
            return "Split: " + " + ".join(f"{l['from']} {l['qty']}" for l in legs) + f" -> {legs[0]['to']}"
        prefix = "Full-need transfer" if d.get("variant") == "naive" else "Transfer"
        return f"{prefix} {legs[0]['qty']} {legs[0]['from']} -> {legs[0]['to']}"
    if t == "reorder":
        return f"Reorder {d['qty']} to {d['location']} (order by {eng.state.plan_date(int(d.get('order_day', 0)))})"
    if t == "bring_forward":
        return f"Bring forward {d['po_id']} by {d['days']} days"
    if t == "reduce_inbound":
        return f"Reduce {d['po_id']} to {d['new_qty']} units"
    if t == "delay_inbound":
        return f"Delay {d['po_id']} by {d['days']} days"
    if t == "wait":
        return f"Wait for {d['po_id']}"
    if t == "flag_clearance":
        return f"Flag {d['sku']} at {d['location']} for clearance"
    if t == "discount":
        return f"Discount {d['units']} oldest units at {d['location']} by {d['depth_pct']:g}%"
    return "Do nothing"


def option_id(d: Dict[str, Any]) -> str:
    return "OPT-" + stable_hash(d)[:10]


def lead_of(eng: Engine, d: Dict[str, Any]) -> Optional[int]:
    t = d["type"]
    if t == "transfer":
        return max(int(eng.lane(l["from"], l["to"])["lead_days"]) for l in d["legs"])
    if t == "reorder":
        return eng.supplier_lead(d["sku"])[0] + int(d.get("order_day", 0))
    if t == "bring_forward":
        a = next((x for x in eng.inbound(d["sku"], "likely", [d]) if x["po_id"] == d["po_id"]), None)
        return a["day"] if a else None
    if t == "discount":
        return 1
    return None


# ---------------------------------------------------------------------- cell status (N1)
def cell_status(eng: Engine, sku: str, loc: str) -> Dict[str, Any]:
    base = eng.simulate(sku, [], "P50", "likely").locs[loc]
    st = eng.state
    flags = []
    if base.lost > LOST_EPS:
        flags.append("Short")
    elif base.below_ss_days > 0:
        flags.append("Buffer breach")
    b = st.batches
    rows = b[(b["sku"] == sku) & (b["location"] == loc)] if not b.empty else b
    aged = rows[rows["age_days"] >= eng.p("aged_days")] if len(rows) else rows
    ageing = rows[(rows["age_days"] >= eng.p("ageing_days")) & (rows["age_days"] < eng.p("aged_days"))] if len(rows) else rows
    if len(aged):
        flags.append("Aged")
    if len(ageing):
        flags.append("Ageing")
    inbound_h = sum(a["qty"] for a in eng.inbound(sku) if a["loc"] == loc and a["day"] <= eng.H)
    d50 = eng.d50(sku, loc)
    excess = max(0.0, st.free(sku, loc) + inbound_h - eng.p("max_cover_days") * d50)
    if excess > EPS:
        flags.append("Excess")
    primary = flags[0] if flags else "Healthy"
    cost = st.skus[sku]["unit_cost"]
    return {
        "status": primary, "flags": flags or ["Healthy"],
        "excess_units": round(excess, 1),
        "cover_days": round(st.free(sku, loc) / d50, 1) if d50 > 0 else None,
        "aged_units": int(aged["units"].sum()) if len(aged) else 0,
        "aged_value_aed": round(float(aged["units"].sum()) * cost, 2) if len(aged) else 0.0,
        "ageing_units": int(ageing["units"].sum()) if len(ageing) else 0,
        "ageing_value_aed": round(float(ageing["units"].sum()) * cost, 2) if len(ageing) else 0.0,
        "projected_lost_sales_p50": round(base.lost, 1),
        "days_below_safety_p50": base.below_ss_days,
        "breach_day": eng.breach_day(sku, loc),
    }


# ---------------------------------------------------------------------- 6.3 reorder
def reorder_plan(eng: Engine, sku: str, loc: str, pct_need: str = "P50") -> Optional[Dict[str, Any]]:
    st = eng.state
    srow = st.skus[sku]
    L, lead_info = eng.supplier_lead(sku)
    R, buf = eng.p("review_period_days"), eng.p("review_buffer_days")
    bday = eng.breach_day(sku, loc)
    if bday is None:
        return None
    d50 = eng.d50(sku, loc)
    d_need = float(eng.demand(sku, loc, pct_need).mean())
    order_by = bday - L - buf
    order_day = max(0, order_by)
    open_in = sum(a["qty"] for a in eng.inbound(sku) if a["loc"] == loc)
    free = st.free(sku, loc)
    ss = st.safety.get((sku, loc), 0)
    raw_q = d_need * (L + R) + ss - (free + open_in)
    if raw_q <= 0:
        return None
    q = max(srow["moq"], ceil_pack(raw_q, srow["pack_size"]))
    arrival = order_day + L
    path = eng.stock_path(sku, loc, "P50", "likely")
    s_arr = max(0.0, float(path[min(arrival, eng.H) - 1])) if arrival >= 1 else float(free)
    cap = eng.p("max_cover_days") * d50 - s_arr
    capped = moq_forced_excess = False
    if q > cap:
        if srow["moq"] > cap:
            moq_forced_excess = True
            q = max(srow["moq"], min(q, ceil_pack(srow["moq"], srow["pack_size"])))
        else:
            q = max(srow["moq"], floor_pack(cap, srow["pack_size"]))
            capped = True
    lost_before = 0.0
    if order_by < 0:
        base = eng.simulate(sku, [], "P50", "likely").locs[loc]
        lost_before = float(sum(base.lost_daily[: max(0, min(arrival, eng.H) - 1)]))
    return {
        "decision": {"type": "reorder", "sku": sku, "location": loc, "qty": int(q), "order_day": int(order_day)},
        "lead": lead_info, "review_period_days": R, "review_buffer_days": buf,
        "breach_day": bday, "breach_date": str(st.plan_date(bday)), "order_by_day": order_by,
        "order_by_date": str(st.plan_date(order_by)), "arrival_day": arrival, "arrival_date": str(st.plan_date(arrival)),
        "d50_daily": round(d50, 3), "raw_qty": round(raw_q, 1), "open_inbound": open_in, "free": free, "safety_stock": ss,
        "stock_on_arrival_p50": round(s_arr, 1), "max_cover_cap": round(cap, 1), "capped": capped,
        "moq_forced_excess": moq_forced_excess,
        "extrapolated": (L + R) > eng.H,
        "extrapolated_note": "Days beyond the 30-day forecast use the P50 daily rate: Extrapolated, low confidence" if (L + R) > eng.H else None,
        "order_now_note": (f"order now; arrives after the breach ({lost_before:.0f} units lost before arrival at P50)"
                           if order_by < 0 else None),
        "lost_before_arrival": round(lost_before, 1),
        "formula": "Q = d(P50) x (L_supplier + R) + SafetyStock - (Free + Open inbound), rounded up to max(MOQ, pack); "
                   "capped at max_cover_days of P50 cover on arrival",
    }


# ---------------------------------------------------------------------- 6.2 candidates
def destination_candidates(eng: Engine, sku: str, dst: str, spare: Dict[str, float]) -> List[Dict[str, Any]]:
    st = eng.state
    pack = st.skus[sku]["pack_size"]
    out: List[Dict[str, Any]] = []
    sources = [l for l in eng.locs if l != dst]
    needs = {}
    for src in sources:
        L = int(eng.lane(src, dst)["lead_days"])
        needs[src] = eng.need(sku, dst, L)[0]
    singles = []
    for src in sources:
        sp = floor_pack(spare.get(src, 0.0), pack)
        need = needs[src]
        if need <= 0 or sp <= 0:
            continue
        q = min(need, sp)
        singles.append(src)
        out.append({"type": "transfer", "variant": "single", "sku": sku, "legs": [{"from": src, "to": dst, "qty": int(q)}]})
    # split: only when no single source can cover the need
    max_need = max(needs.values()) if needs else 0
    covered = any(floor_pack(spare.get(s, 0.0), pack) >= needs[s] for s in singles)
    if max_need > 0 and not covered and len(singles) >= 2:
        order = sorted(singles, key=lambda s: (eng.lane(s, dst)["cost_per_unit_aed"], eng.lane(s, dst)["fixed_cost_aed"]))
        remaining = max_need
        legs = []
        for s in order:
            q = floor_pack(min(remaining, spare.get(s, 0.0)), pack)
            if q > 0:
                legs.append({"from": s, "to": dst, "qty": int(q)})
                remaining -= q
            if remaining <= 0:
                break
        if len(legs) >= 2:
            out.append({"type": "transfer", "variant": "split", "sku": sku, "legs": legs})
    # naive full-need transfer from the largest source (uncapped, so the engine shows why it fails)
    if sources and max_need > 0:
        big = max(sources, key=lambda s: st.free(sku, s))
        need = needs[big]
        if need > floor_pack(spare.get(big, 0.0), pack) and st.free(sku, big) > 0:
            out.append({"type": "transfer", "variant": "naive", "sku": sku, "legs": [{"from": big, "to": dst, "qty": int(need)}]})
    ro = reorder_plan(eng, sku, dst)
    if ro:
        out.append(ro["decision"])
    for a in eng.inbound(sku):
        po = next(p for p in st.inbound if p["po_id"] == a["po_id"])
        if a["loc"] != dst:
            continue
        if po.get("changeable") and po.get("expedite_days") and a["day"] > 1:
            out.append({"type": "bring_forward", "sku": sku, "po_id": po["po_id"], "days": int(po["expedite_days"])})
        out.append({"type": "wait", "sku": sku, "location": dst, "po_id": po["po_id"]})
    return out


def excess_candidates(eng: Engine, sku: str, loc: str, status: Dict[str, Any]) -> List[Dict[str, Any]]:
    st = eng.state
    pack = st.skus[sku]["pack_size"]
    out: List[Dict[str, Any]] = []
    if "Excess" in status["flags"]:
        d50 = eng.d50(sku, loc)
        for a in eng.inbound(sku):
            po = next(p for p in st.inbound if p["po_id"] == a["po_id"])
            if a["loc"] != loc or not po.get("changeable") or a["day"] > eng.H:
                continue
            path = eng.stock_path(sku, loc, "P50", "likely")
            on_arrival = float(path[a["day"] - 1])
            over = on_arrival - eng.p("max_cover_days") * d50
            if over > 0:
                cut = min(a["qty"], ceil_pack(over, pack))
                out.append({"type": "reduce_inbound", "sku": sku, "po_id": po["po_id"], "new_qty": int(a["qty"] - cut)})
            # delay until the units are needed: first day free stock (without this PO) falls to safety stock
            without = eng.stock_path(sku, loc, "P50", "likely", [{"type": "reduce_inbound", "sku": sku, "po_id": po["po_id"], "new_qty": 0}])
            ss = st.safety.get((sku, loc), 0)
            idx = np.where(without < ss)[0]
            need_day = int(idx[0]) + 1 if len(idx) else int(math.ceil((st.free(sku, loc) - ss) / d50)) if d50 > 0 else eng.H + 1
            days = max(0, need_day - a["day"])
            if days > 0:
                out.append({"type": "delay_inbound", "sku": sku, "po_id": po["po_id"], "days": int(days),
                            "extrapolated": not len(idx)})
    if "Aged" in status["flags"] or "Ageing" in status["flags"]:
        out.append({"type": "flag_clearance", "sku": sku, "location": loc})
    return out


# ---------------------------------------------------------------------- evaluation
def evaluate(eng: Engine, sku: str, d: Dict[str, Any], spare: Optional[Dict[str, float]] = None,
             base_decisions: List[Dict[str, Any]] = (), step_down: bool = True) -> Dict[str, Any]:
    opt = {"option_id": option_id(d), "label": label(eng, d), "decision": d, "lead_days": lead_of(eng, d)}
    if d["type"] == "flag_clearance":
        opt.update({"status": "Advisory", "kind": "advisory", "net_benefit_aed": None,
                    "note": "Flag for clearance: no pricing model, net benefit not computed",
                    "robustness": None, "checks": None, "failing_checks": []})
        return opt
    if d["type"] in ("wait", "do_nothing"):
        sim = eng.simulate(sku, base_decisions)
        opt.update({"status": "Baseline", "kind": "baseline", "net_benefit_aed": 0.0, "robustness": None,
                    "checks": None, "failing_checks": [], "sim": sim.summary(),
                    "note": "Reference: same outcome as Do nothing"})
        return opt
    chk = eng.check(sku, [d], list(base_decisions), spare_ref=spare)
    rob = eng.robustness(sku, [d], list(base_decisions))
    failing = list(chk["failing_checks"])
    if rob["label"] == "Harmful":
        failing.append("Harmful")
    opt.update({
        "checks": {k: chk[k] for k in ("economic", "safe", "feasible")},
        "net_benefit_aed": chk["economic"]["net_benefit_aed"],
        "robustness": rob, "failing_checks": failing,
        "status": "Rejected" if failing else "Passes",
        "sim": chk["sim"], "base_sim": chk["base_sim"], "picked_batches": chk["picked_batches"], "notes": chk["notes"],
        "breach_days_total": sum(chk["sim"]["below_safety_days"].values()),
    })
    if failing:
        opt["rejection_reason"] = rejection_text(chk, rob)
    if d["type"] == "transfer" and rob["label"] == "Fragile" and step_down and not failing:
        alt = robust_step_down(eng, sku, d, spare, base_decisions)
        if alt:
            opt["robust_alternative"] = alt
    return opt


def rejection_text(chk: Dict[str, Any], rob: Dict[str, Any]) -> str:
    parts = []
    if not chk["economic"]["pass"]:
        e = chk["economic"]
        parts.append(f"Economic: margin protected {e['margin_protected_aed']:.0f} AED vs direct cost "
                     f"{e['direct_cost_aed']:.0f} AED (net {e['net_benefit_aed']:.0f} AED)")
    if not chk["safe"]["pass"]:
        parts.append("Safe: " + "; ".join(chk["safe"]["failures"]))
    if not chk["feasible"]["pass"]:
        parts.append("Feasible: " + "; ".join(chk["feasible"]["failures"]))
    if rob["label"] == "Harmful":
        parts.append("Harmful: " + "; ".join(rob["failing_cases"]))
    return " | ".join(parts)


def robust_step_down(eng: Engine, sku: str, d: Dict[str, Any], spare, base_decisions) -> Optional[Dict[str, Any]]:
    """Largest Robust quantity, stepping the most expensive leg down by pack size (6.6)."""
    pack = eng.state.skus[sku]["pack_size"]
    legs = [dict(l) for l in d["legs"]]
    order = sorted(range(len(legs)), key=lambda i: -eng.lane(legs[i]["from"], legs[i]["to"])["cost_per_unit_aed"])
    for i in order:
        while legs[i]["qty"] - pack > 0:
            legs[i]["qty"] -= pack
            cand = dict(d, legs=[l for l in legs if l["qty"] > 0])
            if eng.robustness(sku, [cand], list(base_decisions))["label"] == "Robust":
                return {"decision": cand, "label": label(eng, cand), "robustness": "Robust"}
    return None


def rank_key(o: Dict[str, Any]) -> Tuple:
    return (0 if o["status"] == "Passes" else 1, -(o.get("net_benefit_aed") or 0.0), o.get("breach_days_total", 0))


def urgency(eng: Engine, sku: str, loc: str, options: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Slack = days until buffer breach (P50, likely) - lead days of the fastest feasible fix (6.7)."""
    bday = eng.breach_day(sku, loc)
    if bday is None:
        return {"urgency": "OK", "slack_days": None, "breach_day": None, "fastest_fix_lead_days": None}
    leads = [o["lead_days"] for o in options if o.get("lead_days") is not None and o.get("checks")
             and o["checks"]["feasible"]["pass"]]
    lead = min(leads) if leads else eng.supplier_lead(sku)[0]
    slack = bday - lead
    soon = eng.p("urgency_soon_days")
    u = "Critical" if slack < 0 else ("Soon" if slack <= soon else "OK")
    return {"urgency": u, "slack_days": slack, "breach_day": bday, "breach_date": str(eng.state.plan_date(bday)),
            "fastest_fix_lead_days": lead}


def cell_options(eng: Engine, sku: str, loc: str, spare: Dict[str, float], status: Dict[str, Any],
                 base_decisions: List[Dict[str, Any]] = ()) -> Dict[str, Any]:
    decisions: List[Dict[str, Any]] = []
    trigger = None
    if status["breach_day"] is not None:
        trigger = "shortage_or_breach"
        decisions = destination_candidates(eng, sku, loc, spare)
    elif any(f in status["flags"] for f in ("Excess", "Ageing", "Aged")):
        trigger = "excess_or_ageing"
        decisions = excess_candidates(eng, sku, loc, status)
    seen, opts = set(), []
    for d in decisions:
        oid = option_id(d)
        if oid in seen:
            continue
        seen.add(oid)
        opts.append(evaluate(eng, sku, d, spare, list(base_decisions)))
    opts.sort(key=rank_key)
    passing = [o for o in opts if o["status"] == "Passes"]
    advisory = [o for o in opts if o["status"] == "Advisory"]
    top = passing[0] if passing else (advisory[0] if advisory else None)
    for o in opts:
        if o is top:
            o["status"] = "Recommended" if o["status"] == "Passes" else o["status"]
        elif o["status"] == "Passes":
            o["status"] = "Alternative"
    urg = urgency(eng, sku, loc, opts) if trigger == "shortage_or_breach" else {"urgency": "OK", "slack_days": None}
    return {"trigger": trigger, "options": opts, "top": top, "urgency": urg}
