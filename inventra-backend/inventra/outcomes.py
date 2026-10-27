"""Decision Log, outcome review and track record (Scenario Lab spec: "Decision Log and Outcome Review").

Two kinds of record share one log:
  * real decisions approved, rejected or overridden in the app. They are frozen at approval and
    stay "Awaiting actuals" (there is no time machine; actuals for them do not exist yet);
  * DEMO HISTORY records from data/seed/demo_decision_history.json, synthetic decisions approved
    before the planning day together with their actuals. They are labelled demo everywhere.

Outcome labels, forecast error, interval coverage, bias and suggestions are computed here from
the records with fixed rules; nothing is typed in. A suggestion changes data only when the planner
approves it, and then only through the normal correction flow (logged, original kept, re-plan).
"""
from __future__ import annotations

import json
import math
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import numpy as np

from .config import settings
from .service import HITLError, Service, require_reason
from .storage import now_iso

HORIZON = 30
MIN_FOR_HEADLINE = 10          # no headline score until this many decisions are reviewed
MIN_FOR_BIAS = 2               # a bias cell needs at least this many reviewed decisions
UNDER_FORECAST_PCT = 5.0       # actual above P50 by more than this counts as an under-forecast
LABELS = ("Hit", "Right call, unlucky", "Wrong call", "Inconclusive")


def _demo() -> List[Dict[str, Any]]:
    path = settings.seed_dir / "demo_decision_history.json"
    if not path.exists():
        return []
    return json.loads(path.read_text())["records"]


def _days_since(ts: str, planning_day: date) -> int:
    return max(0, (planning_day - datetime.fromisoformat(ts).date()).days)


# ---------------------------------------------------------------------- review rules
def review(rec: Dict[str, Any], planning_day: date) -> Dict[str, Any]:
    """Score a demo record that has actuals. Separates three questions instead of one success rate."""
    a = rec.get("actuals")
    elapsed = _days_since(rec["approved_at"], planning_day)
    if not a or elapsed < HORIZON:
        return {"status": "awaiting", "day": min(elapsed, HORIZON)}
    f = rec["forecast"]
    actual = float(a["demand"])
    inside = f["p10"] <= actual <= f["p90"]
    fd = np.array(rec["forecast_daily_p50"], dtype=float)
    bd = np.array(rec["baseline_daily"], dtype=float)
    ad = np.array(a["demand_daily"], dtype=float)
    wape = float(np.abs(fd - ad).sum() / ad.sum())
    wape_base = float(np.abs(bd - ad).sum() / ad.sum())
    bias = float((fd.sum() - ad.sum()) / ad.sum())
    rows = []
    for x in rec["assumptions"]:
        if x.get("kind") == "demand":
            rows.append({"item": x["item"], "assumed": f"P10–P90 {f['p10']}–{f['p90']}", "actual": f"{actual:.0f} units",
                         "crossed": not inside, "breaking_point": f"outside {f['p10']}–{f['p90']}", "tag": x["tag"]})
            continue
        act = a["assumptions"].get(x["item"])
        crossed = act is not None and ((act > x["breaks_at"]) if x["worse"] == "above" else (act < x["breaks_at"]))
        drift = act is not None and act != x["assumed"]
        rows.append({"item": x["item"], "assumed": _fmt(x["assumed"], x["unit"]), "actual": _fmt(act, x["unit"]) if act is not None else "not observed",
                     "crossed": bool(crossed), "drifted": bool(drift),
                     "breaking_point": f"{'above' if x['worse'] == 'above' else 'below'} {_fmt(x['breaks_at'], x['unit'])}", "tag": x["tag"]})
    breached = [r["item"] for r in rows if r["crossed"]]
    realised, expected = float(a["realised_net"]), float(rec["expected_net"])
    # Labels (spec): Inconclusive -> an unrelated event or sparse data distorts the result;
    # Right call, unlucky -> assumptions were breached and the benefit fell short;
    # Wrong call -> assumptions held, yet the benefit did not appear; Hit -> otherwise positive.
    if a.get("distortion"):
        label, why = "Inconclusive", a["distortion"]
    elif breached and realised < expected:
        label = "Right call, unlucky"
        names = " and ".join([", ".join(breached[:-1]), breached[-1]] if len(breached) > 1 else breached)
        why = (f"{names} {'crossed their breaking points' if len(breached) > 1 else 'crossed its breaking point'}, which the decision did not assume. "
               f"The decision was sound given what was known on {rec['approved_at'][:10]}.")
    elif not breached and realised <= 0:
        label, why = "Wrong call", "The assumptions held, yet the expected benefit did not appear."
    else:
        label = "Hit"
        why = ("Demand landed inside the forecast interval, the assumptions held and the benefit appeared."
               if not breached else "The benefit appeared even though an assumption was crossed.")
    return {"status": "reviewed", "day": HORIZON, "label": label, "why": why,
            "forecast": {"p10": f["p10"], "p50": f["p50"], "p90": f["p90"], "actual": actual, "inside": inside},
            "wape": round(wape, 4), "wape_baseline": round(wape_base, 4), "bias": round(bias, 4),
            "assumptions": rows, "breached": breached,
            "expected_net": expected, "realised_net": realised,
            "realised_share": round(realised / expected, 4) if expected else None,
            "untouched_comparison": a.get("untouched_comparison"),
            "counterfactual": "Realised benefit is measured against the original do-nothing forecast and "
                              f"{a.get('untouched_comparison') or 0} comparable untouched SKU-locations (estimated)."}


