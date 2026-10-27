"""Scenario Lab tabs: one decision per tab, per-tab disruption settings, Compare view.

Everything here is a sandbox. Tab settings never change the shared plan; approving a tab
re-checks its decision on the shared plan (same rule and robustness gates as the queue).

Statistics per tab come from the engine only:
  * a base case (the chosen demand level, likely delay) for charts, timing and breaking points;
  * N simulated demand paths for probabilities and ranges: each path draws a 30-day demand level per
    location from that cell's P10/P50/P90 band (split log-normal through the three points) and, when
    "chance a delay happens" is below 100%, whether the tab's delays happen.
"""
from __future__ import annotations

import copy
import json
import math
import uuid
from dataclasses import replace
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from . import candidates as cand
from . import plan as planmod
from . import receipts
from . import signals as sigmod
from .engine import EPS, LOST_EPS, Engine, floor_pack
from .service import HITLError, Service, require_reason
from .state import stable_hash
from .storage import now_iso
from .tree import to_decision

N_PATHS = 80
CACHE_VERSION = "lab-1"
Z90 = 1.2815515655446004
LEVEL_PCT = {"low": "P10", "expected": "P50", "high": "P90"}
SEVERITY = {"mild": "min", "likely": "likely", "severe": "max"}
STATUSES = ("Draft", "Ready", "Approved", "Rejected", "Sent back")
LOCKED = ("Approved", "Rejected")
_MEM: Dict[str, Dict[str, Any]] = {}  # in-process cache of computed results by cache key


# ---------------------------------------------------------------------- events and settings
def events_for(eng: Engine, sku: str) -> Dict[str, List[Dict[str, Any]]]:
    """Supplied supply signals that can touch this SKU: component events and route events."""
    st = eng.state
    comp, route = [], []
    for s in st.signals:
        if s["type"] != "supply":
            continue
        if sigmod.component_match(s, st.skus[sku]):
            comp.append(s)
        elif any(p["sku"] == sku and p.get("route") in (s.get("routes") or []) for p in st.inbound):
            route.append(s)
    return {"component": comp, "route": route}


def default_settings(eng: Engine, sku: str) -> Dict[str, Any]:
    ev = events_for(eng, sku)
    on = lambda xs: "likely" if any(s.get("include", True) for s in xs) else "none"
    return {"shipment_delay_days": 0, "supply_shortage": on(ev["component"]), "route_delay": on(ev["route"]),
            "delay_chance_pct": 100, "demand": "expected", "transfer_cost_change_pct": 0}


def validate_settings(eng: Engine, sku: str, s: Dict[str, Any]) -> Dict[str, Any]:
    out = {**default_settings(eng, sku), **(s or {})}
    unknown = set(out) - set(default_settings(eng, sku))
    if unknown:
        raise HITLError(f"Unknown settings: {', '.join(sorted(unknown))}", 422)
    try:
        out["shipment_delay_days"] = int(out["shipment_delay_days"])
        out["delay_chance_pct"] = float(out["delay_chance_pct"])
        out["transfer_cost_change_pct"] = float(out["transfer_cost_change_pct"])
    except (TypeError, ValueError):
        raise HITLError("Numeric settings must be numbers", 422)
    if not (0 <= out["shipment_delay_days"] <= 30):
        raise HITLError("shipment_delay_days must be 0 to 30", 422)
    if not (0 <= out["delay_chance_pct"] <= 100):
        raise HITLError("delay_chance_pct must be 0 to 100", 422)
    if not (-50 <= out["transfer_cost_change_pct"] <= 300):
        raise HITLError("transfer_cost_change_pct must be -50 to 300", 422)
    if out["demand"] not in LEVEL_PCT:
        raise HITLError("demand must be low, expected or high", 422)
    ev = events_for(eng, sku)
    for k, kind in (("supply_shortage", "component"), ("route_delay", "route")):
        if out[k] not in ("none", "mild", "likely", "severe"):
            raise HITLError(f"{k} must be none, mild, likely or severe", 422)
        if out[k] != "none" and not ev[kind]:
            raise HITLError(f"No supplied {'component' if kind == 'component' else 'shipping-route'} event names {sku}; "
                            "only events in the dated signal pack can be applied.", 422)
    return out


def light_copy(state):
    """Copy only what a sandbox edits (signals, lanes, inventory, inbound); history frames are shared read-only."""
    st = copy.copy(state)
    st.signals = copy.deepcopy(state.signals)
    st.lanes = {k: dict(v) for k, v in state.lanes.items()}
    st.inventory = {k: dict(v) for k, v in state.inventory.items()}
    st.inbound = [dict(p) for p in state.inbound]
    return st


def sandbox(eng: Engine, sku: str, settings: Dict[str, Any], delays_on: bool = True) -> Engine:
    st = light_copy(eng.state)
    st.sandbox = True
    ev = events_for(eng, sku)
    for kind, key_ in (("component", "supply_shortage"), ("route", "route_delay")):
        level = settings[key_] if delays_on else "none"
        ids = {s["signal_id"] for s in ev[kind]}
        for s in st.signals:
            if s["signal_id"] in ids:
                s["include"] = level != "none"
                if level != "none":
                    s["delay_likely"] = s.get("delay_" + SEVERITY[level])
    extra = int(settings["shipment_delay_days"]) if delays_on else 0
    mult = 1 + float(settings["transfer_cost_change_pct"]) / 100.0
    for lane in st.lanes.values():
        lane["lead_days"] = int(lane["lead_days"]) + extra
        lane["cost_per_unit_aed"] = float(lane["cost_per_unit_aed"]) * mult
        lane["fixed_cost_aed"] = float(lane["fixed_cost_aed"]) * mult
    st.order_delay_add = extra
    return eng.derive(st, refresh_uplift=False)  # supply settings never change demand


def chips(settings: Dict[str, Any], ev: Dict[str, List]) -> List[str]:
    sev = {"none": "off", "mild": "mild", "likely": "likely", "severe": "severe"}
    out = [f"Delay +{settings['shipment_delay_days']} days"]
    if ev["component"]:
        out.append(f"Supply shortage {sev[settings['supply_shortage']]}")
    if ev["route"]:
        out.append(f"Route delay {sev[settings['route_delay']]}")
    if settings["delay_chance_pct"] < 100:
        out.append(f"Delay chance {settings['delay_chance_pct']:g}%")
    out.append(f"Demand: {settings['demand']}")
    if settings["transfer_cost_change_pct"]:
        out.append(f"Transfer cost {settings['transfer_cost_change_pct']:+g}%")
    return out


def describe_settings(settings: Dict[str, Any], ev) -> Dict[str, str]:
    return {c.split(" ")[0] + (" " + c.split(" ")[1] if c.startswith(("Supply", "Route", "Delay chance", "Transfer")) else ""): c
            for c in chips(settings, ev)}


# ---------------------------------------------------------------------- decision helpers
def target_location(d: Dict[str, Any], fallback: str, eng: Engine) -> str:
    if d["type"] == "transfer":
        return d["legs"][0]["to"]
    if d.get("location"):
        return d["location"]
    if d.get("po_id"):
        po = next((p for p in eng.state.inbound if p["po_id"] == d["po_id"]), None)
        if po:
            return po["location"]
    return fallback


