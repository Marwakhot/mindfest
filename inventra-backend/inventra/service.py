"""Application service: the shared plan, global changes, approvals and the audit trail.

Human-in-the-loop rules enforced here (design doc sections 3, 5 and 15):
  * Nothing executes. Approvals create Decision Receipts with staff instructions; transfers and
    purchase orders exist only as drafts.
  * Only the planner approves. The watcher and the LLM never approve, reject or execute.
  * Reject, override, Fragile approvals, corrections and signal edits all require a reason.
  * Harmful or rejected options can never be approved.
  * An approval must quote the input hash the planner saw; if the inputs changed since, the
    approval is refused (409) so a decision is never approved on numbers that are out of date.
  * Every global change re-runs the chain, reports What changed and marks affected approvals Stale.
  * Corrections never overwrite source data: they are an overlay with the original value kept.
"""
from __future__ import annotations

import json
import threading
import uuid
from typing import Any, Dict, List, Optional, Tuple

from . import candidates as cand
from . import plan as planmod
from . import receipts
from .config import settings
from .engine import Engine
from .loader import load_seed
from .schemas import SIGNAL_STATUSES, TABLES, parse_record_id
from .state import build_state, parse_value
from .storage import Store, get_store, now_iso

MAX_REASON = 500


class HITLError(Exception):
    """A request that breaks a human-in-the-loop or data rule. `status` maps to an HTTP code."""

    def __init__(self, message: str, status: int = 400, detail: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.status = status
        self.detail = detail or {}


def require_reason(reason: Optional[str], what: str) -> str:
    r = (reason or "").strip()
    if not r:
        raise HITLError(f"A reason is required to {what}.", 422)
    if len(r) > MAX_REASON:
        raise HITLError(f"Reason is limited to {MAX_REASON} characters.", 422)
    return r


def _uid(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


class Service:
    def __init__(self, store: Optional[Store] = None):
        self.store = store or get_store()
        self.lock = threading.RLock()
        self.engine: Optional[Engine] = None
        self.plan: Optional[Dict[str, Any]] = None
        self.version = 0
        self.last_what_changed: Dict[str, Any] = {}
        self.last_evaluation: Optional[Dict[str, Any]] = None

    # ------------------------------------------------------------------ audit
    def audit(self, action: str, target: str, reason: Optional[str] = None, details: Optional[Dict[str, Any]] = None,
              user: Optional[str] = None) -> None:
        self.store.insert("audit_log", {"audit_id": _uid("AUD"), "ts": now_iso(), "user": user or settings.planner_user,
                                        "action": action, "target": target, "reason": reason, "details": details or {}})

    # ------------------------------------------------------------------ plan lifecycle
    def ensure(self) -> Tuple[Engine, Dict[str, Any]]:
        with self.lock:
            if self.engine is None or self.plan is None:
                if self.store.count("loads") == 0:
                    load_seed(self.store)
                self._rebuild()
            return self.engine, self.plan

    def decisions(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        q = "SELECT * FROM decisions" + (" WHERE status = ?" if status else "") + " ORDER BY timestamp"
        rows = self.store.query(q, [status] if status else [])
        for r in rows:
            for k in ("options", "chosen", "receipt"):
                r[k] = json.loads(r[k]) if r.get(k) else None
        return rows

    def _rebuild(self) -> None:
        state = build_state(self.store)
        self.engine = Engine(state)
        self.plan = planmod.build(self.engine, self.decisions())
        self.version += 1

    def replan(self, cause: str, actor: str = None) -> Dict[str, Any]:
        """Re-run forecast, plan and impact; mark affected approvals Stale; return What changed (G2)."""
        with self.lock:
            before = planmod.snapshot(self.plan, self.engine) if self.plan else {}
            self._rebuild()
            stale = self._mark_stale(cause, actor)
            if stale:  # stale approvals release their allocation lock
                self._rebuild()
            after = planmod.snapshot(self.plan, self.engine)
            changes = planmod.diff(before, after)
            new_recs = []
            if before:
                for k, v in after["cells"].items():
                    if v["top"] and before["cells"].get(k, {}).get("top") != v["top"]:
                        new_recs.append({"cell": k, "recommendation": v["top"]})
            wc = {"cause": cause, "plan_version": self.version, "changes": changes,
                  "new_recommendations": new_recs, "decisions_turned_stale": stale,
                  "kpis_before": before.get("kpis", {}).get("after") if before else None,
                  "kpis_after": after["kpis"]["after"]}
            self.last_what_changed = wc
            return wc

    def _mark_stale(self, cause: str, actor: Optional[str]) -> List[Dict[str, Any]]:
        stale = []
        for d in [x for x in self.decisions() if x["status"] in planmod.ACTIVE]:
            current = self.engine.sku_input_hash(d["sku"])
            if current != d["input_hash"]:
                self.store.execute("UPDATE decisions SET status = 'Stale' WHERE decision_id = ?", [d["decision_id"]])
                self.audit("decision_stale", d["decision_id"], None,
                           {"cause": cause, "old_hash": d["input_hash"], "new_hash": current}, user="system")
                stale.append({"decision_id": d["decision_id"], "sku": d["sku"], "location": d["location"],
                              "label": (d["chosen"] or {}).get("label") or cand.label(self.engine, d["chosen"])})
        return stale

    def reset(self) -> Dict[str, Any]:
        with self.lock:
            loaded = load_seed(self.store)
            self.engine = self.plan = None
            self.last_what_changed = {}
            self.audit("reset", "dataset", None, {"tables": loaded})
            self.ensure()
            return {"loaded": loaded, "plan_version": self.version}

    # ------------------------------------------------------------------ corrections (G2, D4)
    def _current_value(self, table: str, keys: List[str], field: str) -> Optional[str]:
        df = self.store.current(table)
        if df.empty:
            return None
        mask = None
        for k, v in zip(TABLES[table].key, keys):
            m = df[k].astype(str) == v
            mask = m if mask is None else mask & m
        if not mask.any():
            return None
        raw = df[mask].iloc[0][field]
        prior = self.store.query("SELECT new_value FROM corrections_log WHERE record_id = ? AND field = ? "
                                 "ORDER BY timestamp DESC, correction_id DESC LIMIT 1",
                                 [f"{table}:{'|'.join(keys)}", field])
        return prior[0]["new_value"] if prior else raw

    def correct(self, record_id: str, field: str, new_value: Any, reason: str,
                evidence_record_id: Optional[str] = None) -> Dict[str, Any]:
        reason = require_reason(reason, "correct a record")
        try:
            table, keys = parse_record_id(record_id)
        except ValueError as e:
            raise HITLError(str(e), 404)
        spec = TABLES[table]
        if field not in spec.correctable:
            raise HITLError(f"{table}.{field} cannot be corrected. Correctable fields: {', '.join(spec.correctable)}", 422)
        with self.lock:
            self.ensure()
            old = self._current_value(table, keys, field)
            if old is None:
                raise HITLError(f"Record {record_id} not found", 404)
            new_text = self._validate_new_value(table, keys, field, new_value)
            if str(old) == new_text:
                raise HITLError("New value equals the current value; nothing to correct.", 422)
            evidence = self._check_evidence(table, keys, field, evidence_record_id, new_text)
            row = {"correction_id": _uid("COR"), "record_id": record_id, "field": field, "old_value": str(old),
                   "new_value": new_text, "user": settings.planner_user, "timestamp": now_iso(), "reason": reason,
                   "evidence_record_id": evidence}
            self.store.insert("corrections_log", row)
            self.audit("correction", record_id, reason, {"field": field, "old": str(old), "new": new_text, "evidence": evidence})
            wc = self.replan(f"correction {row['correction_id']} on {record_id}.{field}")
            return {"correction": row, "what_changed": wc}

    def _validate_new_value(self, table: str, keys: List[str], field: str, value: Any) -> str:
        kind = TABLES[table].columns[field]
        if table == "planning_parameters":
            name = keys[0]
            text = str(value).strip()
            try:
                if name == "confidence_thresholds":
                    a, b, c = (float(x) for x in text.split(";"))
                    if not (0 <= a <= b <= 100 and c >= 0):
                        raise ValueError
                else:
                    v = float(text)
                    if v < 0:
                        raise ValueError
                    if name == "horizon_days" and not (1 <= v <= 30 and v == int(v)):
                        raise HITLError("horizon_days must be a whole number from 1 to 30 (the forecast covers 30 days).", 422)
            except HITLError:
                raise
            except Exception:
                raise HITLError(f"Invalid value '{value}' for parameter {name}", 422)
            return text
        try:
            v = parse_value(kind, value)
        except (ValueError, TypeError) as e:
            raise HITLError(f"Invalid value for {table}.{field}: {e}", 422)
        if field in TABLES[table].nonneg and isinstance(v, (int, float)) and v < 0:
            raise HITLError(f"{field} cannot be negative", 422)
        if table == "inventory":
            other = "reserved" if field == "available" else "available"
            o = int(float(self._current_value(table, keys, other)))
            avail, res = (v, o) if field == "available" else (o, v)
            if res > avail:
                raise HITLError(f"reserved ({res}) cannot exceed available ({avail})", 422)
        if table == "inbound_orders" and field == "status" and v not in ("Confirmed", "Unconfirmed"):
            raise HITLError("status must be Confirmed or Unconfirmed", 422)
        if table == "sales_daily" and field == "in_stock_flag" and v not in (0, 1):
            raise HITLError("in_stock_flag must be 0 or 1", 422)
        if table == "sku_master" and field in ("pack_size", "moq") and v <= 0:
            raise HITLError(f"{field} must be positive", 422)
        return v.isoformat() if hasattr(v, "isoformat") else str(v)

    def _check_evidence(self, table: str, keys: List[str], field: str, evidence: Optional[str], new_text: str) -> Optional[str]:
        """A correction must cite an evidence record where one exists (section 10.1)."""
        if evidence:
            try:
                et, ek = parse_record_id(evidence)
            except ValueError as e:
                raise HITLError(f"evidence_record_id: {e}", 422)
            df = self.store.current(et)
            mask = None
            for k, v in zip(TABLES[et].key, ek):
                m = df[k].astype(str) == v
                mask = m if mask is None else mask & m
            if df.empty or not mask.any():
                raise HITLError(f"Evidence record {evidence} not found", 422)
            return evidence
        if table == "inventory" and field == "available":
            counts = self.store.current("stock_counts")
            hit = counts[(counts["sku"] == keys[0]) & (counts["location"] == keys[1])] if not counts.empty else counts
            if len(hit):
                latest = hit.sort_values("count_date").iloc[-1]
                rid = f"stock_counts:{latest['sku']}|{latest['location']}|{latest['count_date']}"
                raise HITLError("A cycle count exists for this SKU and location; cite it as evidence_record_id.", 422,
                                {"suggested_evidence_record_id": rid, "counted_units": latest["counted_units"]})
        return None

    def corrections(self) -> List[Dict[str, Any]]:
        return self.store.query("SELECT * FROM corrections_log ORDER BY timestamp, correction_id")

    # ------------------------------------------------------------------ signal edits (X1)
    SIGNAL_FIELDS = ("status", "include", "delay_min", "delay_likely", "delay_max", "uplift_min", "uplift_likely", "uplift_max")

    def edit_signal(self, signal_id: str, changes: Dict[str, Any], reason: str) -> Dict[str, Any]:
        reason = require_reason(reason, "edit a signal")
        with self.lock:
            eng, _ = self.ensure()
            sig = next((s for s in eng.state.signals if s["signal_id"] == signal_id), None)
            if sig is None:
                raise HITLError(f"Signal {signal_id} not found", 404)
            unknown = [k for k in changes if k not in self.SIGNAL_FIELDS]
            if unknown:
                raise HITLError(f"Cannot edit {', '.join(unknown)}. Editable: {', '.join(self.SIGNAL_FIELDS)}", 422)
            merged = dict(sig)
            for k, v in changes.items():
                if k == "status":
                    if v not in SIGNAL_STATUSES:
                        raise HITLError("status must be Unverified or Confirmed", 422)
                    merged[k] = v
                elif k == "include":
                    if not isinstance(v, bool):
                        raise HITLError("include must be true or false", 422)
                    merged[k] = v
                else:
                    pre = k.split("_")[0]
                    if (pre == "delay" and sig["type"] != "supply") or (pre == "uplift" and sig["type"] != "demand"):
                        raise HITLError(f"{k} does not apply to a {sig['type']} signal", 422)
                    try:
                        merged[k] = float(v)
                    except (TypeError, ValueError):
                        raise HITLError(f"{k} must be a number", 422)
                    if merged[k] < 0:
                        raise HITLError(f"{k} cannot be negative", 422)
            pre = "delay" if sig["type"] == "supply" else "uplift"
            lo, mid, hi = (merged.get(f"{pre}_{x}") for x in ("min", "likely", "max"))
            if not (lo <= mid <= hi):
                raise HITLError(f"{pre} needs min <= likely <= max", 422)
            ts = now_iso()
            applied = []
            for k, v in changes.items():
                old = sig.get(k)
                if old == merged[k]:
                    continue
                self.store.insert("signal_edits", {"edit_id": _uid("SED"), "signal_id": signal_id, "field": k,
                                                   "old_value": str(old), "new_value": str(merged[k]),
                                                   "user": settings.planner_user, "timestamp": ts, "reason": reason})
                applied.append({"field": k, "old": old, "new": merged[k]})
            if not applied:
                raise HITLError("No change: values equal the current signal.", 422)
            self.audit("signal_edit", signal_id, reason, {"changes": applied})
            wc = self.replan(f"signal edit {signal_id}: " + ", ".join(a["field"] for a in applied))
            return {"signal_id": signal_id, "applied": applied, "what_changed": wc}

    # ------------------------------------------------------------------ approvals (C2, S6, R1)
    def _find_option(self, sku: str, loc: str, option_id: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        res = self.plan["results"].get((sku, loc))
        if not res:
            raise HITLError(f"No recommendation for {sku} at {loc}", 404)
        opt = next((o for o in res["options"] if o["option_id"] == option_id), None)
        if opt is None:
            raise HITLError(f"Option {option_id} not found for {sku} at {loc}; the plan may have changed", 409)
        return opt, res["options"]

    def _guard_hash(self, sku: str, expected: Optional[str]) -> str:
        current = self.engine.sku_input_hash(sku)
        if not expected:
            raise HITLError("expected_input_hash is required: approve only what you have seen.", 422,
                            {"current_input_hash": current})
        if expected != current:
            raise HITLError("Inputs changed since this option was shown. Review the new numbers before approving.", 409,
                            {"current_input_hash": current, "what_changed": self.last_what_changed})
        return current

    def approve_option(self, sku: str, loc: str, option_id: str, expected_input_hash: str,
                       reason: Optional[str] = None) -> Dict[str, Any]:
        with self.lock:
            self.ensure()
            self._guard_hash(sku, expected_input_hash)
            opt, options = self._find_option(sku, loc, option_id)
            if opt["status"] in ("Rejected", "Baseline"):
                raise HITLError(f"'{opt['label']}' cannot be approved: {opt.get('rejection_reason') or opt.get('note')}", 409)
            rob = (opt.get("robustness") or {}).get("label")
            if rob == "Harmful":
                raise HITLError("A Harmful option can never be approved.", 409)
            if rob == "Fragile":
                reason = require_reason(reason, "approve a Fragile option (failing cases: " +
                                        "; ".join(opt["robustness"]["failing_cases"]) + ")")
            existing = [d for d in self.decisions() if d["status"] in planmod.ACTIVE and d["sku"] == sku and d["location"] == loc]
            if existing:
                raise HITLError(f"{sku} at {loc} already has an approved decision ({existing[0]['decision_id']}).", 409)
            receipt = receipts.build(self.engine, sku, loc, opt, options)
            dec = self._record_decision(sku, loc, None, options, opt, "Approved", reason, receipt)
            wc = self.replan(f"approval {dec['decision_id']}")
            return {"decision": dec, "what_changed": wc}

    def reject_option(self, sku: str, loc: str, option_id: str, reason: str) -> Dict[str, Any]:
        reason = require_reason(reason, "reject a recommendation")
        with self.lock:
            self.ensure()
            opt, options = self._find_option(sku, loc, option_id)
            receipt = receipts.build(self.engine, sku, loc, opt, options)
            dec = self._record_decision(sku, loc, None, options, opt, "Rejected", reason, receipt)
            return {"decision": dec}

    def _record_decision(self, sku, loc, node_id, options, chosen, status, reason, receipt, path_id=None) -> Dict[str, Any]:
        dec = {"decision_id": _uid("DEC"), "sku": sku, "location": loc, "node_id": node_id,
               "options": [{"option_id": o.get("option_id"), "label": o.get("label"), "status": o.get("status"),
                            "net_benefit_aed": o.get("net_benefit_aed")} for o in options],
               "chosen": {**chosen["decision"], "label": chosen["label"], "option_id": chosen.get("option_id")},
               "approver": settings.planner_user, "timestamp": now_iso(), "input_hash": receipt["input_hash"],
               "status": status, "reason": reason, "receipt": receipt, "path_id": path_id}
        self.store.insert("decisions", dec)
        self.audit(f"decision_{status.lower()}", dec["decision_id"], reason,
                   {"sku": sku, "location": loc, "label": chosen["label"], "node_id": node_id})
        return dec

    def decision(self, decision_id: str) -> Dict[str, Any]:
        rows = [d for d in self.decisions() if d["decision_id"] == decision_id]
        if not rows:
            raise HITLError(f"Decision {decision_id} not found", 404)
        return rows[0]

    def recalculate(self, decision_id: str) -> Dict[str, Any]:
        """G3: recompute a Stale decision's cell. Returns the fresh recommendation; nothing is approved."""
        with self.lock:
            eng, plan = self.ensure()
            d = self.decision(decision_id)
            if d["status"] != "Stale":
                raise HITLError(f"Decision {decision_id} is {d['status']}, not Stale", 409)
            sku, loc = d["sku"], d["location"]
            res = plan["results"].get((sku, loc))
            if res is None:
                status = cand.cell_status(eng, sku, loc)
                spare = {l: eng.spare(sku, l, planmod.approved_by_sku(self.decisions()).get(sku, [])) for l in eng.locs}
                res = cand.cell_options(eng, sku, loc, spare, status)
            top = res["top"]
            self.audit("decision_recalculated", decision_id, None, {"new_top": top["label"] if top else None})
            return {
                "decision_id": decision_id, "previous": d["chosen"], "previous_input_hash": d["input_hash"],
                "current_input_hash": eng.sku_input_hash(sku),
                "recommendation": planmod._brief(top) if top else None,
                "draft_receipt": receipts.build(eng, sku, loc, top, res["options"]) if top else None,
                "what_changed": self.last_what_changed,
                "next_step": "Approve the new recommendation (POST /decisions) to replace the Stale decision.",
            }

    # ------------------------------------------------------------------ approved paths from the tree (S6)
    def record_path(self, sku: str, loc: str, node_id: str, path_opts: List[Dict[str, Any]], options_for_receipt,
                    reason: Optional[str], tree_assumptions: Dict[str, Any]) -> List[Dict[str, Any]]:
        path_id = _uid("PATH")
        out = []
        path_decisions = [o["decision"] for o in path_opts]
        for o in path_opts:
            receipt = receipts.build(self.engine, o["decision"].get("sku", sku), o["decision"].get("location", loc), o,
                                     options_for_receipt, path_decisions, tree_assumptions)
            out.append(self._record_decision(o["decision"].get("sku", sku), o["decision"].get("location") or
                                             (o["decision"].get("legs") or [{}])[0].get("to") or loc,
                                             node_id, options_for_receipt, o, "Approved", reason, receipt, path_id))
        return out

    # ------------------------------------------------------------------ inbox events
    def events(self, limit: int = 100) -> Dict[str, Any]:
        rows = self.store.query("SELECT * FROM inbox_events ORDER BY received_at DESC LIMIT ?", [limit])
        for r in rows:
            for k in ("errors", "what_changed"):
                r[k] = json.loads(r[k]) if r.get(k) else None
        unseen = self.store.query("SELECT COUNT(*) AS n FROM inbox_events WHERE NOT seen")[0]["n"]
        return {"alert_badge": unseen, "events": rows}

    def mark_events_seen(self) -> None:
        self.store.execute("UPDATE inbox_events SET seen = TRUE")


_service: Optional[Service] = None


def get_service() -> Service:
    global _service
    if _service is None:
        _service = Service()
    return _service


def set_service(svc: Service) -> None:
    global _service
    _service = svc
