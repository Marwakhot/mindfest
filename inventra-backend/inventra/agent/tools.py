"""Agent tools: read the plan, and test decisions as Scenario Lab tabs. Nothing here approves or executes.

Every tool returns (result, note): `result` is the compact JSON the agent reads, `note` is the one-line
step shown to the planner in the trace. All numbers come from the engine; the agent only chooses
which tool to call next.
"""
from __future__ import annotations

import inspect
import json
from typing import Any, Callable, Dict, List, Tuple

from .. import candidates as cand
from ..lab import Lab, compute, events_for, sandbox, short_name, validate_settings
from ..service import HITLError, Service
from ..state import stable_hash
from ..storage import now_iso
from ..tree import to_decision

MAX_SCENARIOS = 4
BLOCKED_TYPES = ("do_nothing", "flag_clearance")


class Ctx:
    """One agent run: the workspace it may create tabs in, the tabs it touched and what it has read."""

    def __init__(self, svc: Service, sku: str, loc: str, why: str):
        self.svc, self.lab = svc, Lab(svc)
        self.sku, self.loc, self.why = sku, loc, why
        self.tabs: List[Dict[str, Any]] = []
        self.outputs: List[str] = []

    def remember_tab(self, r: Dict[str, Any]) -> None:
        if not any(t["tab_id"] == r["tab_id"] for t in self.tabs):
            self.tabs.append({k: r.get(k) for k in ("tab_id", "letter", "name", "label", "net_benefit_aed",
                                                     "passes_checks", "robustness")})


# ---------------------------------------------------------------------- helpers
def _cell(svc: Service, sku: str, loc: str) -> Dict[str, Any]:
    eng, plan = svc.ensure()
    if (sku, loc) not in eng.state.inventory:
        raise HITLError(f"Unknown SKU-location {sku} / {loc}", 404)
    return next(c for c in plan["cells"] if c["sku"] == sku and c["location"] == loc)


def brief(res: Dict[str, Any]) -> Dict[str, Any]:
    """The few numbers a planner needs from a computed tab."""
    s, c, bp = res["summary"], res["checks"] or {}, res["breaking_points"] or {}
    ms = bp.get("most_sensitive")
    row = next((r for r in bp.get("rows", []) if r["variable"] == ms), None)
    return {"label": res["label"], "net_benefit_aed": round(s["net"]),
            "net_range_aed": [round(s["net_range"][0]), round(s["net_range"][1])],
            "stockout_risk_pct": {"do_nothing": round(s["stockout_focus"]["before"]), "with_decision": round(s["stockout_focus"]["after"])},
            "lost_units": {"do_nothing": round(s["lost_units"]["before"]), "with_decision": round(s["lost_units"]["after"])},
            "new_risk_elsewhere": s["new_risk_elsewhere"], "passes_checks": c.get("passes"), "failing_checks": c.get("failing"),
            "robustness": c.get("robustness"), "rejection_reason": (c.get("reason") or None) and c["reason"][:240],
            "arrival_day": res["timing"]["arrival_day"], "stockout_day_do_nothing": res["timing"]["stockout_day_do_nothing"],
            "most_sensitive": f"{ms}: breaks if {row['breaks_if']}" if row else None, "settings": res["settings"]}


def _tab_out(ctx: Ctx, t: Dict[str, Any], reused: bool) -> Dict[str, Any]:
    out = {"tab_id": t["tab_id"], "letter": t["letter"], "name": t["name"], "reused_existing_tab": reused, **brief(t["results"])}
    ctx.remember_tab(out)
    return out


def _verdict(r: Dict[str, Any]) -> str:
    if r["passes_checks"]:
        return f"net AED {r['net_benefit_aed']:,}, {r['robustness']}"
    return "fails: " + ", ".join(r["failing_checks"] or ["a check"])