def _fmt(v, unit):
    if v is None:
        return "—"
    if unit == "AED":
        return f"AED {v:,.0f}"
    if unit == "days":
        return f"+{v} days" if isinstance(v, (int, float)) and v > 0 else f"{v} days"
    if unit == "%":
        return f"{v}%"
    return f"{v} {unit}"


# ---------------------------------------------------------------------- the log
def _real(svc: Service, eng) -> List[Dict[str, Any]]:
    out = []
    for d in svc.decisions():
        R = d.get("receipt") or {}
        lab = R.get("lab") or {}
        inp = R.get("inputs") or []
        loc = d["location"]
        pv = {p: next((x["value"] for x in inp if x.get("label") == f"{loc} {p} demand, 30 days"), None) for p in ("P10", "P50", "P90")}
        status = d["status"]
        out.append({
            "id": d["decision_id"], "demo": False, "sku": d["sku"], "location": loc,
            "label": (d.get("chosen") or {}).get("label"), "decision": d.get("chosen"),
            "approved_at": d["timestamp"], "approver": d["approver"], "status": status,
            "snapshot": d["input_hash"], "reason": d.get("reason"),
            "settings": lab.get("settings") or "shared plan",
            "expected_net": lab.get("expected_net_aed", (R.get("chosen") or {}).get("net_benefit_aed")),
            "expected_range": lab.get("net_range_aed"),
            "forecast": {"p10": pv["P10"], "p50": pv["P50"], "p90": pv["P90"]},
            "assumption_count": sum(1 for x in inp if x.get("tag") == "Assumption"),
            "breaking_points": len((lab.get("breaking_points") or {}).get("rows") or []) or None,
            "review": ({"status": "awaiting", "day": 0} if status in ("Approved", "Overridden") else {"status": status.lower()}),
        })
    return out


def _demo_rows(eng) -> List[Dict[str, Any]]:
    out = []
    for r in _demo():
        out.append({**{k: r[k] for k in ("id", "sku", "location", "label", "decision", "approved_at", "approver", "snapshot",
                                         "settings", "expected_net", "expected_range", "forecast", "breaking_points")},
                    "demo": True, "status": "Approved",
                    "assumption_count": sum(1 for x in r["assumptions"] if x["tag"] == "Assumption"),
                    "assumption_total": len(r["assumptions"]),
                    "review": review(r, eng.state.day90)})
    return out


def log(svc: Service) -> Dict[str, Any]:
    eng, _ = svc.ensure()
    rows = _demo_rows(eng) + _real(svc, eng)
    rows.sort(key=lambda r: r["approved_at"], reverse=True)
    sug = suggestions(svc, rows)
    counts = {"logged": len(rows),
              "awaiting": sum(1 for r in rows if r["review"]["status"] == "awaiting"),
              "reviewed": sum(1 for r in rows if r["review"]["status"] == "reviewed"),
              "suggestions_pending": sum(1 for s in sug if s["status"] == "Pending")}
    return {"planning_day": str(eng.state.day90), "counts": counts, "rows": rows, "suggestions": sug,
            "demo_note": "DL-records are demo history: synthetic decisions with synthetic actuals, shown so the review can be seen working."}


def record(svc: Service, rid: str) -> Dict[str, Any]:
    L = log(svc)
    row = next((r for r in L["rows"] if r["id"] == rid), None)
    if row is None:
        raise HITLError(f"Decision {rid} not found", 404)
    out = {**row, "suggestions": [s for s in L["suggestions"] if rid in s["from_records"]]}
    if row["demo"]:
        out["assumptions"] = next(r for r in _demo() if r["id"] == rid)["assumptions"]
    else:
        out["receipt_decision_id"] = rid
    return out


