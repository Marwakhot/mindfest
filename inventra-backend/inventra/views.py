"""Read models for the tabs: stock (D3), forecast (F1-F8), matrix (N1/N3), projection (N2),
ageing heatmap (N5), freshness (G5). Every value carries its evidence tag (G1)."""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Optional


from . import candidates as cand
from . import signals as sigmod
from .engine import Engine


def _tagged(value, tag: str, **src) -> Dict[str, Any]:
    return {"value": value, "tag": tag, **{k: v for k, v in src.items() if v is not None}}


def _lin(eng: Engine, rid: str) -> Dict[str, Any]:
    l = eng.state.lineage.get(rid, {})
    out = {"record_id": rid, "source_file": l.get("source_file"), "row": l.get("row"), "as_of": l.get("as_of")}
    if rid in eng.state.corrected_fields:
        out["corrected_fields"] = eng.state.corrected_fields[rid]
    return out


# ---------------------------------------------------------------------- D3
def stock(eng: Engine, plan: Dict[str, Any], sku: Optional[str] = None, location: Optional[str] = None) -> List[Dict[str, Any]]:
    st = eng.state
    cells = {(c["sku"], c["location"]): c for c in plan["cells"]}
    out = []
    for (s, l), inv in sorted(st.inventory.items()):
        if (sku and s != sku) or (location and l != location):
            continue
        src = _lin(eng, inv["record_id"])
        incoming = []
        for po in st.inbound:
            if po["sku"] != s or po["location"] != l:
                continue
            delay, ids = sigmod.po_delay(st, po, "likely")
            incoming.append({"po_id": po["po_id"], "qty": po["qty"], "eta": str(po["eta"]),
                             "eta_with_signals": str(st.plan_date(po["eta_day"] + delay)) if delay else str(po["eta"]),
                             "signal_delay_days": delay, "signals": ids, "status": po["status"],
                             "counted_in_plan": eng.counts_inbound(po),
                             "tag": "Data" if not ids else "Assumption", **_lin(eng, po["record_id"])})
        affecting = sorted({i for x in incoming for i in x["signals"]} | set(eng.fc[(s, l)].signal_ids) |
                           set(eng.supplier_lead(s)[1]["signals"]))
        c = cells.get((s, l), {})
        out.append({
            "sku": s, "name": st.skus[s]["name"], "location": l,
            "available": _tagged(inv["available"], "Data", **src),
            "reserved": _tagged(inv["reserved"], "Data", **src),
            "free": _tagged(st.free(s, l), "Estimate", formula="Available - Reserved", inputs=[inv["record_id"]]),
            "safety_stock": _tagged(st.safety.get((s, l)), "Data", **_lin(eng, f"safety_stock:{s}|{l}")),
            "incoming": incoming, "status": c.get("status"), "flags": c.get("flags"),
            "signals_affecting": affecting or None,
            "signals_note": None if affecting else "No supplied signal affects this SKU.",
        })
    return out


