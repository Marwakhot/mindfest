"""Portfolio plan: allocation, ranked queue, rejected options, KPIs, targeted questions (C1-C3, S8).

Spare stock at a source is allocated once across the whole plan, most urgent shortage
first; approved decisions lock their allocation before anything else is allocated.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from . import candidates as cand
from .engine import Engine, SimResult

ACTIVE = ("Approved", "Overridden")  # decisions that lock their allocation
PLAN_TYPES = ("transfer", "reorder", "bring_forward", "reduce_inbound", "delay_inbound", "discount")


def approved_by_sku(decisions: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for d in decisions:
        if d["status"] in ACTIVE and d["chosen"].get("type") in PLAN_TYPES:
            out.setdefault(d["sku"], []).append(d["chosen"])
    return out


def build(eng: Engine, decisions: List[Dict[str, Any]], with_questions: bool = True) -> Dict[str, Any]:
    st = eng.state
    approved = approved_by_sku(decisions)
    covered = {(d["sku"], d["location"]): d["decision_id"] for d in decisions if d["status"] in ACTIVE}
    cells: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for sku in sorted(st.skus):
        for loc in eng.locs:
            cells[(sku, loc)] = {"sku": sku, "location": loc, **cand.cell_status(eng, sku, loc)}
    remaining = {sku: {loc: eng.spare(sku, loc, approved.get(sku, [])) for loc in eng.locs} for sku in st.skus}
    spare_before = {sku: dict(v) for sku, v in remaining.items()}

    dests = [c for c, v in cells.items() if v["breach_day"] is not None and c not in covered]
    dests.sort(key=lambda c: (cells[c]["breach_day"], -cells[c]["projected_lost_sales_p50"], c))
    others = [c for c, v in cells.items() if c not in dests and c not in covered
              and any(f in v["flags"] for f in ("Excess", "Ageing", "Aged"))]

    results: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for c in dests + others:
        sku, loc = c
        res = cand.cell_options(eng, sku, loc, remaining[sku], cells[c], approved.get(sku, []))
        top = res["top"]
        if top and top["decision"]["type"] == "transfer":
            for leg in top["decision"]["legs"]:
                remaining[sku][leg["from"]] = max(0.0, remaining[sku][leg["from"]] - leg["qty"])
        results[c] = res

    queue, no_action, rejected = [], [], []
    for (sku, loc), res in results.items():
        cell = cells[(sku, loc)]
        entry = {"sku": sku, "name": st.skus[sku]["name"], "location": loc, "status": cell["status"],
                 "flags": cell["flags"], "trigger": res["trigger"], **res["urgency"],
                 "top": _brief(res["top"]) if res["top"] else None,
                 "alternatives": [_brief(o) for o in res["options"] if o is not res["top"]],
                 "input_hash": eng.sku_input_hash(sku)}
        cell["urgency"] = res["urgency"]["urgency"]
        if res["top"]:
            queue.append(entry)
        else:
            no_action.append(entry)
        for o in res["options"]:
            if o["status"] == "Rejected":
                rejected.append({"sku": sku, "location": loc, **_brief(o)})
    for (sku, loc), did in covered.items():
        cells[(sku, loc)]["approved_decision_id"] = did

    queue.sort(key=lambda e: (cand.URGENCY_RANK[e["urgency"]], -(e["top"].get("net_benefit_aed") or 0.0),
                              e["top"].get("breach_days_total", 0)))
    for i, e in enumerate(queue, 1):
        e["rank"] = i

    plan_decisions: Dict[str, List[Dict[str, Any]]] = {s: list(v) for s, v in approved.items()}
    for (sku, loc), res in results.items():
        if res["top"] and res["top"]["decision"]["type"] in PLAN_TYPES:
            plan_decisions.setdefault(sku, []).append(res["top"]["decision"])

    out = {
        "planning_day": str(st.day90), "horizon_days": eng.H,
        "kpis": kpis(eng, plan_decisions),
        "queue": queue, "no_action": no_action, "rejected": rejected,
        "cells": [_cell_public(v) for v in cells.values()],
        "spare_before_allocation": _round_nested(spare_before), "spare_after_allocation": _round_nested(remaining),
        "plan_decisions": plan_decisions,
        "results": results,
    }
    out["questions"] = targeted_questions(eng, decisions, results, cells, approved) if with_questions else []
    return out


def _round_nested(d):
    return {k: {kk: round(vv, 1) for kk, vv in v.items()} for k, v in d.items()}


def _cell_public(c: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in c.items()}


def _brief(o: Dict[str, Any]) -> Dict[str, Any]:
    keep = ("option_id", "label", "decision", "status", "net_benefit_aed", "failing_checks", "rejection_reason",
            "lead_days", "breach_days_total", "note", "kind", "robust_alternative")
    b = {k: o[k] for k in keep if k in o}
    if o.get("robustness"):
        b["robustness"] = o["robustness"]["label"]
        b["robustness_failing_cases"] = o["robustness"]["failing_cases"]
    if o.get("checks"):
        b["checks"] = {k: {"pass": v["pass"], **({"failures": v.get("failures")} if v.get("failures") else {})}
                       for k, v in o["checks"].items()}
        e = o["checks"]["economic"]
        b["economics"] = {k: e[k] for k in ("margin_protected_aed", "direct_cost_aed", "delta_holding_aed",
                                            "delta_ageing_aed", "lost_sales_avoided", "net_benefit_aed")}
    return b


# ---------------------------------------------------------------------- KPIs (C1)
def _agg(eng: Engine, sims: Dict[str, SimResult]) -> Dict[str, float]:
    tot = {"shortage_units": 0.0, "buffer_breach_days": 0, "excess_units": 0.0, "ageing_units": 0.0, "ageing_aed": 0.0,
           "aged_units": 0.0, "aged_aed": 0.0, "holding_cost_aed": 0.0, "ageing_cost_aed": 0.0,
           "transfer_cost_aed": 0.0, "expedite_cost_aed": 0.0}
    for sku, s in sims.items():
        tot["shortage_units"] += s.total("lost")
        tot["buffer_breach_days"] += int(sum(r.breach_days for r in s.locs.values()))
        tot["excess_units"] += s.total("excess_units")
        tot["ageing_units"] += s.total("ageing_units")
        tot["ageing_aed"] += s.total("ageing_value")
        tot["aged_units"] += s.total("aged_units")
        tot["aged_aed"] += s.total("aged_value")
        tot["holding_cost_aed"] += s.total("holding")
        tot["ageing_cost_aed"] += s.total("ageing_cost")
        tot["transfer_cost_aed"] += s.transfer_cost
        tot["expedite_cost_aed"] += s.expedite_cost
    return {k: round(v, 2) for k, v in tot.items()}


def kpis(eng: Engine, plan_decisions: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    before = {sku: eng.simulate(sku, []) for sku in eng.state.skus}
    after = {sku: eng.simulate(sku, plan_decisions.get(sku, [])) for sku in eng.state.skus}
    net = sum(eng.net(sku, after[sku], before[sku])["net_benefit_aed"] for sku in eng.state.skus)
    b, a = _agg(eng, before), _agg(eng, after)
    return {"basis": "P50 demand, likely delay, plan days 1-30", "before": b, "after": a,
            "net_benefit_aed": round(net, 2), "tag": "Estimate"}


# ---------------------------------------------------------------------- S8 targeted question
def targeted_questions(eng: Engine, decisions, results, cells, approved) -> List[Dict[str, Any]]:
    """One question per unconfirmed inbound order whose confirmation would change a top option."""
    st = eng.state
    out = []
    unconfirmed = [p for p in st.inbound if p["status"] != "Confirmed" and p["po_id"] not in st.include_unconfirmed]
    for po in unconfirmed:
        alt_state = st.copy()
        alt_state.include_unconfirmed = tuple(st.include_unconfirmed) + (po["po_id"],)
        alt = eng.derive(alt_state)
        effects = []
        for (sku, loc), res in results.items():
            if sku != po["sku"] or res["trigger"] is None:
                continue
            spare = {l: alt.spare(sku, l, approved.get(sku, [])) for l in alt.locs}
            status = cand.cell_status(alt, sku, loc)
            if status["breach_day"] is None and res["trigger"] == "shortage_or_breach":
                alt_top = "Do nothing (no breach once the PO arrives)"
            else:
                alt_res = cand.cell_options(alt, sku, loc, spare, status, approved.get(sku, []))
                alt_top = alt_res["top"]["label"] if alt_res["top"] else "No action passes the checks"
            cur_top = res["top"]["label"] if res["top"] else "No action passes the checks"
            if alt_top != cur_top:
                effects.append({"sku": sku, "location": loc, "if_unconfirmed": cur_top, "if_confirmed": alt_top,
                                "demand_signals": eng.fc[(sku, loc)].signal_ids})
        if effects:
            effects.sort(key=lambda x: (not x["demand_signals"], x["location"]))
            out.append({
                "question_id": f"Q-{po['po_id']}",
                "po_id": po["po_id"], "sku": po["sku"],
                "question": f"Is {po['po_id']} ({po['qty']} x {po['sku']} into {po['location']}, ETA {po['eta']}) confirmed?",
                "effects": effects,
                "how_to_answer": {"endpoint": "POST /corrections",
                                  "body": {"record_id": po["record_id"], "field": "status", "new_value": "Confirmed",
                                           "reason": "<who confirmed it and when>"}},
                "note": "Unconfirmed inbound is excluded from the plan until the planner confirms it (cautious default).",
            })
    return out


# ---------------------------------------------------------------------- What changed
def snapshot(plan: Dict[str, Any], eng: Engine) -> Dict[str, Any]:
    cells = {}
    for c in plan["cells"]:
        k = f"{c['sku']}|{c['location']}"
        cells[k] = {"p50_30d": round(eng.fc[(c["sku"], c["location"])].total("P50"), 1), "status": c["status"],
                    "lost_p50": c["projected_lost_sales_p50"], "top": None, "net": None, "qty": None}
    for e in plan["queue"]:
        k = f"{e['sku']}|{e['location']}"
        cells[k]["top"] = e["top"]["label"]
        cells[k]["net"] = e["top"].get("net_benefit_aed")
        d = e["top"]["decision"]
        cells[k]["qty"] = sum(l["qty"] for l in d["legs"]) if d["type"] == "transfer" else d.get("qty")
    return {"cells": cells, "kpis": plan["kpis"]}


def diff(before: Dict[str, Any], after: Dict[str, Any]) -> List[Dict[str, Any]]:
    changes = []
    if not before:
        return changes
    for k, a in after["cells"].items():
        b = before["cells"].get(k)
        if b is None:
            changes.append({"cell": k, "change": "new cell"})
            continue
        delta = {f: {"before": b[f], "after": a[f]} for f in a if b.get(f) != a[f]}
        if delta:
            sku, loc = k.split("|")
            changes.append({"sku": sku, "location": loc, "changes": delta})
    return changes