# ---------------------------------------------------------------------- track record
def track_record(svc: Service) -> Dict[str, Any]:
    L = log(svc)
    rev = [r for r in L["rows"] if r["review"]["status"] == "reviewed"]
    n = len(rev)
    cover = sum(1 for r in rev if r["review"]["forecast"]["inside"])
    cats = {"CHG": "Chargers", "PWB": "Power banks", "CBL": "Cables", "MSE": "Mice", "KBD": "Keyboards"}
    bias: Dict[str, Dict[str, Any]] = {}
    for r in rev:
        c = cats.get(r["sku"][:3], "Other")
        f = r["review"]["forecast"]
        bias.setdefault(c, {}).setdefault(r["location"], []).append((f["p50"] - f["actual"]) / f["actual"])
    bias_out = {c: {l: ({"n": len(v), "bias_pct": round(100 * float(np.mean(v)), 1)} if len(v) >= MIN_FOR_BIAS else {"n": len(v), "bias_pct": None})
                    for l, v in locs.items()} for c, locs in bias.items()}
    fails: Dict[str, Dict[str, int]] = {}
    for r in rev:
        for a in r["review"]["assumptions"]:
            k = "Demand level (outside P10–P90)" if a["item"].endswith("demand level") else a["item"]
            x = fails.setdefault(k, {"failed": 0, "n": 0})
            x["n"] += 1
            x["failed"] += int(a["crossed"])
    labels = {l: sum(1 for r in rev if r["review"]["label"] == l) for l in LABELS}
    return {
        "reviewed": n, "minimum_for_headline": MIN_FOR_HEADLINE, "headline_available": n >= MIN_FOR_HEADLINE,
        "interval_coverage": {"inside": cover, "n": n, "target_pct": 80},
        "avg_predicted_net": round(float(np.mean([r["review"]["expected_net"] for r in rev])), 2) if rev else None,
        "avg_realised_net": round(float(np.mean([r["review"]["realised_net"] for r in rev])), 2) if rev else None,
        "confidence": "Low" if n < MIN_FOR_HEADLINE else ("Medium" if n < 2 * MIN_FOR_HEADLINE else "High"),
        "bias": bias_out, "locations": ["Dubai", "Abu Dhabi", "Sharjah"],
        "assumption_failures": sorted([{"item": k, **v} for k, v in fails.items()], key=lambda x: -x["failed"] / max(x["n"], 1)),
        "labels": labels, "demo_records": sum(1 for r in rev if r["demo"]),
        "note": "Every metric shows its sample size. Results on demo history show the mechanism, not real-world accuracy.",
    }