# ---------------------------------------------------------------------- tools
def get_cell(ctx: Ctx, sku: str = None, location: str = None) -> Tuple[Dict[str, Any], str]:
    sku, loc = sku or ctx.sku, location or ctx.loc
    eng, plan = ctx.svc.ensure()
    c, st, fc = _cell(ctx.svc, sku, loc), eng.state, eng.fc[(sku, loc)]
    entry = next((e for e in plan["queue"] + plan["no_action"] if e["sku"] == sku and e["location"] == loc), None)
    q = next((x for x in plan.get("questions") or [] if any(e["sku"] == sku and e["location"] == loc for e in x["effects"])), None)
    out = {"sku": sku, "name": st.skus[sku]["name"], "location": loc, "status": c["status"], "flags": c["flags"],
           "first_day_below_safety_stock": c["breach_day"],
           "first_date_below_safety_stock": str(st.plan_date(c["breach_day"])) if c["breach_day"] is not None else None,
           "projected_lost_sales_units_p50": round(c["projected_lost_sales_p50"]),
           "free_stock": st.free(sku, loc), "safety_stock": st.safety.get((sku, loc)),
           "demand_30d": {p: round(fc.total(p)) for p in ("P10", "P50", "P90")}, "forecast_confidence": fc.confidence,
           "urgency": c.get("urgency"), "approved_decision_id": c.get("approved_decision_id"),
           "plan_top_option": ({"label": entry["top"]["label"], "net_benefit_aed": round(entry["top"].get("net_benefit_aed") or 0),
                                "robustness": entry["top"].get("robustness")} if entry and entry["top"] else None),
           "open_question_for_planner": q["question"] if q else None}
    when = f", below safety stock from day {c['breach_day']}" if c["breach_day"] is not None else ""
    return out, f"Checked {sku} at {loc}: {c['status']}{when}"


def get_network(ctx: Ctx, sku: str = None) -> Tuple[Dict[str, Any], str]:
    sku = sku or ctx.sku
    eng, plan = ctx.svc.ensure()
    if sku not in eng.state.skus:
        raise HITLError(f"Unknown SKU {sku}", 404)
    st = eng.state
    locs = [{"location": l, "status": _cell(ctx.svc, sku, l)["status"], "free_stock": st.free(sku, l),
             "spare_stock": round(eng.spare(sku, l)), "demand_signals": list(eng.fc[(sku, l)].signal_ids)} for l in eng.locs]
    inbound = [{k: a[k] for k in ("po_id", "loc", "day", "qty", "delay", "signals", "status")} for a in eng.inbound(sku)]
    ev = events_for(eng, sku)
    events = [{"signal_id": s["signal_id"], "kind": kind, "status": s["status"], "included": s.get("include", True),
               "delay_days_min_likely_max": [s.get("delay_min"), s.get("delay_likely"), s.get("delay_max")]}
              for kind in ("component", "route") for s in ev[kind]]
    out = {"sku": sku, "locations": locs, "inbound_orders": inbound, "supply_events": events,
           "stress_settings_available": {"supply_shortage": bool(ev["component"]), "route_delay": bool(ev["route"])}}
    spare = [f"{l['location']} {l['spare_stock']}" for l in locs if l["spare_stock"] > 0]
    return out, f"Looked across the network for {sku}: spare stock {', '.join(spare) or 'none'}; " \
                f"{len(inbound)} inbound order(s), {len(events)} supply event(s)"


def _options(svc: Service, sku: str, loc: str) -> List[Dict[str, Any]]:
    """The engine's options for a cell: from the plan, or generated for cells the plan did not allocate."""
    eng, plan = svc.ensure()
    _cell(svc, sku, loc)
    res = plan["results"].get((sku, loc))
    if res is None:
        res = cand.cell_options(eng, sku, loc, {l: eng.spare(sku, l) for l in eng.locs}, cand.cell_status(eng, sku, loc))
    return res["options"]


def list_options(ctx: Ctx, sku: str = None, location: str = None) -> Tuple[Dict[str, Any], str]:
    sku, loc = sku or ctx.sku, location or ctx.loc
    opts = [{"option_id": o["option_id"], "type": o["decision"]["type"], "label": o["label"], "status": o["status"],
             "net_benefit_aed": round(o.get("net_benefit_aed") or 0), "robustness": (o.get("robustness") or {}).get("label"),
             "rejection_reason": (o.get("rejection_reason") or None) and o["rejection_reason"][:200]}
            for o in _options(ctx.svc, sku, loc) if o["decision"]["type"] not in BLOCKED_TYPES][:8]
    n = sum(o["status"] in ("Recommended", "Alternative") for o in opts)
    return {"sku": sku, "location": loc, "options": opts}, f"Read the engine's options: {n} pass, {len(opts) - n} rejected or other"


def _option(ctx: Ctx, option_id: str) -> Dict[str, Any]:
    o = next((o for o in _options(ctx.svc, ctx.sku, ctx.loc) if o["option_id"] == option_id), None)
    if o is None:
        raise HITLError(f"Unknown option_id {option_id}; call list_options for valid IDs", 422)
    if o["decision"]["type"] in BLOCKED_TYPES:
        raise HITLError(f"{o['label']} cannot be tested as a scenario", 422)
    return o