# ---------------------------------------------------------------------- F1-F5, F8
def forecast(eng: Engine, sku: Optional[str] = None, location: Optional[str] = None, weekly: bool = False) -> Dict[str, Any]:
    st = eng.state
    rows = []
    for (s, l), fc in sorted(eng.fc.items()):
        if (sku and s != sku) or (location and l != location):
            continue
        sig_effect = None
        if fc.signal_ids:
            sig = [x for x in st.signals if x["signal_id"] in fc.signal_ids]
            sig_effect = {
                "signals": fc.signal_ids,
                "status": [x["status"] for x in sig],
                "tag": "Assumption" if any(x["status"] != "Confirmed" for x in sig) else "Data",
                "without": {p: round(fc.total(p, with_signals=False), 1) for p in ("P10", "P50", "P90")},
                "with": {p: round(fc.total(p), 1) for p in ("P10", "P50", "P90")},
            }
        h = fc.history
        hist = []
        for i in range(st.history_days):
            hist.append({"day": i + 1, "date": str(st.day1 + timedelta(days=i)),
                         "observed": None if h.missing[i] else float(h.observed[i]),
                         "corrected": round(float(fc.corrected_series[i]), 2),
                         "out_of_stock": bool(h.oos[i]), "imputed": bool(h.missing[i]),
                         "tag": "Estimate" if (h.missing[i] or h.oos[i]) else "Data"})
        fut = []
        p10, p50, p90 = fc.daily("P10"), fc.daily("P50"), fc.daily("P90")
        for t in range(eng.H):
            fut.append({"plan_day": t + 1, "date": str(st.plan_date(t + 1)), "p10": round(float(p10[t]), 2),
                        "p50": round(float(p50[t]), 2), "p90": round(float(p90[t]), 2), "tag": "Estimate"})
        if weekly:
            hist, fut = _weekly(hist, ["observed", "corrected"]), _weekly(fut, ["p10", "p50", "p90"])
        rows.append({
            "sku": s, "name": st.skus[s]["name"], "location": l,
            "p10": round(fc.total("P10"), 1), "p50": round(fc.total("P50"), 1), "p90": round(fc.total("P90"), 1),
            "tag": "Estimate", "confidence": fc.confidence, "confidence_detail": fc.confidence_detail,
            "stockout_correction": {"observed_total": fc.observed_total, "corrected_total": round(fc.corrected_total, 1),
                                    "out_of_stock_days": [int(d) for d in fc.oos_days],
                                    "out_of_stock_dates": [str(st.day1 + timedelta(days=int(d) - 1)) for d in fc.oos_days]},
            "imputed": {"days": [int(d) for d in fc.imputed_days],
                        "dates": [str(st.day1 + timedelta(days=int(d) - 1)) for d in fc.imputed_days], "tag": "Estimate"},
            "signal_effect": sig_effect,
            "signal_note": None if sig_effect else "No supplied signal affects this SKU.",
            "band": {"p10_multiplier": round(fc.m10, 4), "p90_multiplier": round(fc.m90, 4),
                     "widened_for_low_confidence": fc.confidence == "Low"},
            "chart": {"granularity": "weekly" if weekly else "daily", "history": hist, "forecast": fut},
        })
    return {"planning_day": str(st.day90), "horizon_days": eng.H, "rows": rows,
            "limitations": eng.fc_meta["limitations"],
            "seasonality_note": "90 days of history cannot establish annual seasonality."}


def _weekly(points: List[Dict[str, Any]], keys: List[str]) -> List[Dict[str, Any]]:
    out = []
    for i in range(0, len(points), 7):
        chunk = points[i:i + 7]
        row = {"week_start": chunk[0]["date"], "days": len(chunk)}
        for k in keys:
            vals = [p[k] for p in chunk if p.get(k) is not None]
            row[k] = round(sum(vals), 2) if vals else None
        if "out_of_stock" in chunk[0]:
            row["out_of_stock_days"] = sum(p["out_of_stock"] for p in chunk)
            row["imputed_days"] = sum(p["imputed"] for p in chunk)
        out.append(row)
    return out


def accuracy(eng: Engine) -> Dict[str, Any]:
    m = eng.fc_meta
    w = m["backtest"]["wape"]
    chosen = "corrected_dow" if m["components"]["day_of_week"] else ("corrected" if m["components"]["stockout_correction"] else "uncorrected")
    return {
        "backtest_origins_day": m["backtest"]["origins"],
        "wape": {"inventra": round(w[chosen], 4) if w[chosen] is not None else None,
                 "baseline_recent_average": round(w["baseline"], 4) if w["baseline"] is not None else None,
                 "by_component": {k: (round(v, 4) if v is not None else None) for k, v in w.items()}},
        "bias": {k: (round(v, 4) if v is not None else None) for k, v in m["backtest"]["bias"].items()},
        "components_kept": m["components"],
        "component_rule": "A component stays in only if it does not raise backtest WAPE (errors measured on in-stock, non-missing days).",
        "p10_p90_coverage": m["coverage"], "coverage_target": 0.8,
        "bands_by_category": m["bands"], "day_of_week_pattern": m["dow_pattern"],
        "limitations": m["limitations"],
    }


# ---------------------------------------------------------------------- N1 / N3
def matrix(eng: Engine, plan: Dict[str, Any]) -> Dict[str, Any]:
    rows: Dict[str, Dict[str, Any]] = {}
    urg = {(e["sku"], e["location"]): e["urgency"] for e in plan["queue"] + plan["no_action"]}
    for c in plan["cells"]:
        r = rows.setdefault(c["sku"], {"sku": c["sku"], "name": eng.state.skus[c["sku"]]["name"], "cells": {}})
        r["cells"][c["location"]] = {"status": c["status"], "flags": c["flags"],
                                     "urgency": urg.get((c["sku"], c["location"]), "OK"),
                                     "cover_days": c["cover_days"], "excess_units": c["excess_units"],
                                     "aged_units": c["aged_units"], "ageing_units": c["ageing_units"],
                                     "approved_decision_id": c.get("approved_decision_id"), "tag": "Estimate"}
    return {"locations": eng.locs, "statuses": ["Short", "Buffer breach", "Healthy", "Excess", "Ageing", "Aged"],
            "rows": list(rows.values())}