# ---------------------------------------------------------------------- suggestions (closing the loop)
def suggestions(svc: Service, rows: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """Repeated under-forecasts in one category and location -> propose a higher safety stock.
    Proposed = current safety stock x (1 + mean under-forecast), rounded up to 5 units."""
    eng, _ = svc.ensure()
    rows = rows if rows is not None else (_demo_rows(eng) + _real(svc, eng))
    rev = [r for r in rows if r["review"]["status"] == "reviewed" and r["review"]["label"] != "Inconclusive"]
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for r in rev:
        f = r["review"]["forecast"]
        under = (f["actual"] - f["p50"]) / f["p50"] * 100
        if under > UNDER_FORECAST_PCT:
            groups.setdefault((r["sku"][:3], r["location"]), []).append({**r, "_under": under})
    state = {x["suggestion_id"]: x for x in svc.store.query("SELECT * FROM review_suggestions")}
    out = []
    for (cat, loc), rs in groups.items():
        if len(rs) < 2:
            continue
        latest = max(rs, key=lambda r: r["approved_at"])
        sku = latest["sku"]
        cur = eng.state.safety.get((sku, loc))
        if cur is None:
            continue
        mean_under = float(np.mean([r["_under"] for r in rs]))
        proposed = int(math.ceil(cur * (1 + mean_under / 100) / 5) * 5)
        sid = f"SUG-{sku}-{loc.replace(' ', '')}"
        st = state.get(sid, {})
        if proposed <= cur and st.get("status") != "Approved":
            continue
        out.append({
            "suggestion_id": sid, "sku": sku, "sku_name": eng.state.skus[sku]["name"], "location": loc,
            "field": "safety_stock.units", "record_id": f"safety_stock:{sku}|{loc}",
            "current": cur, "proposed": int(st.get("proposed") or proposed),
            "from_records": [r["id"] for r in rs], "mean_under_forecast_pct": round(mean_under, 1),
            "text": (f"{loc} demand was under-forecast in {len(rs)} reviewed decisions (by {mean_under:.0f}% on average). "
                     f"Raise {eng.state.skus[sku]['name']} safety stock in {loc} from {cur} to {proposed}?"),
            "tag": "Assumption", "status": st.get("status") or "Pending", "reason": st.get("reason"),
            "decided_by": st.get("user"), "decided_at": st.get("ts"), "correction_id": st.get("correction_id"),
        })
    return out


def act_on_suggestion(svc: Service, sid: str, action: str, reason: Optional[str]) -> Dict[str, Any]:
    with svc.lock:
        sug = next((s for s in suggestions(svc) if s["suggestion_id"] == sid), None)
        if sug is None:
            raise HITLError(f"Suggestion {sid} not found", 404)
        if sug["status"] != "Pending":
            raise HITLError(f"Suggestion {sid} is already {sug['status']}.", 409)
        row = {"suggestion_id": sid, "status": None, "reason": None, "user": settings.planner_user, "ts": now_iso(),
               "correction_id": None, "proposed": sug["proposed"]}
        wc = None
        if action == "approve":
            why = (reason or "").strip() or f"Approved suggestion {sid} from outcome review of {', '.join(sug['from_records'])}"
            res = svc.correct(sug["record_id"], "units", sug["proposed"],
                              f"{why} (suggested by outcome review; demo history: {', '.join(r for r in sug['from_records'] if r.startswith('DL-'))})")
            row.update(status="Approved", reason=why, correction_id=res["correction"]["correction_id"])
            wc = res["what_changed"]
        elif action in ("reject", "send_back"):
            row.update(status="Rejected" if action == "reject" else "Sent back",
                       reason=require_reason(reason, "reject a suggestion" if action == "reject" else "send a suggestion back"))
        else:
            raise HITLError("action must be approve, reject or send_back", 422)
        svc.store.execute("DELETE FROM review_suggestions WHERE suggestion_id = ?", [sid])
        svc.store.insert("review_suggestions", row)
        svc.audit(f"suggestion_{row['status'].lower().replace(' ', '_')}", sid, row["reason"], {"suggestion": sug})
        return {"suggestion": next(s for s in suggestions(svc) if s["suggestion_id"] == sid), "what_changed": wc}


# ---------------------------------------------------------------------- explanation (chat)
def explain(svc: Service, rid: str) -> Dict[str, Any]:
    from .llm import explain as llm
    rec = record(svc, rid)
    rv = rec["review"]
    F, R = [], []

    def f(label, value, unit=None, tag="Estimate"):
        F.append({"id": f"F{len(F) + 1}", "label": label, "value": value, "unit": unit, "tag": tag})
        return F[-1]["id"]
    if rv["status"] != "reviewed":
        i1 = f("Decision", rec["label"])
        i2 = f("Days since approval", rv.get("day", 0), "days")
        tpl = f"{rec['label']} is still awaiting actuals [{i1}], {rv.get('day', 0)} of 30 days in [{i2}]."
        return {"facts": {"facts": F, "reasons": R}, "template": tpl, "text": None, "mode": "keyless"}
    i_lab = f("Outcome label", rv["label"])
    i_e = f("Expected net benefit", round(rv["expected_net"]), "AED")
    i_r = f("Realised net benefit (counterfactual estimated)", round(rv["realised_net"]), "AED", "Assumption" if rec["demo"] else "Estimate")
    i_a = f("Actual 30-day demand", round(rv["forecast"]["actual"]), "units", "Data")
    i_p10, i_p90 = f("Forecast P10", rv["forecast"]["p10"], "units"), f("Forecast P90", rv["forecast"]["p90"], "units")
    for a in rv["assumptions"]:
        if a["crossed"]:
            R.append({"id": f"R{len(R) + 1}", "text": f"{a['item']}: assumed {a['assumed']}, actual {a['actual']}, crossed its breaking point", "tag": "Estimate"})
    held = [a["item"] for a in rv["assumptions"] if not a["crossed"]]
    tpl = (f"Outcome: {rv['label']} [{i_lab}]. Net benefit was AED {round(rv['expected_net'])} expected and AED {round(rv['realised_net'])} realised [{i_e}, {i_r}]. "
           f"Actual demand of {round(rv['forecast']['actual'])} units was {'inside' if rv['forecast']['inside'] else 'outside'} the P10 to P90 range of "
           f"{rv['forecast']['p10']} to {rv['forecast']['p90']} [{i_a}, {i_p10}, {i_p90}]"
           + (f"; {', '.join(rv['breached'])} broke the assumptions [{', '.join(r['id'] for r in R)}]" if R else "")
           + (f", while {', '.join(held)} held." if held else "."))
    out = {"facts": {"facts": F, "reasons": R}, "template": tpl, "mode": "keyed" if settings.keyed else "keyless", "text": None}
    if settings.keyed:
        x = llm.explain(svc, "outcome", {"facts": F, "reasons": R}, HORIZON)
        out.update({k: x.get(k) for k in ("text", "verified", "badge", "fallback")})
    return out