def short_name(d: Optional[Dict[str, Any]]) -> str:
    if not d or d["type"] == "do_nothing":
        return "Do nothing"
    t = d["type"]
    if t == "transfer":
        return "Split transfer" if len(d["legs"]) > 1 else f"Transfer {d['legs'][0]['from']} to {d['legs'][0]['to']}"
    return {"reorder": "External order", "bring_forward": f"Bring {d.get('po_id')} forward",
            "delay_inbound": f"Delay {d.get('po_id')}", "reduce_inbound": f"Reduce {d.get('po_id')}",
            "discount": "Discount ageing stock", "wait": "Wait for inbound"}.get(t, t)


def arrival_day(e: Engine, d: Optional[Dict[str, Any]]) -> Optional[int]:
    if not d:
        return None
    t = d["type"]
    if t == "transfer":
        return max(int(e.lane(l["from"], l["to"])["lead_days"]) for l in d["legs"])
    if t == "reorder":
        return int(d.get("order_day", 0)) + e.supplier_lead(d["sku"])[0]
    if t in ("bring_forward", "delay_inbound", "reduce_inbound"):
        a = next((x for x in e.inbound(d["sku"], "likely", [d]) if x["po_id"] == d["po_id"]), None)
        return a["day"] if a else None
    if t == "discount":
        return 1
    return None


def decision_list(d):
    return [] if not d or d["type"] in ("do_nothing", "wait") else [d]


# ---------------------------------------------------------------------- simulated paths
def _split_lognormal(m10: float, m90: float) -> Tuple[float, float]:
    up = math.log(max(m90, 1.0001)) / Z90
    dn = -math.log(min(max(m10, 1e-3), 0.9999)) / Z90
    return up, dn


def simulate_paths(eng: Engine, e_on: Engine, e_off: Engine, sku: str, decs, settings, seed_key: str, n: int = N_PATHS):
    rng = np.random.default_rng(int(stable_hash(seed_key)[:8], 16))
    p = float(settings["delay_chance_pct"]) / 100.0
    level = settings["demand"]
    params = {}
    for l in eng.locs:
        c = eng.fc[(sku, l)]
        shift = c.m10 if level == "low" else c.m90 if level == "high" else 1.0
        params[l] = (shift, *_split_lognormal(c.m10, c.m90))
    margin = eng.state.margin(sku)
    rows = []
    for _ in range(n):
        scale = {}
        for l, (shift, up, dn) in params.items():
            z = float(rng.standard_normal())
            scale[l] = shift * math.exp(z * (up if z > 0 else dn))
        delayed = bool(rng.random() < p)
        e = e_on if delayed else e_off
        base = e.simulate(sku, [], "P50", "likely", scale)
        opt = e.simulate(sku, decs, "P50", "likely", scale)
        net = e.net(sku, opt, base)
        rows.append({
            "net": net["net_benefit_aed"],
            "lost_b": base.total("lost"), "lost_o": opt.total("lost"),
            "out_b": {l: base.locs[l].lost > LOST_EPS for l in eng.locs},
            "out_o": {l: opt.locs[l].lost > LOST_EPS for l in eng.locs},
            "sold_delta": {l: (opt.locs[l].sold - base.locs[l].sold) * margin for l in eng.locs},
            "hold_b": base.total("holding"), "hold_o": opt.total("holding"),
            "age_b": base.total("ageing_cost"), "age_o": opt.total("ageing_cost"),
            "transfer": opt.transfer_cost, "expedite": opt.expedite_cost, "discount": opt.discount_cost,
            "ageing_units_b": base.total("ageing_units") + base.total("aged_units"),
            "ageing_units_o": opt.total("ageing_units") + opt.total("aged_units"),
            "end_o": opt.locs, "end_b": base.locs,
        })
    return rows


# ---------------------------------------------------------------------- breaking points
def breaking_points(e: Engine, sku: str, loc: str, d: Optional[Dict[str, Any]], pct: str) -> Dict[str, Any]:
    decs = decision_list(d)
    if not decs:
        return {"rows": [], "most_sensitive": None, "fragility_pct": None}

    def net_of(engine: Engine, ds=None) -> float:
        ds = decs if ds is None else ds
        return engine.net(sku, engine.simulate(sku, ds, pct, "likely"), engine.simulate(sku, [], pct, "likely"))["net_benefit_aed"]

    n0 = net_of(e)
    rows = []

    def bisect(f, lo, hi, it=16):
        flo, fhi = f(lo) > 0, f(hi) > 0
        if flo == fhi:
            return None
        for _ in range(it):
            m = (lo + hi) / 2
            if (f(m) > 0) == flo:
                lo = m
            else:
                hi = m
        return (lo + hi) / 2

    def lane_engine(mult=1.0, add=0):
        st = light_copy(e.state)
        for k in {(l["from"], l["to"]) for l in d.get("legs", [])}:
            st.lanes[k]["cost_per_unit_aed"] = float(st.lanes[k]["cost_per_unit_aed"]) * mult
            st.lanes[k]["lead_days"] = int(st.lanes[k]["lead_days"]) + add
        return e.derive(st, refresh_uplift=False)

    if d["type"] == "transfer":
        leg = d["legs"][0]
        cur = float(e.lane(leg["from"], leg["to"])["cost_per_unit_aed"])
        m = bisect(lambda x: net_of(lane_engine(mult=x)), 1.0, 60.0) if n0 > 0 else None
        rows.append({"variable": "Transfer cost per unit", "current": f"AED {cur:.2f}",
                     "breaks_if": f"exceeds AED {cur * m:.2f}" if m else ("already not paying back" if n0 <= 0 else "no break up to 60x"),
                     "margin_pct": round((m - 1) * 100, 1) if m else (0.0 if n0 <= 0 else None)})
        cur_lt = int(e.lane(leg["from"], leg["to"])["lead_days"])
        brk = None
        if n0 > 0:
            for add in range(1, e.H - cur_lt + 1):
                if net_of(lane_engine(add=add)) <= 0:
                    brk = cur_lt + add
                    break
        rows.append({"variable": "Transfer lead time", "current": f"{cur_lt} day{'s' if cur_lt != 1 else ''}",
                     "breaks_if": f"reaches {brk} days" if brk else "no break inside 30 days",
                     "margin_pct": round((brk - cur_lt) / max(cur_lt, 1) * 100, 1) if brk else None})
        for lg in d["legs"]:
            spare = e.spare(sku, lg["from"])
            thr = floor_pack(spare, e.state.skus[sku]["pack_size"])
            rows.append({"variable": f"{lg['from']} remaining stock", "current": f"{lg['qty']} units leave",
                         "breaks_if": f"more than {thr} units leave {lg['from']} (P90 demand reaches its safety stock)",
                         "margin_pct": round((thr - lg["qty"]) / max(lg["qty"], 1) * 100, 1), "kind": "safety"})
    if d["type"] in ("reorder", "bring_forward"):
        brk = None
        if n0 > 0:
            for add in range(1, e.H + 1):
                st = light_copy(e.state)
                st.order_delay_add = int(getattr(st, "order_delay_add", 0)) + add
                if d["type"] == "bring_forward":
                    for po in st.inbound:
                        if po["po_id"] == d["po_id"]:
                            po["eta_day"] += add
                if net_of(e.derive(st, refresh_uplift=False)) <= 0:
                    brk = add
                    break
        arr = arrival_day(e, d)
        rows.append({"variable": "Arrival delay", "current": f"arrives day {arr}",
                     "breaks_if": f"it arrives {brk} or more days late (day {arr + brk})" if brk else "no break inside 30 days",
                     "margin_pct": round(brk / max(arr or 1, 1) * 100, 1) if brk else None})
    if d["type"] == "discount":
        u0 = float(d["uplift_pct"])
        u = bisect(lambda x: net_of(e, [dict(d, uplift_pct=x)]), 0.0, max(u0, 1.0) * 4)
        rows.append({"variable": "Assumed sell-through uplift", "current": f"{u0:g}% (Assumption)",
                     "breaks_if": f"falls below {u:.0f}%" if u is not None and u < u0 else
                     (f"must exceed {u:.0f}% to pay off" if u is not None else "no break in tested range"),
                     "margin_pct": round((u0 - u) / max(u0, 1) * 100, 1) if u is not None else None})
    # destination demand
    base_total = float(e.demand(sku, loc, pct).sum())
    def demand_engine(k):
        cells = dict(e.fc)
        c = cells[(sku, loc)]
        cells[(sku, loc)] = replace(c, base=c.base * k)
        return Engine(e.state, {"cells": cells, "meta": e.fc_meta})
    if n0 > 0 and d["type"] != "discount":
        k = bisect(lambda x: net_of(demand_engine(x)), 1.0, 0.02)
        rows.append({"variable": f"{loc} 30-day demand", "current": f"{base_total:.0f} units",
                     "breaks_if": f"falls below {base_total * k:.0f} units" if k else "no break down to 2% of forecast",
                     "margin_pct": round((1 - k) * 100, 1) if k else None})
    scored = [r for r in rows if r.get("margin_pct") is not None]
    most = min(scored, key=lambda r: r["margin_pct"]) if scored else None
    return {"rows": rows, "net_base_case": round(n0, 2), "most_sensitive": most["variable"] if most else None,
            "fragility_pct": max(0.0, most["margin_pct"]) if most else None,
            "method": "The engine re-runs the base case across each input and reports where net benefit reaches zero."}