# ---------------------------------------------------------------------- N2
def projection(eng: Engine, plan: Dict[str, Any], sku: str, location: str) -> Dict[str, Any]:
    st = eng.state
    if (sku, location) not in st.inventory:
        raise KeyError((sku, location))
    p10 = eng.stock_path(sku, location, "P10", "likely")
    p50 = eng.stock_path(sku, location, "P50", "likely")
    p90 = eng.stock_path(sku, location, "P90", "likely")
    ss = st.safety.get((sku, location), 0)
    maxcov = eng.p("max_cover_days") * eng.d50(sku, location)
    series = [{"plan_day": t + 1, "date": str(st.plan_date(t + 1)), "p50": round(float(p50[t]), 1),
               "band_low": round(float(p90[t]), 1), "band_high": round(float(p10[t]), 1)} for t in range(eng.H)]
    inbound = [{"po_id": a["po_id"], "qty": a["qty"], "plan_day": a["day"], "date": str(st.plan_date(a["day"])),
                "original_eta": str(next(p["eta"] for p in st.inbound if p["po_id"] == a["po_id"])),
                "signal_delay_days": a["delay"], "signals": a["signals"]}
               for a in eng.inbound(sku) if a["loc"] == location]
    stockout = next((t + 1 for t in range(eng.H) if p50[t] <= 0), None)
    ro = cand.reorder_plan(eng, sku, location)
    return {
        "sku": sku, "location": location, "series": series, "tag": "Estimate",
        "band_note": "Band runs from free stock under P90 demand (low) to P10 demand (high)",
        "safety_stock": ss, "max_cover_units": round(maxcov, 1), "inbound_steps": inbound,
        "markers": {
            "breach": {"plan_day": eng.breach_day(sku, location),
                       "date": str(st.plan_date(eng.breach_day(sku, location))) if eng.breach_day(sku, location) is not None else None},
            "stockout": {"plan_day": stockout, "date": str(st.plan_date(stockout)) if stockout else None},
            "order_by": {"plan_day": ro["order_by_day"], "date": ro["order_by_date"]} if ro else None,
        },
    }


# ---------------------------------------------------------------------- N5
def ageing_heatmap(eng: Engine) -> Dict[str, Any]:
    st = eng.state
    b = st.batches
    out = []
    if b.empty:
        return {"rows": out}
    age_d, aged_d = eng.p("ageing_days"), eng.p("aged_days")
    for (s, l), grp in b.groupby(["sku", "location"]):
        cost = st.skus[s]["unit_cost"]
        tiers = {"Fresh": grp[grp["age_days"] < age_d], "Ageing": grp[(grp["age_days"] >= age_d) & (grp["age_days"] < aged_d)],
                 "Aged": grp[grp["age_days"] >= aged_d]}
        out.append({"sku": s, "location": l, "tag": "Data",
                    "tiers": {k: {"units": int(v["units"].sum()), "aed": round(float(v["units"].sum()) * cost, 2),
                                  "batches": v["batch_id"].tolist()} for k, v in tiers.items()}})
    return {"ageing_days": age_d, "aged_days": aged_d, "rows": out}


# ---------------------------------------------------------------------- G5
def freshness(eng: Engine) -> Dict[str, Any]:
    st = eng.state
    days = eng.p("freshness_days")
    per = {}
    for l in eng.locs:
        inv_asof = max((v["as_of"] for (s, loc), v in st.inventory.items() if loc == l), default=None)
        sales = st.sales[st.sales["location"] == l]
        last_sale = max(sales["date"]) if len(sales) else None
        per[l] = {"inventory_as_of": inv_asof.isoformat() if inv_asof else None,
                  "last_sales_date": str(last_sale) if last_sale else None,
                  "stale": bool((last_sale and (st.day90 - last_sale).days > days) or
                                (inv_asof and (st.day90 - inv_asof.date()).days > days))}
    as_ofs = {v["inventory_as_of"] for v in per.values()}
    banner = None
    if any(v["stale"] for v in per.values()):
        banner = "Data for " + ", ".join(l for l, v in per.items() if v["stale"]) + f" is older than {days} days."
    elif len(as_ofs) > 1:
        banner = "Locations have different as-of dates."
    return {"freshness_days": days, "locations": per, "banner": banner}