def create_scenario(ctx: Ctx, option_id: str = None, decision: Dict[str, Any] = None, settings: Dict[str, Any] = None,
                    name: str = None) -> Tuple[Dict[str, Any], str]:
    if len(ctx.tabs) >= MAX_SCENARIOS:
        raise HITLError(f"Scenario limit reached ({MAX_SCENARIOS} per run). Compare or finish.", 409)
    if not option_id and not decision:
        raise HITLError("Give option_id (from list_options) or decision (builder fields)", 422)
    svc, lab = ctx.svc, ctx.lab
    note = f"Created by the agent while investigating: {ctx.why}"[:500]
    with svc.lock:
        eng, _ = svc.ensure()
        st = validate_settings(eng, ctx.sku, settings or {})
        if option_id:
            d = _option(ctx, option_id)["decision"]
        else:
            d = to_decision(sandbox(eng, ctx.sku, st), {**decision, "sku": ctx.sku})
            if d["type"] in BLOCKED_TYPES:
                raise HITLError(f"{d['type']} cannot be tested as a scenario", 422)
        same = stable_hash([d, st])
        for t in lab.tabs(ctx.sku, ctx.loc):
            if t["letter"] != "0" and stable_hash([t["decision"], t["settings"]]) == same:
                r = _tab_out(ctx, lab.refresh(t), True)
                return r, f"Tab {r['letter']} already tests {r['label']}: {_verdict(r)}"
        t = lab._new(ctx.sku, ctx.loc, d, st, "Agent", name=(name or f"Agent · {short_name(d)}")[:60])
        t["chat"] = [{"role": "agent", "kind": "text", "text": note, "ts": now_iso()}]
        lab._save(t)
        svc.audit("lab_tab_created", t["tab_id"], None, {"sku": ctx.sku, "location": ctx.loc, "source": "Agent"}, user="agent")
        r = _tab_out(ctx, lab.refresh(t), False)
    return r, f"Opened tab {r['letter']} for {r['label']}: {_verdict(r)}"


def _workspace_tab(ctx: Ctx, tab_id: str) -> Dict[str, Any]:
    """A tab by id, or by the letter the agent sees in results ("C" or "tab C")."""
    ref = (tab_id or "").strip()
    letter = ref[4:].strip() if ref.lower().startswith("tab ") else ref
    t = next((x for x in ctx.lab.tabs(ctx.sku, ctx.loc) if x["letter"].upper() == letter.upper() and x["letter"] != "0"), None)
    t = t or ctx.lab._row(ref)
    if (t["sku"], t["location"]) != (ctx.sku, ctx.loc):
        raise HITLError(f"Tab {tab_id} is not in the {ctx.sku} / {ctx.loc} workspace", 422)
    return t


def stress_test(ctx: Ctx, tab_id: str, settings: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
    with ctx.svc.lock:
        t = _workspace_tab(ctx, tab_id)
        eng, _ = ctx.svc.ensure()
        s = validate_settings(eng, ctx.sku, {**t["settings"], **(settings or {})})
        res = compute(ctx.svc, t, s, with_alternatives=False)  # not saved: the tab keeps its own settings
    r = {"tab_id": t["tab_id"], "letter": t["letter"], "stressed_settings": settings, **brief(res)}
    what = ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in (settings or {}).items()) or "defaults"
    return r, f"Stress-tested tab {t['letter']} ({what}): net AED {r['net_benefit_aed']:,}, " \
              f"{ctx.loc} stockout risk {r['stockout_risk_pct']['with_decision']}%"


def compare_scenarios(ctx: Ctx, tab_a: str, tab_b: str) -> Tuple[Dict[str, Any], str]:
    tab_a, tab_b = _workspace_tab(ctx, tab_a)["tab_id"], _workspace_tab(ctx, tab_b)["tab_id"]
    c = ctx.lab.compare(tab_a, tab_b)
    whole = lambda v: round(v) if isinstance(v, float) else v  # the agent copies numbers; give it the ones a planner reads
    rows = [{"metric": r["metric"], "a": whole(r["a"]), "b": whole(r["b"]), "better": r["better"]} for r in c["rows"]]
    out = {"a": {"tab_id": tab_a, "letter": c["a"]["letter"]}, "b": {"tab_id": tab_b, "letter": c["b"]["letter"]},
           "verdict": c["verdict"], "assumption_mismatch": c["mismatch"], "rows": rows}
    return out, f"Compared tabs {c['a']['letter']} and {c['b']['letter']}: {c['verdict']}"


TOOLS: Dict[str, Callable[..., Tuple[Dict[str, Any], str]]] = {
    "get_cell": get_cell, "get_network": get_network, "list_options": list_options, "create_scenario": create_scenario,
    "stress_test": stress_test, "compare_scenarios": compare_scenarios,
}

