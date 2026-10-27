"""Inventra HTTP API (design doc section 14.3).

Every call that changes data returns a `what_changed` summary. Request bodies reject
unknown fields; no endpoint returns the LLM key, raw answer-key rows or stack traces.
"""
from __future__ import annotations

import logging
import math
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional, Union

import numpy as np
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .. import signals as sigmod
from .. import views
from ..config import settings
from ..drafts import draft_po
from ..llm import explain as llm
from ..replay import run as run_replay
from ..schemas import UPLOADABLE
from ..service import HITLError, get_service
from .. import outcomes
from ..agent import copilot, runner as agent_runner
from ..lab import Lab
from ..tree import Trees
from ..validation import raw_duplicates, report as validation_report
from ..watcher import InboxWatcher, ingest

log = logging.getLogger("inventra.api")


# ---------------------------------------------------------------------- JSON cleaning
def clean(o: Any) -> Any:
    if isinstance(o, dict):
        return {str(k) if not isinstance(k, tuple) else "|".join(map(str, k)): clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple, set)):
        return [clean(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.ndarray):
        return clean(o.tolist())
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    return o


def ok(o: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(clean(o), status_code=status)


# ---------------------------------------------------------------------- app
watcher: Optional[InboxWatcher] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global watcher
    svc = get_service()
    svc.ensure()
    for d in (settings.inbox_dir, settings.archive_dir, settings.quarantine_dir):
        d.mkdir(parents=True, exist_ok=True)
    if settings.watcher_enabled:
        watcher = InboxWatcher(svc)
        watcher.start()
    yield
    if watcher:
        watcher.stop()


app = FastAPI(title="Inventra API", version="1.0.0", lifespan=lifespan,
              description="AI-assisted inventory planning. The system recommends; the planner decides.")
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST", "PATCH"],
                   allow_headers=["Content-Type"], allow_credentials=False)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.exception_handler(HITLError)
async def hitl_handler(request: Request, exc: HITLError):
    return ok({"error": str(exc), **exc.detail}, exc.status)


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    return ok({"error": "invalid request", "details": [{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors()]}, 422)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error on %s", request.url.path)
    return ok({"error": "internal error"}, 500)  # no stack traces or internals to the client


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


Reason = Field(None, max_length=500)


# ---------------------------------------------------------------------- shared layer / settings
@app.post("/reset", tags=["shared"])
def reset():
    """G4: reload the seeded dataset. Clears decisions, corrections, trees and events."""
    return ok(get_service().reset())


@app.get("/settings/mode", tags=["shared"])
def mode():
    return ok({"mode": "keyed" if settings.keyed else "keyless",
               "typed_questions": settings.keyed, "llm_explanations": settings.keyed,
               "model": settings.llm_model if settings.keyed else None,
               "fallback_models": settings.llm_fallback_models if settings.keyed else [],
               "planner": settings.planner_user, "watcher": settings.watcher_enabled})


@app.get("/what-changed", tags=["shared"])
def what_changed():
    return ok(get_service().last_what_changed or {"changes": []})


@app.get("/audit", tags=["shared"])
def audit(limit: int = Query(200, ge=1, le=2000)):
    rows = get_service().store.query("SELECT * FROM audit_log ORDER BY ts DESC LIMIT ?", [limit])
    return ok({"audit": rows})


@app.get("/freshness", tags=["shared"])
def freshness():
    eng, _ = get_service().ensure()
    return ok(views.freshness(eng))


# ---------------------------------------------------------------------- watcher / data hub
@app.post("/data/upload/{table}", tags=["data hub"])
async def upload(table: str, file: UploadFile = File(...)):
    if table not in UPLOADABLE:
        raise HTTPException(404, f"unknown table {table}")
    data = await file.read(settings.max_upload_bytes + 1)
    if len(data) > settings.max_upload_bytes:
        raise HITLError(f"file larger than {settings.max_upload_bytes} bytes", 413)
    ev = ingest(get_service(), file.filename or f"{table}.csv", data, target_table=table, origin="upload")
    return ok(ev, 200 if ev["status"] == "Loaded" else 422 if ev["status"] == "Quarantined" else 200)


@app.get("/inbox/events", tags=["data hub"])
def inbox_events(limit: int = Query(100, ge=1, le=1000)):
    return ok(get_service().events(limit))


@app.post("/inbox/events/seen", tags=["data hub"])
def inbox_seen():
    get_service().mark_events_seen()
    return ok({"alert_badge": 0})


@app.get("/data/validation", tags=["data hub"])
def data_validation():
    svc = get_service()
    eng, _ = svc.ensure()
    rep = validation_report(eng.state)
    dups = [d for t in UPLOADABLE for d in raw_duplicates(svc.store, t)]
    rep["issues"] += dups
    if dups:
        rep["issue_counts"]["duplicates"] = len(dups)
    return ok(rep)


@app.get("/stock", tags=["data hub"])
def stock(sku: Optional[str] = None, location: Optional[str] = None):
    eng, plan = get_service().ensure()
    return ok({"rows": views.stock(eng, plan, sku, location)})


class CorrectionIn(Strict):
    record_id: str = Field(..., max_length=200)
    field: str = Field(..., max_length=60)
    new_value: Union[int, float, str, bool]
    reason: str = Field(..., max_length=500)
    evidence_record_id: Optional[str] = Field(None, max_length=200)


@app.post("/corrections", tags=["data hub"])
def corrections_post(body: CorrectionIn):
    return ok(get_service().correct(body.record_id, body.field, body.new_value, body.reason, body.evidence_record_id))


@app.get("/corrections", tags=["data hub"])
def corrections_get():
    return ok({"corrections": get_service().corrections()})


# ---------------------------------------------------------------------- forecast
@app.get("/forecast", tags=["forecast"])
def forecast(sku: Optional[str] = None, location: Optional[str] = None,
             granularity: Literal["daily", "weekly"] = "daily"):
    eng, _ = get_service().ensure()
    return ok(views.forecast(eng, sku, location, granularity == "weekly"))


@app.get("/forecast/accuracy", tags=["forecast"])
def forecast_accuracy():
    eng, _ = get_service().ensure()
    return ok(views.accuracy(eng))


# ---------------------------------------------------------------------- network
@app.get("/network/matrix", tags=["network"])
def network_matrix():
    eng, plan = get_service().ensure()
    return ok(views.matrix(eng, plan))


@app.get("/network/projection", tags=["network"])
def network_projection(sku: str, location: str):
    eng, plan = get_service().ensure()
    try:
        return ok(views.projection(eng, plan, sku, location))
    except KeyError:
        raise HTTPException(404, "unknown SKU-location")


@app.get("/network/ageing", tags=["network"])
def network_ageing():
    eng, _ = get_service().ensure()
    return ok(views.ageing_heatmap(eng))


class DraftPOIn(Strict):
    sku: str
    location: str
    qty: Optional[int] = Field(None, gt=0, le=1_000_000)
    save: bool = False
    override_reason: Optional[str] = Reason
    expected_input_hash: Optional[str] = None


@app.post("/drafts/po", tags=["network"])
def drafts_po(body: DraftPOIn):
    return ok(draft_po(get_service(), body.sku, body.location, body.qty, body.save, body.override_reason,
                       body.expected_input_hash))


# ---------------------------------------------------------------------- signals
@app.get("/signals", tags=["disruptions"])
def signals_list():
    eng, _ = get_service().ensure()
    edits = get_service().store.query("SELECT * FROM signal_edits ORDER BY timestamp")
    return ok({"signals": [sigmod.public_signal(s) for s in eng.state.signals], "edits": edits,
               "note": "Signals load only from the supplied, dated register; the LLM never creates or parses them."})


class SignalPatch(Strict):
    status: Optional[Literal["Unverified", "Confirmed"]] = None
    include: Optional[bool] = None
    delay_min: Optional[float] = Field(None, ge=0, le=365)
    delay_likely: Optional[float] = Field(None, ge=0, le=365)
    delay_max: Optional[float] = Field(None, ge=0, le=365)
    uplift_min: Optional[float] = Field(None, ge=0, le=10)
    uplift_likely: Optional[float] = Field(None, ge=0, le=10)
    uplift_max: Optional[float] = Field(None, ge=0, le=10)
    reason: str = Field(..., max_length=500)


@app.patch("/signals/{signal_id}", tags=["disruptions"])
def signals_patch(signal_id: str, body: SignalPatch):
    changes = body.model_dump(exclude_none=True, exclude={"reason"})
    if not changes:
        raise HITLError("Nothing to change", 422)
    return ok(get_service().edit_signal(signal_id, changes, body.reason))


@app.get("/signals/{signal_id}/impact", tags=["disruptions"])
def signals_impact(signal_id: str):
    svc = get_service()
    eng, _ = svc.ensure()
    try:
        imp = sigmod.impact(eng.state, signal_id)
    except KeyError:
        raise HTTPException(404, f"unknown signal {signal_id}")
    # X3: before and after risk for each affected SKU at the likely delay
    on_state, off_state = eng.state.copy(), eng.state.copy()
    for st_, inc in ((on_state, True), (off_state, False)):
        for s in st_.signals:
            if s["signal_id"] == signal_id:
                s["include"] = inc
    on, off = eng.derive(on_state), eng.derive(off_state)
    affected = sorted({x["sku"] for x in imp["skus"] if x["affected"]})
    risk = []
    for sku in affected:
        a, b = on.simulate(sku, []), off.simulate(sku, [])
        for l in eng.locs:
            risk.append({"sku": sku, "location": l, "basis": "P50 demand, likely delay, Do nothing",
                         "without_signal": {"lost_sales": round(b.locs[l].lost, 1), "days_below_safety": b.locs[l].below_ss_days,
                                            "breach_day": off.breach_day(sku, l)},
                         "with_signal": {"lost_sales": round(a.locs[l].lost, 1), "days_below_safety": a.locs[l].below_ss_days,
                                         "breach_day": on.breach_day(sku, l)},
                         "tag": "Assumption" if imp["signal"]["status"] != "Confirmed" else "Estimate"})
    imp["risk_before_after"] = risk
    return ok(imp)


# ---------------------------------------------------------------------- command center
@app.get("/plan", tags=["command center"])
def plan_get():
    eng, plan = get_service().ensure()
    return ok({k: v for k, v in plan.items() if k not in ("results",)})


# ---------------------------------------------------------------------- scenario lab
class TreeIn(Strict):
    sku: str
    location: str
    assumptions: Optional[Dict[str, Any]] = None


@app.post("/scenario/trees", tags=["scenario lab"])
def tree_create(body: TreeIn):
    return ok(Trees(get_service()).create(body.sku, body.location, body.assumptions))


@app.get("/scenario/trees/{tree_id}", tags=["scenario lab"])
def tree_get(tree_id: str):
    return ok(Trees(get_service()).get(tree_id))


class AssumptionsIn(Strict):
    demand: Optional[Literal["P10", "P50", "P90"]] = None
    signals: Optional[Dict[str, Dict[str, Union[bool, float]]]] = None
    lanes: Optional[List[Dict[str, Union[str, float, int]]]] = None
    stock_corrections: Optional[List[Dict[str, Union[str, int]]]] = None


@app.patch("/scenario/trees/{tree_id}/assumptions", tags=["scenario lab"])
def tree_assumptions(tree_id: str, body: AssumptionsIn):
    return ok(Trees(get_service()).set_assumptions(tree_id, body.model_dump(exclude_none=True)))


class Leg(Strict):
    from_: str = Field(..., alias="from")
    to: str
    qty: int = Field(..., gt=0, le=1_000_000)
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Builder(Strict):
    type: Literal["transfer", "split", "reorder", "bring_forward", "reduce_inbound", "delay_inbound", "wait"]
    sku: str
    from_: Optional[str] = Field(None, alias="from")
    to: Optional[str] = None
    qty: Optional[int] = Field(None, gt=0, le=1_000_000)
    legs: Optional[List[Leg]] = None
    location: Optional[str] = None
    order_day: Optional[int] = Field(None, ge=0, le=30)
    po_id: Optional[str] = None
    days: Optional[int] = Field(None, gt=0, le=365)
    new_qty: Optional[int] = Field(None, ge=0)
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class NodeIn(Strict):
    parent_id: str
    decision: Builder
    source: Literal["Controls", "Question"] = "Controls"
    question_text: Optional[str] = Field(None, max_length=500)


@app.post("/scenario/trees/{tree_id}/nodes", tags=["scenario lab"])
def tree_node(tree_id: str, body: NodeIn):
    b = body.decision.model_dump(by_alias=True, exclude_none=True)
    return ok(Trees(get_service()).add_node(tree_id, body.parent_id, b, body.source, body.question_text))


class ParseIn(Strict):
    tree_id: str
    question: str = Field(..., max_length=500)


@app.post("/scenario/parse", tags=["scenario lab"])
def scenario_parse(body: ParseIn):
    svc = get_service()
    tree = Trees(svc).get(body.tree_id)
    return ok(llm.parse_question(svc, tree, body.question))


@app.get("/scenario/nodes/{node_id}/breaking-points", tags=["scenario lab"])
def breaking_points(node_id: str):
    return ok(Trees(get_service()).breaking_points(node_id))


@app.get("/explain/recommendation", tags=["scenario lab"])
def explain_recommendation(sku: str, location: str):
    svc = get_service()
    eng, plan = svc.ensure()
    res = plan["results"].get((sku, location))
    if not res or not res["top"]:
        raise HTTPException(404, "no recommendation for this SKU-location")
    facts = llm.facts_for_option(eng, sku, location, res["top"], res["options"])
    return ok(llm.explain(svc, "recommendation", facts, eng.H))


@app.get("/explain/{node_id}", tags=["scenario lab"])
def explain_node(node_id: str):
    svc = get_service()
    eng, _ = svc.ensure()
    tree, node, _chain = Trees(svc).node(node_id)
    facts = llm.facts_for_node(eng, tree, node)
    return ok(llm.explain(svc, "node", facts, eng.H))


# ---------------------------------------------------------------------- decisions
class DecisionIn(Strict):
    action: Literal["approve", "reject", "approve_path"]
    sku: Optional[str] = None
    location: Optional[str] = None
    option_id: Optional[str] = None
    tree_id: Optional[str] = None
    node_id: Optional[str] = None
    expected_input_hash: Optional[str] = None
    reason: Optional[str] = Reason


@app.post("/decisions", tags=["decisions"])
def decisions_post(body: DecisionIn):
    svc = get_service()
    if body.action == "approve_path":
        if not (body.tree_id and body.node_id):
            raise HITLError("tree_id and node_id are required to approve a path", 422)
        return ok(Trees(svc).approve(body.tree_id, body.node_id, body.expected_input_hash, body.reason))
    if not (body.sku and body.location and body.option_id):
        raise HITLError("sku, location and option_id are required", 422)
    if body.action == "approve":
        return ok(svc.approve_option(body.sku, body.location, body.option_id, body.expected_input_hash, body.reason))
    return ok(svc.reject_option(body.sku, body.location, body.option_id, body.reason))


@app.get("/decisions", tags=["decisions"])
def decisions_get(status: Optional[Literal["Approved", "Rejected", "Overridden", "Stale"]] = None):
    rows = get_service().decisions(status)
    return ok({"decisions": [{k: v for k, v in d.items() if k != "receipt"} for d in rows]})


@app.get("/decisions/{decision_id}", tags=["decisions"])
def decision_get(decision_id: str):
    return ok(get_service().decision(decision_id))


@app.post("/decisions/{decision_id}/recalculate", tags=["decisions"])
def decision_recalc(decision_id: str):
    return ok(get_service().recalculate(decision_id))


# ---------------------------------------------------------------------- evaluation
@app.post("/evaluation/replay", tags=["evaluation"])
def evaluation_replay():
    return ok(run_replay(get_service()))


@app.get("/evaluation/report", tags=["evaluation"])
def evaluation_report():
    rep = getattr(get_service(), "last_evaluation", None)
    if not rep:
        raise HTTPException(404, "no replay run yet: POST /evaluation/replay")
    return ok(rep)


# ---------------------------------------------------------------------- scenario lab tabs (one decision per tab)
class LabBuilder(Strict):
    type: Literal["transfer", "split", "reorder", "bring_forward", "reduce_inbound", "delay_inbound", "wait", "discount"]
    sku: Optional[str] = None
    from_: Optional[str] = Field(None, alias="from")
    to: Optional[str] = None
    qty: Optional[int] = Field(None, gt=0, le=1_000_000)
    legs: Optional[List[Leg]] = None
    location: Optional[str] = None
    order_day: Optional[int] = Field(None, ge=0, le=30)
    po_id: Optional[str] = None
    days: Optional[int] = Field(None, gt=0, le=365)
    new_qty: Optional[int] = Field(None, ge=0)
    units: Optional[int] = Field(None, gt=0, le=1_000_000)
    depth_pct: Optional[float] = Field(None, gt=0, le=90)
    uplift_pct: Optional[float] = Field(None, ge=0, le=300)
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class LabSettings(Strict):
    shipment_delay_days: Optional[int] = Field(None, ge=0, le=30)
    supply_shortage: Optional[Literal["none", "mild", "likely", "severe"]] = None
    route_delay: Optional[Literal["none", "mild", "likely", "severe"]] = None
    delay_chance_pct: Optional[float] = Field(None, ge=0, le=100)
    demand: Optional[Literal["low", "expected", "high"]] = None
    transfer_cost_change_pct: Optional[float] = Field(None, ge=-50, le=300)


class LabTabIn(Strict):
    sku: str
    location: str
    name: Optional[str] = Field(None, max_length=60)
    decision: Optional[LabBuilder] = None
    settings: Optional[LabSettings] = None


class LabTabPatch(Strict):
    name: Optional[str] = Field(None, max_length=60)
    settings: Optional[LabSettings] = None


class LabDecisionIn(Strict):
    decision: LabBuilder
    source: Literal["Controls", "Question"] = "Controls"
    question_text: Optional[str] = Field(None, max_length=500)


class LabChatIn(Strict):
    message: str = Field(..., min_length=1, max_length=500)


class LabReviewIn(Strict):
    action: Literal["approve", "reject", "send_back"]
    reason: Optional[str] = Reason
    expected_input_hash: Optional[str] = None


def _b(x):
    return x.model_dump(by_alias=True, exclude_none=True) if x is not None else None


@app.get("/lab/workspace", tags=["scenario lab tabs"])
def lab_workspace(sku: str, location: str):
    return ok(Lab(get_service()).workspace(sku, location))


@app.post("/lab/tabs", tags=["scenario lab tabs"])
def lab_create(body: LabTabIn):
    return ok(Lab(get_service()).create(body.sku, body.location, _b(body.decision), _b(body.settings), body.name))


@app.get("/lab/tabs/{tab_id}", tags=["scenario lab tabs"])
def lab_tab(tab_id: str):
    return ok(Lab(get_service()).get(tab_id))


@app.patch("/lab/tabs/{tab_id}", tags=["scenario lab tabs"])
def lab_patch(tab_id: str, body: LabTabPatch):
    return ok(Lab(get_service()).update(tab_id, body.name, _b(body.settings)))


@app.post("/lab/tabs/{tab_id}/fork", tags=["scenario lab tabs"])
def lab_fork(tab_id: str):
    return ok(Lab(get_service()).fork(tab_id))


@app.post("/lab/tabs/{tab_id}/decision", tags=["scenario lab tabs"])
def lab_decision(tab_id: str, body: LabDecisionIn):
    return ok(Lab(get_service()).set_decision(tab_id, _b(body.decision), body.source, body.question_text))


@app.post("/lab/tabs/{tab_id}/chat", tags=["scenario lab tabs"])
def lab_chat(tab_id: str, body: LabChatIn):
    return ok(Lab(get_service()).chat(tab_id, body.message))


@app.post("/lab/tabs/{tab_id}/review", tags=["scenario lab tabs"])
def lab_review(tab_id: str, body: LabReviewIn):
    return ok(Lab(get_service()).review(tab_id, body.action, body.reason, body.expected_input_hash))


@app.get("/lab/compare", tags=["scenario lab tabs"])
def lab_compare(a: str, b: str):
    return ok(Lab(get_service()).compare(a, b))


@app.get("/lab/event-impact", tags=["scenario lab tabs"])
def lab_event_impact(sku: str, location: str):
    return ok(Lab(get_service()).event_impact(sku, location))


# ---------------------------------------------------------------------- decision log, outcome review, track record
class SuggestionReviewIn(Strict):
    action: Literal["approve", "reject", "send_back"]
    reason: Optional[str] = Reason


@app.get("/decision-log", tags=["decision log"])
def decision_log():
    return ok(outcomes.log(get_service()))


@app.get("/decision-log/{rid}", tags=["decision log"])
def decision_log_record(rid: str):
    return ok(outcomes.record(get_service(), rid))


@app.post("/decision-log/{rid}/explain", tags=["decision log"])
def decision_log_explain(rid: str):
    return ok(outcomes.explain(get_service(), rid))


@app.get("/track-record", tags=["decision log"])
def track_record():
    return ok(outcomes.track_record(get_service()))


@app.post("/suggestions/{sid}/review", tags=["decision log"])
def suggestion_review(sid: str, body: SuggestionReviewIn):
    return ok(outcomes.act_on_suggestion(get_service(), sid, body.action, body.reason))


# ---------------------------------------------------------------------- agents (copilot and watcher agent); they never approve
class AgentAskIn(Strict):
    sku: str
    location: str
    question: Optional[str] = Field(None, max_length=500)


@app.post("/agent/copilot", tags=["agents"])
def agent_copilot(body: AgentAskIn):
    """Start the copilot in a Lab workspace. Returns the queued run; poll GET /agent/runs/{id} for the live trace."""
    return ok(copilot.start(get_service(), body.sku, body.location, body.question), 202)


@app.get("/agent/runs", tags=["agents"])
def agent_runs(sku: Optional[str] = None, location: Optional[str] = None,
               kind: Optional[Literal["copilot", "watcher"]] = None, limit: int = Query(20, ge=1, le=100)):
    return ok({"runs": agent_runner.list_runs(get_service(), sku, location, kind, limit)})


@app.get("/agent/runs/{run_id}", tags=["agents"])
def agent_run(run_id: str):
    return ok(agent_runner.get_run(get_service(), run_id))