# ---------------------------------------------------------------------- one tab
def _pctl(xs, q):
    return float(np.percentile(np.array(xs, dtype=float), q)) if xs else 0.0


def compute(svc: Service, tab: Dict[str, Any], settings_override: Optional[Dict[str, Any]] = None,
            with_alternatives: bool = True) -> Dict[str, Any]:
    eng, plan = svc.ensure()
    sku, loc = tab["sku"], tab["location"]
    settings = validate_settings(eng, sku, settings_override or tab["settings"])
    d = tab.get("decision")
    d = None if not d or d.get("type") == "do_nothing" else d
    key = stable_hash([CACHE_VERSION, eng.sku_input_hash(sku), settings, d, sku, loc, with_alternatives])
    if key in _MEM:
        return _MEM[key]
    ev = events_for(eng, sku)
    e_on = sandbox(eng, sku, settings, True)
    delays_set = settings["shipment_delay_days"] or settings["supply_shortage"] != "none" or settings["route_delay"] != "none"
    e_off = sandbox(eng, sku, settings, False) if delays_set else e_on
    p = settings["delay_chance_pct"] / 100.0
    e_base = e_on if p >= 0.5 else e_off
    pct = LEVEL_PCT[settings["demand"]]
    decs = decision_list(d)
    tloc = target_location(d, loc, eng) if d else loc
    margin = eng.state.margin(sku)

    seed = stable_hash([CACHE_VERSION, eng.sku_input_hash(sku), settings, d, sku, loc])  # same draws with or without alternatives
    rows = simulate_paths(eng, e_on, e_off, sku, decs, settings, seed)
    mean = lambda f: float(np.mean([f(r) for r in rows]))
    nets = [r["net"] for r in rows]
    stockout = {l: {"before": round(100 * mean(lambda r: r["out_b"][l]), 1), "after": round(100 * mean(lambda r: r["out_o"][l]), 1)}
                for l in eng.locs}
    gains = {l: mean(lambda r: r["sold_delta"][l]) for l in eng.locs}

    # base case for charts, timing and checks
    b0 = e_base.simulate(sku, [], pct, "likely")
    b1 = e_base.simulate(sku, decs, pct, "likely")
    free0 = eng.state.free(sku, loc)
    start_o = e_base.flows(sku, loc, "likely", decs)[0]
    band_lo = [start_o] + [_pctl([r["end_o"][loc].end_daily[t] for r in rows], 10) for t in range(e_base.H)]
    band_hi = [start_o] + [_pctl([r["end_o"][loc].end_daily[t] for r in rows], 90) for t in range(e_base.H)]
    dem = e_base.demand(sku, loc, pct)
    ss = eng.state.safety.get((sku, loc), 0)
    lost_days_o = [t + 1 for t, x in enumerate(b1.locs[loc].lost_daily) if x > LOST_EPS]
    lost_days_b = [t + 1 for t, x in enumerate(b0.locs[loc].lost_daily) if x > LOST_EPS]
    arr = arrival_day(e_base, d)
    arr_off = arrival_day(e_off, d) if delays_set else arr
    restores = None
    if d and arr:
        for t in range(max(arr, 1), e_base.H + 1):
            if b1.locs[tloc].end_daily[t - 1] >= ss - EPS:
                restores = t
                break

    # rule and robustness under the tab's settings
    if decs:
        chk = e_base.check(sku, decs, [], pct)
        rob = e_base.robustness(sku, decs)
        failing = list(chk["failing_checks"]) + (["Harmful"] if rob["label"] == "Harmful" else [])
        checks = {"economic": chk["economic"], "safe": chk["safe"], "feasible": chk["feasible"],
                  "robustness": rob["label"], "robustness_failing_cases": rob["failing_cases"], "passes": not failing,
                  "failing": failing, "reason": cand.rejection_text(chk, rob) if failing else None}
    else:
        checks = None

    bp = breaking_points(e_base, sku, tloc, d, pct)
    fc = eng.fc[(sku, loc)]
    bridge = []
    pos = sum(v for v in gains.values() if v > 0)
    if pos:
        bridge.append({"label": "Lost sales avoided", "value": round(pos, 2)})
    for l, v in gains.items():
        if v < -0.5:
            bridge.append({"label": f"New {l} risk", "value": round(v, 2)})
    dh = mean(lambda r: r["hold_b"] - r["hold_o"])
    bridge.append({"label": "Holding cost saved" if dh >= 0 else "Extra holding cost", "value": round(dh, 2)})
    da = mean(lambda r: r["age_b"] - r["age_o"])
    if abs(da) > 0.5:
        bridge.append({"label": "Ageing cost saved" if da >= 0 else "Extra ageing cost", "value": round(da, 2)})
    for k, lab in (("transfer", "Transfer cost"), ("expedite", "Expedite cost"), ("discount", "Discount margin given up")):
        v = mean(lambda r: r[k])
        if v > 0.5:
            bridge.append({"label": lab, "value": round(-v, 2)})
    net_mean = mean(lambda r: r["net"])
    order_value = (d["qty"] * eng.state.skus[sku]["unit_cost"]) if d and d["type"] == "reorder" else 0.0

    hist = fc.history
    oos = [i + 1 for i in np.where(hist.oos)[0]]
    miss = [i + 1 for i in np.where(hist.missing)[0]]
    bands = (eng.fc_meta.get("bands") or {}).get(eng.state.skus[sku]["category"], {})
    p50 = fc.total("P50")
    dq = {
        "bars": [{"day": i + 1, "units": None if hist.missing[i] else float(hist.observed[i]),
                  "kind": "missing" if hist.missing[i] else "oos" if hist.oos[i] else "data"} for i in range(eng.state.history_days)],
        "oos_days": oos, "missing_days": miss,
        "correction_pct": round((fc.corrected_total - fc.observed_total) / fc.observed_total * 100, 1) if oos and fc.observed_total else 0.0,
        "band": [round(fc.total("P10")), round(fc.total("P90"))],
        "clean_band": [round(p50 * bands.get("p10_multiplier", fc.m10)), round(p50 * bands.get("p90_multiplier", fc.m90))]
        if fc.confidence == "Low" else None,
        "confidence": fc.confidence, "detail": fc.confidence_detail,
    }
    notes = []
    if miss:
        notes.append(f"{len(miss)} missing sales days")
    if oos:
        notes.append(f"{len(oos)} stockout days corrected")
    if fc.confidence == "Low":
        notes.append("interval widened")

    lane_rows = []
    if d and d["type"] == "transfer":
        for lg in d["legs"]:
            ln = e_base.lane(lg["from"], lg["to"])
            lane_rows.append({"label": f"Lane {lg['from']} to {lg['to']}", "tag": "Data" if not settings["transfer_cost_change_pct"] and not settings["shipment_delay_days"] else "Assumption",
                              "value": f"AED {ln['fixed_cost_aed']:.0f} + {ln['cost_per_unit_aed']:.2f}/unit, {ln['lead_days']} day(s)"})
    sev = {"none": "off", "mild": "mild (min delay)", "likely": "likely delay", "severe": "severe (max delay)"}
    assumptions = (
        [{"label": f"{l} free stock", "value": f"{eng.state.free(sku, l)} units", "tag": "Data",
          "source": (eng.state.lineage.get(eng.state.inventory[(sku, l)]["record_id"]) or {}).get("source_file")} for l in eng.locs]
        + [{"label": f"{loc} demand, 30 days", "value": f"P10 {fc.total('P10'):.0f} / P50 {fc.total('P50'):.0f} / P90 {fc.total('P90'):.0f}", "tag": "Calculated"},
           {"label": "Margin per unit", "value": f"AED {margin:.2f}", "tag": "Data", "source": "sku_master.csv"}]
        + lane_rows
        + [{"label": "Shipment delay", "value": f"+{settings['shipment_delay_days']} days", "tag": "Assumption"},
           {"label": "Demand sensitivity", "value": f"{settings['demand']} ({pct} level)", "tag": "Assumption"},
           {"label": "Chance the delay happens", "value": f"{settings['delay_chance_pct']:g}%", "tag": "Assumption"}]
        + [{"label": f"{s['signal_id']} supply shortage", "value": f"{sev[settings['supply_shortage']]} · {s['status']}", "tag": "Assumption"} for s in ev["component"]]
        + [{"label": f"{s['signal_id']} route delay", "value": f"{sev[settings['route_delay']]} · {s['status']}", "tag": "Assumption"} for s in ev["route"]]
        + ([{"label": "Sell-through uplift while discounted", "value": f"{d['uplift_pct']:g}% (no price-elasticity data)", "tag": "Assumption"}] if d and d["type"] == "discount" else [])
        + [{"label": "Simulated demand paths", "value": f"{N_PATHS}, drawn from each cell's P10–P90 band", "tag": "Calculated"}]
    )
    events = []
    for kind, setting_key in (("component", "supply_shortage"), ("route", "route_delay")):
        for s in ev[kind]:
            imp = sigmod.impact(eng.state, s["signal_id"])
            hit = sorted({x["sku"] for x in imp["skus"] if x["affected"]})
            events.append({"signal_id": s["signal_id"], "date": str(s["date"]), "source": s["source"], "kind": kind,
                           "status": s["status"], "setting": settings[setting_key], "active": settings[setting_key] != "none",
                           "matches": ", ".join(s.get("components") or s.get("routes") or []),
                           "affected_skus": hit, "untouched": len(eng.state.skus) - len(hit),
                           "delay_range": [s.get("delay_min"), s.get("delay_likely"), s.get("delay_max")]})

    res = {
        "sku": sku, "sku_name": eng.state.skus[sku]["name"], "location": loc, "target_location": tloc,
        "decision": d, "label": cand.label(eng, d) if d else "Do nothing", "settings": settings,
        "chips": chips(settings, ev), "events": events, "paths": N_PATHS, "basis_pct": pct,
        "summary": {
            "net": round(net_mean, 2), "net_range": [round(_pctl(nets, 10), 2), round(_pctl(nets, 90), 2)],
            "stockout_focus": stockout[loc], "lost_value": {"before": round(margin * mean(lambda r: r["lost_b"]), 2),
                                                           "after": round(margin * mean(lambda r: r["lost_o"]), 2)},
            "lost_units": {"before": round(mean(lambda r: r["lost_b"]), 1), "after": round(mean(lambda r: r["lost_o"]), 1)},
            "new_risk_elsewhere": any(stockout[l]["after"] > stockout[l]["before"] + 0.5 for l in eng.locs if l != loc),
            "confidence": fc.confidence, "confidence_note": ", ".join(notes) or "clean history",
        },
        "stockout": stockout,
        "projection": {"days": list(range(0, e_base.H + 1)), "option": [free0 if not decs else start_o] + [round(x, 1) for x in b1.locs[loc].end_daily],
                       "do_nothing": [free0] + [round(x, 1) for x in b0.locs[loc].end_daily],
                       "band_low": [round(x, 1) for x in band_lo], "band_high": [round(x, 1) for x in band_hi],
                       "safety_stock": ss, "arrival_day": arr if tloc == loc else None, "arrival_day_without_event": arr_off if tloc == loc else None,
                       "stockout_day_do_nothing": lost_days_b[0] if lost_days_b else None,
                       "lost_window": [lost_days_o[0], lost_days_o[-1]] if lost_days_o else None,
                       "lost_days": len(lost_days_o), "lost_days_do_nothing": len(lost_days_b)},
        "sales": {"demand_cum": [0] + [round(x, 1) for x in np.cumsum(dem)],
                  "do_nothing_cum": [0] + [round(x, 1) for x in np.cumsum(b0.locs[loc].sold_daily)],
                  "option_cum": [0] + [round(x, 1) for x in np.cumsum(b1.locs[loc].sold_daily)],
                  "option_total": round(b1.locs[loc].sold, 1), "do_nothing_total": round(b0.locs[loc].sold, 1)},
        "bridge": bridge, "bridge_net": round(net_mean, 2),
        "costs": {"transfer": round(mean(lambda r: r["transfer"]), 2), "expedite": round(mean(lambda r: r["expedite"]), 2),
                  "discount_margin_lost": round(mean(lambda r: r["discount"]), 2), "order_value": round(order_value, 2),
                  "holding_before": round(mean(lambda r: r["hold_b"]), 2), "holding_after": round(mean(lambda r: r["hold_o"]), 2),
                  "total": round(mean(lambda r: r["transfer"] + r["expedite"] + r["discount"]), 2)},
        "ageing": {"units_before": round(mean(lambda r: r["ageing_units_b"]), 1), "units_after": round(mean(lambda r: r["ageing_units_o"]), 1),
                   "cost_before": round(mean(lambda r: r["age_b"]), 2), "cost_after": round(mean(lambda r: r["age_o"]), 2)},
        "timing": {"arrival_day": arr, "arrival_day_without_event": arr_off, "restores_safety_day": restores,
                   "stockout_day_do_nothing": lost_days_b[0] if lost_days_b else None},
        "checks": checks, "breaking_points": bp, "assumptions": assumptions, "data_quality": dq,
        "input_hash": eng.sku_input_hash(sku), "computed_at": now_iso(),
    }
    if with_alternatives:
        res["alternatives"] = alternatives(eng, e_base, sku, loc, pct, d)
    _MEM[key] = res
    if len(_MEM) > 300:
        _MEM.pop(next(iter(_MEM)))
    return res