_SETTINGS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "description": "Scenario settings. supply_shortage / route_delay only work when get_network shows a matching supply event.",
    "properties": {
        "shipment_delay_days": {"type": "integer", "minimum": 0, "maximum": 30},
        "supply_shortage": {"type": "string", "enum": ["none", "mild", "likely", "severe"]},
        "route_delay": {"type": "string", "enum": ["none", "mild", "likely", "severe"]},
        "delay_chance_pct": {"type": "number", "minimum": 0, "maximum": 100},
        "demand": {"type": "string", "enum": ["low", "expected", "high"]},
        "transfer_cost_change_pct": {"type": "number", "minimum": -50, "maximum": 300},
    },
}
_TAB = {"type": "string", "description": "tab_id from create_scenario (TAB-...) or the tab letter"}
_DECISION_DESC = (
    "Builder fields for a decision the engine did not list. One of: "
    "transfer {type,from,to,qty}; split {type,legs:[{from,to,qty}]}; reorder {type,location,qty,order_day?}; "
    "bring_forward {type,po_id,days}; reduce_inbound {type,po_id,new_qty}; delay_inbound {type,po_id,days}; "
    "wait {type,location,po_id?}; discount {type,location,units,depth_pct,uplift_pct?}. Use only IDs seen in tool results.")


def _fn(name, desc, props, required=()):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": list(required), "additionalProperties": False}}}


SPECS = [
    _fn("get_cell", "Status of one SKU at one location: free and safety stock, 30-day P10/P50/P90 demand, first day below "
        "safety stock, projected lost sales, the plan's top option, any open question for the planner. Defaults to the workspace.",
        {"sku": {"type": "string"}, "location": {"type": "string"}}),
    _fn("get_network", "The SKU across all locations: free and spare stock, inbound orders with delays, supply events "
        "(and whether stress_test can switch them on).", {"sku": {"type": "string"}}),
    _fn("list_options", "Options the engine generated for the workspace cell, with status (Recommended / Alternative / "
        "Rejected), net benefit, robustness and the rejection reason.", {"sku": {"type": "string"}, "location": {"type": "string"}}),
    _fn("create_scenario", "Test one decision as a Scenario Lab tab in the workspace (80 simulated demand paths). Prefer "
        f"option_id from list_options. At most {MAX_SCENARIOS} per run. Returns the tab letter and its results.",
        {"option_id": {"type": "string"}, "decision": {"type": "object", "description": _DECISION_DESC},
         "settings": _SETTINGS_SCHEMA, "name": {"type": "string", "maxLength": 60}}),
    _fn("stress_test", "Re-run a tab under harsher settings without changing the tab, e.g. demand high or a severe delay.",
        {"tab_id": _TAB, "settings": _SETTINGS_SCHEMA}, ["tab_id", "settings"]),
    _fn("compare_scenarios", "Compare two tabs side by side: net benefit, lost sales, stockout risk, timing, fragility.",
        {"tab_a": _TAB, "tab_b": _TAB}, ["tab_a", "tab_b"]),
    _fn("finish", "End the investigation with a briefing for the planner. You cannot approve anything; the planner decides.",
        {"answer": {"type": "string", "description": "2 to 4 short plain-English sentences: what you recommend, why, and the main risk. "
                                                      "Refer to tabs by letter. Copy every number from a tool result."},
         "recommended_tab_id": {"type": ["string", "null"]},
         "question_for_planner": {"type": ["string", "null"], "description": "One question only if an answer would change the recommendation."}},
        ["answer"]),
]


def execute(ctx: Ctx, name: str, args: Dict[str, Any]) -> Tuple[Dict[str, Any], str, bool]:
    """Run a tool. Rule breaks come back to the agent as errors it can read, never as exceptions."""
    fn = TOOLS.get(name)
    if fn is None:
        return {"error": f"unknown tool {name}"}, f"Tried an unknown tool {name}", False
    try:
        inspect.signature(fn).bind(ctx, **(args or {}))  # wrong argument names are the agent's mistake, not a crash
    except TypeError as e:
        out = {"error": f"bad arguments: {e}"}
        ctx.outputs.append(json.dumps(out))
        return out, f"{name} called with bad arguments", False
    try:
        out, note = fn(ctx, **(args or {}))
        ok = True
    except HITLError as e:
        out, note, ok = {"error": str(e)}, f"{name} refused: {e}", False
    ctx.outputs.append(json.dumps(out, default=str))
    return out, note, ok
