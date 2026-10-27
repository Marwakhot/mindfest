"""Evaluation replay (E1-E4, design doc section 11).

This is the ONLY module that reads the judges' answer key. The plan is built on day 90
from visible data before the answer key is opened, and nothing read here flows back into
planning; the API exposes only aggregate results and the checklist.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List

import numpy as np
import pandas as pd

from . import candidates as cand
from . import plan as planmod
from . import signals as sigmod
from .config import settings
from .engine import Engine, ceil_pack
from .service import Service
from .state import build_state
from .validation import report as validation_report


def _key(name: str) -> pd.DataFrame:
    path = settings.answer_key_dir / f"{name}.csv"
    if not path.exists():
        raise FileNotFoundError(f"answer key file {path.name} not found")
    return pd.read_csv(path)


class TrueWorld(Engine):
    """Engine whose demand, inbound arrivals and supplier delays are the realised truth."""

    def __init__(self, planning: Engine, true_demand: pd.DataFrame, inbound_out: pd.DataFrame,
                 signal_out: pd.DataFrame, true_stock: pd.DataFrame):
        st = planning.state.copy()
        for _, r in true_stock.iterrows():
            inv = st.inventory.get((r["sku"], r["location"]))
            if inv:
                inv["available"], inv["reserved"] = int(r["true_available"]), int(r["reserved"])
        super().__init__(st, {"cells": planning.fc, "meta": planning.fc_meta})
        self.true = {}
        for (s, l), g in true_demand.groupby(["sku", "location"]):
            arr = np.zeros(self.H)
            for t, v in zip(g["plan_day"], g["true_demand"]):
                if 1 <= t <= self.H:
                    arr[int(t) - 1] = v
            self.true[(s, l)] = arr
        self.inbound_out = {r["po_id"]: r for _, r in inbound_out.iterrows()}
        self.realised = {r["signal_id"]: r for _, r in signal_out.iterrows()}

    def demand(self, sku, loc, pct="P50", level="likely", scale=1.0):
        return self.true.get((sku, loc), np.zeros(self.H)) * scale

    def inbound(self, sku, level="likely", decisions=()):
        mods = {}
        for d in decisions:
            if d["type"] in ("bring_forward", "reduce_inbound", "delay_inbound") and d.get("sku") == sku:
                mods.setdefault(d["po_id"], {})[d["type"]] = d
        out = []
        for po in self.state.inbound:
            o = self.inbound_out.get(po["po_id"])
            if po["sku"] != sku or o is None or not bool(o["arrives"]):
                continue
            day, qty = int(o["realised_eta_plan_day"]), int(po["qty"])
            m = mods.get(po["po_id"], {})
            if "bring_forward" in m:
                day -= int(m["bring_forward"]["days"])
            if "delay_inbound" in m:
                day += int(m["delay_inbound"]["days"])
            if "reduce_inbound" in m:
                qty = int(m["reduce_inbound"]["new_qty"])
            out.append({"po_id": po["po_id"], "loc": po["location"], "day": max(1, day), "qty": qty, "delay": 0,
                        "signals": [], "status": "realised"})
        return out

    def supplier_lead(self, sku, level="likely"):
        sup = self.state.skus[sku]["supplier"]
        mean = self.supplier_mean_lead(sup)
        delay = 0
        for sig in self.state.signals:
            if sig["type"] == "supply" and sigmod.component_match(sig, self.state.skus[sku]):
                r = self.realised.get(sig["signal_id"])
                if r is not None and bool(r["happened"]) and not pd.isna(r["realised_delay_days"]):
                    delay = max(delay, int(r["realised_delay_days"]))
        lead = int(math.ceil(mean - 1e-9)) + delay
        return lead, {"supplier": sup, "mean_past_lead_days": round(mean, 2), "signal_delay_days": delay, "lead_days": lead}


def _baseline_decisions(world: TrueWorld, planning: Engine, sku: str) -> List[Dict[str, Any]]:
    """Reorder when free stock reaches safety stock; qty = baseline_reorder_days of average observed sales."""
    st = planning.state
    W = st.params["base_rate_window_days"]
    srow = st.skus[sku]
    decisions: List[Dict[str, Any]] = []
    for loc in world.locs:
        h = planning.fc[(sku, loc)].history
        lo = st.history_days - W
        avg = float(np.nanmean(h.observed[lo:])) if (~h.missing[lo:]).any() else 0.0
        q = max(srow["moq"], ceil_pack(st.params["baseline_reorder_days"] * avg, srow["pack_size"]))
        lead = world.supplier_lead(sku)[0]
        ss = st.safety.get((sku, loc), 0)
        start = 1
        for _ in range(5):
            sim = world.simulate(sku, decisions).locs[loc]
            trig = next((t for t in range(start, world.H + 1) if sim.end_daily[t - 1] <= ss), None)
            if trig is None:
                break
            decisions.append({"type": "reorder", "sku": sku, "location": loc, "qty": int(q), "order_day": int(trig)})
            start = trig + lead + 1  # one open order at a time
            if start > world.H:
                break
    return decisions


def _forecast_metrics(planning: Engine, true: pd.DataFrame) -> Dict[str, Any]:
    st = planning.state
    W = st.params["base_rate_window_days"]
    ae = {"inventra": 0.0, "baseline": 0.0}
    se = {"inventra": 0.0, "baseline": 0.0}
    tot = 0.0
    inside = cells = 0
    for (s, l), fc in planning.fc.items():
        g = true[(true["sku"] == s) & (true["location"] == l)].sort_values("plan_day")
        a = g["true_demand"].to_numpy(dtype=float)[: planning.H]
        f = fc.daily("P50")[: len(a)]
        h = fc.history
        lo = st.history_days - W
        b = float(np.nanmean(h.observed[lo:])) if (~h.missing[lo:]).any() else 0.0
        ae["inventra"] += float(np.abs(f - a).sum())
        se["inventra"] += float((f - a).sum())
        ae["baseline"] += float(np.abs(b - a).sum())
        se["baseline"] += float((b - a).sum())
        tot += float(a.sum())
        cells += 1
        inside += fc.total("P10") <= a.sum() <= fc.total("P90")
    return {"wape": {k: round(v / tot, 4) for k, v in ae.items()},
            "bias": {k: round(v / tot, 4) for k, v in se.items()},
            "band_calibration": round(inside / cells, 3), "calibration_target": 0.8}


def run(svc: Service) -> Dict[str, Any]:
    eng, _ = svc.ensure()
    # Plan on day 90 from visible data only (corrections applied), with every recommended option auto-approved
    planned = planmod.build(eng, [], with_questions=False)
    decisions = planned["plan_decisions"]
    raw_eng = Engine(build_state(svc.store, apply_corrections=False))
    # Only now is the answer key opened
    true_demand = _key("true_demand")
    world = TrueWorld(eng, true_demand, _key("inbound_outcomes"), _key("signal_outcomes"), _key("true_stock_day90"))
    arms = {"inventra": {}, "baseline": {}, "no_action": {}}
    breaches: List[Dict[str, Any]] = []
    for sku in eng.state.skus:
        mine = decisions.get(sku, [])
        arms["inventra"][sku] = world.simulate(sku, mine)
        arms["baseline"][sku] = world.simulate(sku, _baseline_decisions(world, raw_eng, sku))
        arms["no_action"][sku] = world.simulate(sku, [])
        for d in mine:
            if d["type"] == "transfer":
                for leg in d["legs"]:
                    true_free = world.state.free(sku, leg["from"])
                    if leg["qty"] > true_free:
                        breaches.append({"type": "reserved_or_unavailable_stock_moved", "sku": sku, "leg": leg,
                                         "true_free": true_free})
                    if int(world.lane(leg["from"], leg["to"])["lead_days"]) > world.H:
                        breaches.append({"type": "infeasible_lead_time", "sku": sku, "leg": leg})
                    src_a = arms["inventra"][sku].locs[leg["from"]]
                    src_n = arms["no_action"][sku].locs[leg["from"]]
                    if src_a.below_ss_days > 0 and src_n.below_ss_days == 0:
                        breaches.append({"type": "new_safety_stock_breach", "sku": sku, "location": leg["from"],
                                         "days_below": src_a.below_ss_days})
            elif d["type"] == "reorder" and eng.supplier_lead(sku)[0] + int(d.get("order_day", 0)) > eng.H:
                breaches.append({"type": "infeasible_lead_time", "sku": sku, "decision": d})

    def metrics(arm: str) -> Dict[str, Any]:
        sims = arms[arm]
        margin_earned = sum(s.total("sold") * eng.state.margin(k) for k, s in sims.items())
        costs = sum(s.transfer_cost + s.expedite_cost + s.total("holding") + s.total("ageing_cost") for s in sims.values())
        return {"lost_sales": round(sum(s.total("lost") for s in sims.values()), 1),
                "buffer_breach_days": int(sum(r.below_ss_days for s in sims.values() for r in s.locs.values())),
                "excess_units_day120": round(sum(s.total("excess_units") for s in sims.values()), 1),
                "aged_stock_value_day120_aed": round(sum(s.total("aged_value") for s in sims.values()), 2),
                "margin_earned_aed": round(margin_earned, 2),
                "transfer_cost_aed": round(sum(s.transfer_cost for s in sims.values()), 2),
                "expedite_cost_aed": round(sum(s.expedite_cost for s in sims.values()), 2),
                "holding_cost_aed": round(sum(s.total("holding") for s in sims.values()), 2),
                "ageing_cost_aed": round(sum(s.total("ageing_cost") for s in sims.values()), 2),
                "net_economic_aed": round(margin_earned - costs, 2)}

    m_i, m_b = metrics("inventra"), metrics("baseline")
    llm = svc.store.query("SELECT COUNT(*) AS n, SUM(CASE WHEN first_pass THEN 1 ELSE 0 END) AS ok FROM llm_log WHERE kind <> 'parse'")[0]
    result = {
        "replay": "Plan on day 90 (plan days 0), reveal days 91-120 from the answer key",
        "forecast": _forecast_metrics(eng, true_demand),
        "inventra": m_i, "baseline": m_b,
        "net_benefit_vs_baseline_aed": round(m_i["net_economic_aed"] - m_b["net_economic_aed"], 2),
        "constraint_breaches": {"count": len(breaches), "items": breaches, "target": 0},
        "explanation_verification": (round(llm["ok"] / llm["n"], 3) if llm["n"] else None),
        "explanation_note": None if llm["n"] else "No LLM explanations generated (keyless mode)",
        "verdict": "failure: constraint breach" if breaches else "pass",
        "auto_approved_options": sum(len(v) for v in decisions.values()),
        "checklist": checklist(svc, eng, planned),
    }
    svc.audit("evaluation_replay", "answer_key", None, {"verdict": result["verdict"], "breaches": len(breaches)}, user="system")
    svc.last_evaluation = result
    return result


# ---------------------------------------------------------------------- E4 answer-key checklist
def checklist(svc: Service, eng: Engine, planned: Dict[str, Any]) -> List[Dict[str, Any]]:
    path = settings.judges_dir / "evaluation_checklist_v1.csv"
    cases = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=["case_id", "description", "expected_action"])
    results = {(e["sku"], e["location"]): e for e in planned["queue"] + planned["no_action"]}
    sig_ids = {s["signal_id"] for s in eng.state.signals}

    def top(sku, loc):
        e = results.get((sku, loc))
        return e["top"] if e and e.get("top") else None

    out = {}

    # 1 CHG-01: Dubai-only before SIG-001; split after; full Dubai rejected (on the uncorrected case-12 record)
    raw = build_state(svc.store, apply_corrections=False)
    if "SIG-001" in sig_ids:
        before_st = raw.copy()
        for s in before_st.signals:
            if s["signal_id"] == "SIG-001":
                s["include"] = False
        e_b, e_a = Engine(before_st), Engine(raw)
        tb = _cell_top(e_b, "CHG-01", "Abu Dhabi")
        ta = _cell_top(e_a, "CHG-01", "Abu Dhabi")
        ok = (tb["top"] or {}).get("decision", {}).get("variant") == "single" and \
             (tb["top"] or {}).get("decision", {}).get("legs", [{}])[0].get("from") == "Dubai" and \
             (ta["top"] or {}).get("decision", {}).get("variant") == "split" and ta["naive_rejected"]
        out[1] = (ok, f"before SIG-001: {(tb['top'] or {}).get('label')}; after: {(ta['top'] or {}).get('label')}; "
                      f"full-need Dubai rejected/Harmful: {ta['naive_rejected']}")
    else:
        out[1] = (False, "SIG-001 not loaded yet: drop signals_SIG-001.csv into the inbox to test both states")

    # 2 stockout correction
    det = []
    ok2 = True
    for s, l in (("CBL-03", "Dubai"), ("MSE-02", "Abu Dhabi")):
        fc = eng.fc[(s, l)]
        good = fc.corrected_total > fc.observed_total and len(fc.oos_days) > 0
        ok2 &= good
        det.append(f"{s} {l}: observed {fc.observed_total:.0f}, corrected {fc.corrected_total:.0f}, OOS days {len(fc.oos_days)}")
    out[2] = (ok2, "; ".join(det))

    # 3 Sharjah gap
    rep = validation_report(eng.state)
    gap = [i for i in rep["issues"] if i["check"] == "missing_days" and i.get("location") == "Sharjah" and "sku" not in i]
    days = gap[0]["days"] if gap else []
    rank = {"High": 2, "Medium": 1, "Low": 0}
    sh = np.mean([rank[eng.fc[(s, "Sharjah")].confidence] for s in eng.state.skus])
    du = np.mean([rank[eng.fc[(s, "Dubai")].confidence] for s in eng.state.skus])
    imputed_ok = all(set(days) <= set(int(x) for x in eng.fc[(s, "Sharjah")].imputed_days) for s in eng.state.skus)
    out[3] = (days == list(range(61, 68)) and imputed_ok and sh < du,
              f"missing Sharjah days flagged: {days}; imputed as Estimate: {imputed_ok}; mean confidence Sharjah {sh:.2f} vs Dubai {du:.2f}")

    # 4 KBD-04 aged, oldest first
    cell = next((c for c in planned["cells"] if c["sku"] == "KBD-04" and c["location"] == "Dubai"), {})
    probe = eng.simulate("KBD-04", [{"type": "transfer", "sku": "KBD-04", "legs": [{"from": "Dubai", "to": "Abu Dhabi", "qty": 5}]}])
    picked = probe.picked_batches[0]["batches"][0]["batch_id"] if probe.picked_batches else None
    b = eng.state.batches
    oldest = b[(b["sku"] == "KBD-04") & (b["location"] == "Dubai")].sort_values("age_days", ascending=False)["batch_id"].iloc[0]
    out[4] = ("Aged" in cell.get("flags", []) and picked == oldest, f"flags {cell.get('flags')}; first batch picked {picked}, oldest {oldest}")

    # 5 CBL-02 uneconomic transfer
    rej = [r for r in planned["rejected"] if r["sku"] == "CBL-02" and r["location"] == "Abu Dhabi"
           and r["decision"]["type"] == "transfer" and r["decision"]["legs"][0]["from"] == "Sharjah"]
    r5 = rej[0] if rej else None
    out[5] = (bool(r5 and "Economic" in r5["failing_checks"]),
              (f"{r5['label']} rejected: margin protected {r5['economics']['margin_protected_aed']} AED vs direct cost "
               f"{r5['economics']['direct_cost_aed']} AED") if r5 else "no rejected Sharjah->Abu Dhabi CBL-02 transfer listed")

    # 6 PWB-01 reorder with SIG-001 delay
    tops = [top("PWB-01", l) for l in eng.locs]
    lead = eng.supplier_lead("PWB-01")[1]
    ok6 = all(t and t["decision"]["type"] == "reorder" for t in tops) and "SIG-001" in lead["signals"]
    out[6] = (ok6, f"top options: {[t['label'] if t else None for t in tops]}; lead {lead}")

    # 7 KBD-03 Sharjah reduce/delay PO-107
    t7 = top("KBD-03", "Sharjah")
    out[7] = (bool(t7 and t7["decision"]["type"] in ("reduce_inbound", "delay_inbound") and t7["decision"]["po_id"] == "PO-107"),
              f"top option: {t7['label'] if t7 else None}")

    # 8 / 9 signal scoping
    if "SIG-001" in sig_ids:
        imp = sigmod.impact(eng.state, "SIG-001")
        hit = sorted(x["sku"] for x in imp["skus"] if x["affected"])
        out[8] = (all(s.startswith(("CHG", "PWB")) for s in hit) and len(hit) == 8, f"affected: {hit}")
    else:
        out[8] = (False, "SIG-001 not loaded yet")
    imp2 = sigmod.impact(eng.state, "SIG-002") if "SIG-002" in sig_ids else None
    pos = sorted(x["po_id"] for x in imp2["pos"] if x["affected"]) if imp2 else []
    out[9] = (pos == ["PO-105", "PO-106"], f"affected POs: {pos}")

    # 10 SIG-003 demand uplift only for MSE-01 / KBD-01 at Abu Dhabi
    eff = sorted(f"{s}|{l}" for (s, l), fc in eng.fc.items() if "SIG-003" in fc.signal_ids)
    out[10] = (eff == ["KBD-01|Abu Dhabi", "MSE-01|Abu Dhabi"], f"cells with SIG-003 effect: {eff}")

    # 11 targeted question on PO-108
    qs = planmod.targeted_questions(eng, svc.decisions(), planned["results"], {}, {})
    q = next((x for x in qs if x["po_id"] == "PO-108"), None)
    out[11] = (q is not None, q["question"] if q else "no flip found for PO-108")

    # 12 cycle-count mismatch flagged, corrected with evidence, approved CHG-01 decision Stale
    raw_rep = validation_report(raw)
    flagged = any(i["check"] == "cycle_count_mismatch" and i.get("sku") == "CHG-01" and i.get("location") == "Abu Dhabi"
                  for i in raw_rep["issues"])
    cor = svc.store.query("SELECT * FROM corrections_log WHERE record_id = 'inventory:CHG-01|Abu Dhabi' "
                          "AND evidence_record_id LIKE 'stock_counts:%'")
    stale = [d for d in svc.decisions("Stale") if d["sku"] == "CHG-01"]
    out[12] = (flagged and bool(cor) and bool(stale),
               f"mismatch flagged: {flagged}; correction citing a count: {bool(cor)}; stale CHG-01 decision: {bool(stale)}")

    rows = []
    for _, c in cases.iterrows():
        cid = int(c["case_id"])
        ok, detail = out.get(cid, (False, "no automated check"))
        rows.append({"case_id": cid, "description": c["description"], "expected_action": c["expected_action"],
                     "result": "handled" if ok else "missed", "evidence": detail})
    return rows


def _cell_top(e: Engine, sku: str, loc: str) -> Dict[str, Any]:
    spare = {l: e.spare(sku, l) for l in e.locs}
    res = cand.cell_options(e, sku, loc, spare, cand.cell_status(e, sku, loc))
    naive = [o for o in res["options"] if o["decision"].get("variant") == "naive"]
    return {"top": res["top"], "naive_rejected": bool(naive) and all(o["status"] == "Rejected" for o in naive)}