def alternatives(eng: Engine, e: Engine, sku: str, loc: str, pct: str, current: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    status = cand.cell_status(e, sku, loc)
    spare = {l: e.spare(sku, l) for l in e.locs}
    ds = cand.destination_candidates(e, sku, loc, spare) if status["breach_day"] is not None else cand.excess_candidates(e, sku, loc, status)
    if any(f in status["flags"] for f in ("Ageing", "Aged", "Excess")):
        units = status["ageing_units"] + status["aged_units"] or int(status["excess_units"])
        units = min(units, e.state.free(sku, loc))
        if units > 0:
            ds.append({"type": "discount", "sku": sku, "location": loc, "units": int(units), "depth_pct": 15.0, "uplift_pct": 20.0})
    out, seen = [], set()
    cur_id = cand.option_id(current) if current else None
    for x in ds:
        if x["type"] in ("wait", "flag_clearance"):
            continue
        oid = cand.option_id(x)
        if oid in seen:
            continue
        seen.add(oid)
        c = e.check(sku, [x], [], pct)
        out.append({"label": cand.label(e, x), "decision": x, "net": c["economic"]["net_benefit_aed"],
                    "passes": c["passes"], "failing": c["failing_checks"], "current": oid == cur_id,
                    "assumption": x["type"] == "discount"})
    out.sort(key=lambda a: (not a["passes"], -a["net"]))
    return out[:8]


# ---------------------------------------------------------------------- facts and messages
def facts(res: Dict[str, Any]) -> Dict[str, Any]:
    s, loc = res["summary"], res["location"]
    F, R = [], []

    def f(label, value, unit=None, tag="Estimate"):
        F.append({"id": f"F{len(F) + 1}", "label": label, "value": value, "unit": unit, "tag": tag})
        return F[-1]["id"]

    ids = {"label": f("Decision", res["label"]), "net": f("Expected net benefit vs do nothing", round(s["net"]), "AED"),
           "lo": f("Net benefit range low (P10)", round(s["net_range"][0]), "AED"),
           "hi": f("Net benefit range high (P90)", round(s["net_range"][1]), "AED"),
           "sb": f(f"{loc} stockout risk, do nothing", round(s["stockout_focus"]["before"]), "%"),
           "sa": f(f"{loc} stockout risk, with decision", round(s["stockout_focus"]["after"]), "%")}
    bp = res["breaking_points"]
    if bp.get("most_sensitive"):
        row = next(r for r in bp["rows"] if r["variable"] == bp["most_sensitive"])
        ids["bp"] = f("Most sensitive assumption", f"{row['variable']}: breaks if {row['breaks_if']}")
    better = [a for a in res.get("alternatives", []) if a["passes"] and not a["current"] and a["net"] > (bp.get("net_base_case") or 0) + 1]
    if better:
        ids["alt"] = f("Higher-scoring alternative", better[0]["label"])
        ids["alt_label"] = better[0]["label"]
    if res["checks"] and not res["checks"]["passes"]:
        R.append({"id": f"R{len(R) + 1}", "text": res["checks"]["reason"] or "fails a check", "tag": "Estimate"})
    for e in res["events"]:
        if e["active"]:
            R.append({"id": f"R{len(R) + 1}", "text": f"{e['signal_id']} applied at {e['setting']} severity", "tag": "Assumption" if e["status"] != "Confirmed" else "Data"})
    return {"facts": F, "reasons": R, "ids": ids}


def template(res: Dict[str, Any], fx: Dict[str, Any]) -> str:
    s, i, loc = res["summary"], fx["ids"], res["location"]
    if not res["decision"]:
        return (f"Doing nothing leaves a {round(s['stockout_focus']['before'])}% stockout risk at {loc} [{i['sb']}].")
    parts = [f"Done. Net benefit is about AED {round(s['net'])} against doing nothing [{i['net']}], "
             f"range AED {round(s['net_range'][0])} to {round(s['net_range'][1])} [{i['lo']}, {i['hi']}]."]
    parts.append(f"Stockout risk at {loc} goes from {round(s['stockout_focus']['before'])}% to {round(s['stockout_focus']['after'])}% [{i['sb']}, {i['sa']}].")
    if "alt" in i:
        parts.append(f"I also checked alternatives: {i['alt_label']} scores higher on the base case [{i['alt']}].")
    elif "bp" in i:
        parts.append(f"Watch the most sensitive assumption [{i['bp']}].")
    return " ".join(parts)


# ---------------------------------------------------------------------- tab store
class Lab:
    def __init__(self, svc: Service):
        self.svc = svc

    def _row(self, tab_id: str) -> Dict[str, Any]:
        rows = self.svc.store.query("SELECT * FROM lab_tabs WHERE tab_id = ?", [tab_id])
        if not rows:
            raise HITLError(f"Tab {tab_id} not found", 404)
        return self._load(rows[0])

    @staticmethod
    def _load(r):
        for k in ("decision", "settings", "chat", "results", "changelog"):
            r[k] = json.loads(r[k]) if r.get(k) else ([] if k in ("chat", "changelog") else None)
        return r

    def _save(self, t: Dict[str, Any]) -> None:
        t["updated_at"] = now_iso()
        self.svc.store.execute("DELETE FROM lab_tabs WHERE tab_id = ?", [t["tab_id"]])
        self.svc.store.insert("lab_tabs", {k: t.get(k) for k in (
            "tab_id", "sku", "location", "letter", "name", "decision", "settings", "status", "source", "chat", "results",
            "cache_key", "changelog", "forked_from", "status_reason", "decision_id", "created_at", "updated_at")})

    def tabs(self, sku: str, loc: str) -> List[Dict[str, Any]]:
        rows = self.svc.store.query("SELECT * FROM lab_tabs WHERE sku = ? AND location = ?", [sku, loc])
        order = lambda r: (r["letter"] != "0", len(r["letter"]), r["letter"])
        return [self._load(r) for r in sorted(rows, key=order)]

    def _next_letter(self, sku, loc) -> str:
        used = {t["letter"] for t in self.tabs(sku, loc)}
        for i in range(26 * 3):
            l = chr(65 + i % 26) + ("" if i < 26 else str(i // 26))
            if l not in used:
                return l
        return uuid.uuid4().hex[:2].upper()

    def _new(self, sku, loc, decision, settings, source, name=None, letter=None, forked_from=None) -> Dict[str, Any]:
        t = {"tab_id": f"TAB-{uuid.uuid4().hex[:8]}", "sku": sku, "location": loc,
             "letter": letter or self._next_letter(sku, loc), "decision": decision, "settings": settings,
             "status": "Draft", "source": source, "chat": [], "results": None, "cache_key": None, "changelog": [],
             "forked_from": forked_from, "status_reason": None, "decision_id": None, "created_at": now_iso()}
        t["name"] = name or short_name(decision)
        self._save(t)
        return t

    def workspace(self, sku: str, loc: str) -> Dict[str, Any]:
        with self.svc.lock:
            eng, plan = self.svc.ensure()
            if (sku, loc) not in eng.state.inventory:
                raise HITLError(f"Unknown SKU-location {sku} / {loc}", 404)
            tabs = self.tabs(sku, loc)
            if not tabs:
                self._new(sku, loc, {"type": "do_nothing", "sku": sku, "location": loc}, default_settings(eng, sku),
                          "Baseline", name="Do nothing", letter="0")
                res = plan["results"].get((sku, loc))
                if res is None:
                    status = cand.cell_status(eng, sku, loc)
                    res = cand.cell_options(eng, sku, loc, {l: eng.spare(sku, l) for l in eng.locs}, status)
                ranked = [o for o in res["options"] if o["status"] in ("Recommended", "Alternative")]
                ranked += [o for o in res["options"] if o["status"] == "Rejected"]
                for o in ranked[:2]:
                    self._new(sku, loc, o["decision"], default_settings(eng, sku), "Suggested")
                tabs = self.tabs(sku, loc)
            out = [self.refresh(t, summary_only=True) for t in tabs]
            return {"sku": sku, "location": loc, "sku_name": eng.state.skus[sku]["name"], "planning_day": str(eng.state.day90),
                    "plan_input_hash": eng.sku_input_hash(sku), "keyed": self.svc.store is not None and _keyed(),
                    "tabs": out, "events": events_summary(eng, sku)}

    def refresh(self, t: Dict[str, Any], summary_only: bool = False) -> Dict[str, Any]:
        """Recompute a tab if its inputs changed; keep a changelog of what moved when shared data changed."""
        eng, _ = self.svc.ensure()
        res = compute(self.svc, t)
        key = stable_hash([res["input_hash"], res["settings"], res["decision"]])
        if t.get("cache_key") != key:
            old = t.get("results")
            if old and old.get("input_hash") != res["input_hash"] and old.get("settings") == res["settings"]:
                moved = _diff(old, res)
                if moved:
                    t["changelog"] = (t.get("changelog") or []) + [{"ts": now_iso(), "cause": (self.svc.last_what_changed or {}).get("cause", "shared data changed"), "changes": moved}]
            t["results"] = {k: res[k] for k in ("summary", "input_hash", "settings", "decision", "label", "timing", "checks")}
            t["cache_key"] = key
            if t["status"] not in LOCKED + ("Sent back",):
                t["status"] = "Ready" if res["checks"] and res["checks"]["passes"] else "Draft"
            self._save(t)
        brief = {k: t[k] for k in ("tab_id", "letter", "name", "status", "source", "forked_from", "status_reason", "decision_id", "created_at")}
        brief.update({"pinned": t["letter"] == "0", "label": res["label"], "net": res["summary"]["net"], "chips": res["chips"],
                      "settings": res["settings"], "changelog": t.get("changelog") or [], "decision": res["decision"]})
        if summary_only:
            return brief
        return {**brief, "results": res, "chat": t.get("chat") or []}

    def get(self, tab_id: str) -> Dict[str, Any]:
        with self.svc.lock:
            return self.refresh(self._row(tab_id))

    def create(self, sku: str, loc: str, decision_builder=None, settings=None, name=None) -> Dict[str, Any]:
        with self.svc.lock:
            eng, _ = self.svc.ensure()
            if (sku, loc) not in eng.state.inventory:
                raise HITLError(f"Unknown SKU-location {sku} / {loc}", 404)
            st = validate_settings(eng, sku, settings or {})
            d = to_decision(sandbox(eng, sku, st), {**decision_builder, "sku": sku}) if decision_builder else None
            t = self._new(sku, loc, d, st, "Controls" if d else "New", name=(name or "")[:60] or ("New decision" if not d else None))
            if not d:
                t["chat"] = [{"role": "agent", "kind": "text", "text": "What decision should this tab test? Type it in plain language, or use the form.", "ts": now_iso()}]
                self._save(t)
            self.svc.audit("lab_tab_created", t["tab_id"], None, {"sku": sku, "location": loc})
            return self.refresh(t)

    def _editable(self, t):
        if t["status"] in LOCKED:
            raise HITLError(f"This tab is {t['status']}. Fork it to test a change.", 409)
        if t["letter"] == "0" and (t.get("decision") or {}).get("type") != "do_nothing":
            raise HITLError("The Do nothing tab cannot hold a decision.", 409)

    def update(self, tab_id: str, name: Optional[str] = None, settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        with self.svc.lock:
            eng, _ = self.svc.ensure()
            t = self._row(tab_id)
            if settings is not None:
                self._editable(t)
                t["settings"] = validate_settings(eng, t["sku"], {**t["settings"], **settings})
                if t["status"] == "Sent back":
                    t["status"] = "Draft"
            if name is not None:
                if t["letter"] == "0":
                    raise HITLError("The Do nothing tab cannot be renamed.", 409)
                t["name"] = name.strip()[:60] or t["name"]
            self._save(t)
            return self.refresh(t)

    def fork(self, tab_id: str, name: Optional[str] = None) -> Dict[str, Any]:
        with self.svc.lock:
            t = self._row(tab_id)
            if t["letter"] == "0":
                raise HITLError("Fork a decision tab, not Do nothing.", 409)
            n = self._new(t["sku"], t["location"], t["decision"], t["settings"], "Fork", name=(name or f"{t['name']} (fork)")[:60], forked_from=tab_id)
            self.svc.audit("lab_tab_forked", n["tab_id"], None, {"from": tab_id})
            return self.refresh(n)

    def set_decision(self, tab_id: str, builder: Dict[str, Any], source: str = "Controls", question: Optional[str] = None) -> Dict[str, Any]:
        with self.svc.lock:
            eng, _ = self.svc.ensure()
            t = self._row(tab_id)
            self._editable(t)
            if t["letter"] == "0":
                raise HITLError("The Do nothing tab cannot hold a decision. Use + New decision.", 409)
            d = to_decision(sandbox(eng, t["sku"], t["settings"]), {**builder, "sku": builder.get("sku") or t["sku"]})
            if d["sku"] != t["sku"]:
                raise HITLError(f"This workspace is for {t['sku']}. Open the {d['sku']} workspace to test that decision.", 422)
            t["decision"] = d
            if t["source"] == "New" or t["name"] in ("New decision",):
                t["name"] = short_name(d)
            t["source"] = source if source in ("Controls", "Question") else "Controls"
            t["status"] = "Draft"
            if question:
                t["chat"] = (t.get("chat") or []) + [{"role": "user", "kind": "text", "text": question[:500], "ts": now_iso()}]
            self._save(t)
            out = self.refresh(t)
            msg = self._result_message(out["results"])
            t = self._row(tab_id)
            t["chat"] = (t.get("chat") or []) + [msg]
            self._save(t)
            self.svc.audit("lab_tab_run", tab_id, None, {"decision": d, "source": t["source"]})
            out["chat"] = t["chat"]
            return out

    def _result_message(self, res: Dict[str, Any]) -> Dict[str, Any]:
        from .llm import explain as llm
        fx = facts(res)
        tpl = template(res, fx)
        msg = {"role": "agent", "kind": "result", "template": tpl, "facts": {"facts": fx["facts"], "reasons": fx["reasons"]}, "ts": now_iso()}
        if _keyed():
            x = llm.explain(self.svc, "lab", {"facts": fx["facts"], "reasons": fx["reasons"]}, 30)
            if x.get("text"):
                msg.update({"text": x["text"], "verified": True, "badge": x.get("badge")})
        return msg

    def chat(self, tab_id: str, message: str) -> Dict[str, Any]:
        from .llm import explain as llm
        from .tree import DEFAULT_ASSUMPTIONS
        with self.svc.lock:
            t = self._row(tab_id)
            self._editable(t)
            if t["letter"] == "0":
                raise HITLError("Ask in a decision tab. Use + New decision.", 409)
            if not _keyed():
                raise HITLError("Typed questions need an API key. Use the form in this tab.", 409)
            pseudo = {"sku": t["sku"], "location": t["location"], "assumptions": dict(DEFAULT_ASSUMPTIONS)}
            t["chat"] = (t.get("chat") or []) + [{"role": "user", "kind": "text", "text": message[:500], "ts": now_iso()}]
            try:
                r = llm.parse_question(self.svc, pseudo, message)
            except HITLError as e:
                t["chat"].append({"role": "agent", "kind": "text", "text": f"I could not read that safely: {e}. Nothing ran.", "ts": now_iso()})
                self._save(t)
                return self.refresh(t)
            if not r.get("supported"):
                t["chat"].append({"role": "agent", "kind": "text", "ts": now_iso(),
                                  "text": f"{r['message']}. Missing: {r['missing_input']}. Can you tell me that, or use the form?"})
            else:
                b = r["builder"]["decision"]
                if b.get("sku") and b["sku"] != t["sku"]:
                    t["chat"].append({"role": "agent", "kind": "text", "ts": now_iso(),
                                      "text": f"That question is about {b['sku']}, but this workspace is {t['sku']}. Open the {b['sku']} workspace to test it."})
                else:
                    t["chat"].append({"role": "agent", "kind": "interpretation", "builder": b, "question": r["question_text"], "ts": now_iso(),
                                      "settings_note": "Disruption settings are set in the sidebar." if r["builder"].get("assumptions") else None})
            self._save(t)
            return self.refresh(t)

    # ------------------------------------------------------------------ review (human approval)
    def review(self, tab_id: str, action: str, reason: Optional[str], expected_input_hash: Optional[str]) -> Dict[str, Any]:
        with self.svc.lock:
            eng, plan = self.svc.ensure()
            t = self._row(tab_id)
            if t["status"] in LOCKED:
                raise HITLError(f"This tab is already {t['status']}.", 409)
            d = t.get("decision")
            if not d or d["type"] in ("do_nothing", "wait"):
                raise HITLError("There is no decision in this tab to review.", 409)
            sku = t["sku"]
            if action == "send_back":
                t["status_reason"] = require_reason(reason, "send a decision back")
                t["status"] = "Sent back"
                self._save(t)
                self.svc.audit("lab_sent_back", tab_id, t["status_reason"], {"sku": sku, "decision": d})
                return {"tab": self.refresh(t)}
            res = compute(self.svc, t)
            current = eng.sku_input_hash(sku)
            if not expected_input_hash or expected_input_hash != current or res["input_hash"] != current:
                raise HITLError("Inputs changed since this tab was computed. Review the recomputed tab before deciding.", 409,
                                {"current_input_hash": current})
            tloc = target_location(d, t["location"], eng)
            active = [x for x in self.svc.decisions() if x["status"] in planmod.ACTIVE]
            base = planmod.approved_by_sku(active).get(sku, [])
            opt = cand.evaluate(eng, sku, d, None, base)  # the SHARED plan, not the tab's sandbox settings
            options = [opt]
            if action == "reject":
                why = require_reason(reason, "reject a decision")
                receipt = receipts.build(eng, sku, tloc, opt, options, tree_assumptions={"lab_tab": tab_id, "settings": t["settings"]})
                dec = self.svc._record_decision(sku, tloc, tab_id, options, opt, "Rejected", why, receipt)
                t["status"], t["status_reason"], t["decision_id"] = "Rejected", why, dec["decision_id"]
                self._save(t)
                return {"tab": self.refresh(t), "decision": dec}
            if action != "approve":
                raise HITLError("action must be approve, reject or send_back", 422)
            if (opt.get("robustness") or {}).get("label") == "Harmful":
                raise HITLError("This decision is Harmful on the shared plan and can never be approved: " +
                                "; ".join(opt["robustness"]["failing_cases"][:3]), 409)
            if opt["status"] == "Rejected":
                raise HITLError("On the shared plan this decision fails the recommendation rule: " + (opt.get("rejection_reason") or ""), 409)
            if (opt.get("robustness") or {}).get("label") == "Fragile":
                reason = require_reason(reason, "approve a Fragile decision (failing cases: " + "; ".join(opt["robustness"]["failing_cases"][:3]) + ")")
            clash = [x for x in active if x["sku"] == sku and x["location"] == tloc]
            if clash:
                raise HITLError(f"{sku} at {tloc} already has an approved decision ({clash[0]['decision_id']}).", 409)
            defaults = default_settings(eng, sku)
            receipt = receipts.build(eng, sku, tloc, opt, options,
                                     tree_assumptions=None if t["settings"] == defaults else {"lab_tab": tab_id, "settings": t["settings"]})
            receipt["lab"] = {"tab_id": tab_id, "expected_net_aed": res["summary"]["net"], "net_range_aed": res["summary"]["net_range"],
                              "breaking_points": res["breaking_points"], "settings": t["settings"], "paths": N_PATHS}
            dec = self.svc._record_decision(sku, tloc, tab_id, options, opt, "Approved", reason, receipt)
            t["status"], t["status_reason"], t["decision_id"] = "Approved", reason, dec["decision_id"]
            self._save(t)
            wc = self.svc.replan(f"Scenario Lab approval {dec['decision_id']} ({t['name']})")
            return {"tab": self.refresh(self._row(tab_id)), "decision": dec, "what_changed": wc}

    # ------------------------------------------------------------------ compare
    def compare(self, a_id: str, b_id: str) -> Dict[str, Any]:
        with self.svc.lock:
            ta, tb = self._row(a_id), self._row(b_id)
            if ta["letter"] == "0" or tb["letter"] == "0":
                raise HITLError("Do nothing is always shown as the reference; tick two decision tabs.", 422)
            if (ta["sku"], ta["location"]) != (tb["sku"], tb["location"]):
                raise HITLError("Compare two tabs from the same workspace.", 422)
            A, B = self.refresh(ta)["results"], self.refresh(tb)["results"]
            ev = events_for(self.svc.engine, ta["sku"])
            ca, cb = describe_settings(A["settings"], ev), describe_settings(B["settings"], ev)
            mismatch = [f"{ta['letter']} assumes {ca[k].lower()}, {tb['letter']} assumes {cb[k].lower()}" for k in ca if ca.get(k) != cb.get(k)]

            def fr(r):
                return r["breaking_points"].get("fragility_pct")
            rows = []

            def row(metric, a, b, better, fmt="aed", note_a=None, note_b=None):
                diff = None if a is None or b is None else b - a
                win = None
                if a is not None and b is not None and abs(a - b) > 1e-9:
                    win = ("a" if a > b else "b") if better == "higher" else ("a" if a < b else "b")
                rows.append({"metric": metric, "a": a, "b": b, "diff": diff, "better": win, "format": fmt, "note_a": note_a, "note_b": note_b})
            loc = A["location"]
            row("Net benefit vs do nothing", A["summary"]["net"], B["summary"]["net"], "higher")
            row("Expected lost sales", A["summary"]["lost_value"]["after"], B["summary"]["lost_value"]["after"], "lower")
            row(f"{loc} stockout risk", A["summary"]["stockout_focus"]["after"], B["summary"]["stockout_focus"]["after"], "lower", "pct")
            row("Stock arrives", A["timing"]["arrival_day"], B["timing"]["arrival_day"], "lower", "day")
            row("Ageing exposure", A["ageing"]["units_after"], B["ageing"]["units_after"], "lower", "units")
            row("Holding cost, 30 days", A["costs"]["holding_after"], B["costs"]["holding_after"], "lower")
            row("Transfer or order cost", A["costs"]["total"], B["costs"]["total"], "lower",
                note_a=f"order value AED {A['costs']['order_value']:.0f} not counted" if A["costs"]["order_value"] else None,
                note_b=f"order value AED {B['costs']['order_value']:.0f} not counted" if B["costs"]["order_value"] else None)
            row("Fragility (safe margin left)", fr(A), fr(B), "higher", "pct",
                note_a=A["breaking_points"].get("most_sensitive"), note_b=B["breaking_points"].get("most_sensitive"))
            na, nb = A["summary"]["net"], B["summary"]["net"]
            win, lose, wl, ll = (A, B, ta["letter"], tb["letter"]) if na >= nb else (B, A, tb["letter"], ta["letter"])
            verdict = [f"{wl} beats {ll} by AED {abs(na - nb):,.0f}."]
            so = lose["timing"]["stockout_day_do_nothing"]
            if lose["timing"]["arrival_day"] and so and lose["timing"]["arrival_day"] > so:
                verdict.append(f"{ll} arrives on day {lose['timing']['arrival_day']}, after the day-{so} stockout.")
            fa, fb = fr(A), fr(B)
            if fa is not None and fb is not None:
                frag, fl = (A, ta["letter"]) if fa < fb else (B, tb["letter"])
                ms = frag["breaking_points"]["most_sensitive"]
                rowx = next((r for r in frag["breaking_points"]["rows"] if r["variable"] == ms), None)
                if rowx:
                    verdict.append(f"{fl} is the more fragile option: it breaks if {rowx['breaks_if']}.")
            scales = {"stock_max": max(max(A["projection"]["band_high"] + A["projection"]["do_nothing"] + [A["projection"]["safety_stock"]]),
                                       max(B["projection"]["band_high"] + B["projection"]["do_nothing"] + [B["projection"]["safety_stock"]])),
                      "sales_max": max(A["sales"]["demand_cum"][-1], B["sales"]["demand_cum"][-1], A["sales"]["option_cum"][-1], B["sales"]["option_cum"][-1])}
            return {"a": {**self.refresh(ta, True), "results": A}, "b": {**self.refresh(tb, True), "results": B},
                    "verdict": " ".join(verdict), "mismatch": mismatch, "rows": rows, "scales": scales}

    # ------------------------------------------------------------------ event impact
    def event_impact(self, sku: str, loc: str) -> Dict[str, Any]:
        with self.svc.lock:
            out = []
            for t in self.tabs(sku, loc):
                s_on = t["settings"]
                if s_on["supply_shortage"] == "none" and s_on["route_delay"] == "none":
                    continue
                on = compute(self.svc, t, with_alternatives=False)
                off = compute(self.svc, t, {**s_on, "supply_shortage": "none", "route_delay": "none"}, with_alternatives=False)
                a0, a1 = off["timing"]["arrival_day"], on["timing"]["arrival_day"]
                so = on["timing"]["stockout_day_do_nothing"]
                n0, n1 = off["summary"]["net"], on["summary"]["net"]
                ld0, ld1 = off["projection"]["lost_days"], on["projection"]["lost_days"]
                internal = bool(t.get("decision") and t["decision"].get("type") == "transfer")
                if not t.get("decision") or t["decision"].get("type") == "do_nothing":
                    s0, s1 = off["summary"]["stockout_focus"]["before"], on["summary"]["stockout_focus"]["before"]
                    comment = (f"Stockout risk at {loc} {'rises' if s1 > s0 + 0.5 else 'stays'} {s0:.0f}% → {s1:.0f}%: "
                               f"{'no replenishment arrives in time either way' if s1 >= 50 else 'risk shifts with the delay'}.")
                elif a1 != a0 and a1 is not None:
                    comment = f"Arrives day {a1} (was day {a0})" + (f", {a1 - so} days after the day-{so} stockout" if so and a1 > so else "") + \
                              (f". Lost-sales window grows from {ld0} to {ld1} days." if ld1 > ld0 else ".")
                elif abs(n1 - n0) < max(50, abs(n0) * 0.03):
                    comment = "Unchanged. Internal stock only." if internal else "Unchanged."
                else:
                    comment = f"Net benefit {'falls' if n1 < n0 else 'rises'} from AED {n0:,.0f} to AED {n1:,.0f}."
                alert = None
                if t.get("decision") and t["decision"].get("type") not in ("do_nothing", None) and (n1 < n0 - max(50, abs(n0) * 0.1) or (a1 and so and a1 > so and (a0 or 0) <= so)):
                    alert = (f"{t['name']} now arrives on day {a1} (was day {a0}), after the predicted stockout on day {so}. " if a1 != a0 and so and a1 and a1 > so else
                             f"{t['name']} is hit by the event. ") + f"Net benefit falls from AED {n0:,.0f} to AED {n1:,.0f}."
                out.append({"tab_id": t["tab_id"], "letter": t["letter"], "name": t["name"], "net_without": n0, "net_with": n1,
                            "arrival_without": a0, "arrival_with": a1, "comment": comment, "alert": alert, "internal_transfer": internal})
            unaffected = [r for r in out if r["internal_transfer"] and r["comment"].startswith("Unchanged")]
            for r in out:
                if r["alert"] and unaffected:
                    r["alert"] += " " + " ".join(f"{u['letter']}: {u['name']} is unaffected because that stock is already in a UAE warehouse." for u in unaffected[:2])
            return {"sku": sku, "location": loc, "rows": out}


def _diff(old, new) -> List[str]:
    out = []
    o, n = old["summary"], new["summary"]
    if round(o["net"]) != round(n["net"]):
        out.append(f"Net benefit AED {o['net']:,.0f} → {n['net']:,.0f}")
    if round(o["stockout_focus"]["after"]) != round(n["stockout_focus"]["after"]):
        out.append(f"Stockout risk {o['stockout_focus']['after']:.0f}% → {n['stockout_focus']['after']:.0f}%")
    if round(o["lost_value"]["after"]) != round(n["lost_value"]["after"]):
        out.append(f"Expected lost sales AED {o['lost_value']['after']:,.0f} → {n['lost_value']['after']:,.0f}")
    return out


def events_summary(eng: Engine, sku: str) -> List[Dict[str, Any]]:
    ev = events_for(eng, sku)
    out = []
    for kind in ("component", "route"):
        for s in ev[kind]:
            imp = sigmod.impact(eng.state, s["signal_id"])
            hit = sorted({x["sku"] for x in imp["skus"] if x["affected"]})
            out.append({"signal_id": s["signal_id"], "kind": kind, "date": str(s["date"]), "source": s["source"],
                        "status": s["status"], "include": s.get("include", True),
                        "matches": ", ".join(s.get("components") or s.get("routes") or []),
                        "affected_skus": hit, "untouched": len(eng.state.skus) - len(hit),
                        "delay_range": [s.get("delay_min"), s.get("delay_likely"), s.get("delay_max")]})
    return out


def _keyed() -> bool:
    from .config import settings
    return settings.keyed
