"""Scenario Lab decision tree (S1-S7, design doc sections 5 and 6.9).

Everything here is a sandbox: tree-wide assumptions and sandbox stock corrections never
change the shared plan. Only approving a path writes decisions, and approval re-checks
the path against the shared plan, not the sandbox.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import replace
from typing import Any, Dict, List, Optional, Tuple

from . import candidates as cand
from . import plan as planmod
from .engine import PCTS, Engine, floor_pack
from .service import HITLError, Service, require_reason
from .storage import now_iso

DEFAULT_ASSUMPTIONS = {"demand": "P50", "signals": {}, "lanes": [], "stock_corrections": []}
DECISION_TYPES = ("transfer", "split", "reorder", "bring_forward", "reduce_inbound", "delay_inbound", "wait", "discount")


# ---------------------------------------------------------------------- sandbox engine
def validate_assumptions(eng: Engine, a: Dict[str, Any]) -> Dict[str, Any]:
    st = eng.state
    out = {**DEFAULT_ASSUMPTIONS, **(a or {})}
    if out["demand"] not in PCTS:
        raise HITLError("demand must be P10, P50 or P90", 422)
    ids = {s["signal_id"]: s for s in st.signals}
    for sid, ch in (out["signals"] or {}).items():
        if sid not in ids:
            raise HITLError(f"Unknown signal {sid}", 422)
        allowed = {"include"} | ({"delay_min", "delay_likely", "delay_max"} if ids[sid]["type"] == "supply"
                                 else {"uplift_min", "uplift_likely", "uplift_max"})
        bad = set(ch) - allowed
        if bad:
            raise HITLError(f"{sid}: cannot set {', '.join(sorted(bad))}", 422)
    for l in out["lanes"] or []:
        if (l.get("from"), l.get("to")) not in st.lanes:
            raise HITLError(f"Unknown lane {l.get('from')} -> {l.get('to')}", 422)
        for k in ("fixed_cost_aed", "cost_per_unit_aed", "lead_days"):
            if k in l and (l[k] is None or float(l[k]) < 0):
                raise HITLError(f"lane {k} must be >= 0", 422)
    for c in out["stock_corrections"] or []:
        if (c.get("sku"), c.get("location")) not in st.inventory:
            raise HITLError(f"Unknown SKU-location {c.get('sku')} / {c.get('location')}", 422)
        av = int(c.get("available", st.inventory[(c["sku"], c["location"])]["available"]))
        rs = int(c.get("reserved", st.inventory[(c["sku"], c["location"])]["reserved"]))
        if av < 0 or rs < 0 or rs > av:
            raise HITLError("sandbox stock correction needs 0 <= reserved <= available", 422)
    return out


def sandbox_engine(eng: Engine, a: Dict[str, Any]) -> Engine:
    if a == DEFAULT_ASSUMPTIONS or (not a["signals"] and not a["lanes"] and not a["stock_corrections"]):
        return eng
    st = eng.state.copy()
    st.sandbox = True
    for s in st.signals:
        for k, v in (a["signals"].get(s["signal_id"]) or {}).items():
            s[k] = bool(v) if k == "include" else float(v)
    for l in a["lanes"]:
        lane = st.lanes[(l["from"], l["to"])]
        for k in ("fixed_cost_aed", "cost_per_unit_aed", "lead_days"):
            if k in l:
                lane[k] = int(l[k]) if k == "lead_days" else float(l[k])
    for c in a["stock_corrections"]:
        inv = st.inventory[(c["sku"], c["location"])]
        inv["available"] = int(c.get("available", inv["available"]))
        inv["reserved"] = int(c.get("reserved", inv["reserved"]))
    return eng.derive(st)


# ---------------------------------------------------------------------- builder -> engine decision
def to_decision(eng: Engine, b: Dict[str, Any]) -> Dict[str, Any]:
    st = eng.state
    t = b.get("type")
    sku = b.get("sku")
    if t not in DECISION_TYPES:
        raise HITLError(f"type must be one of {', '.join(DECISION_TYPES)}", 422)
    if sku not in st.skus:
        raise HITLError(f"Unknown SKU {sku}", 422)
    locs = set(eng.locs)

    def po_of(pid):
        po = next((p for p in st.inbound if p["po_id"] == pid), None)
        if po is None or po["sku"] != sku:
            raise HITLError(f"{pid} is not an inbound order for {sku}", 422)
        return po

    if t in ("transfer", "split"):
        legs = b.get("legs") or ([{"from": b.get("from"), "to": b.get("to"), "qty": b.get("qty")}])
        clean = []
        for l in legs:
            if l.get("from") not in locs or l.get("to") not in locs or l["from"] == l["to"]:
                raise HITLError("transfer legs need two different valid locations", 422)
            q = int(l.get("qty") or 0)
            if q <= 0:
                raise HITLError("transfer quantity must be positive", 422)
            clean.append({"from": l["from"], "to": l["to"], "qty": q})
        return {"type": "transfer", "variant": "split" if len(clean) > 1 else "single", "sku": sku, "legs": clean}
    if t == "reorder":
        loc = b.get("location")
        if loc not in locs:
            raise HITLError(f"Unknown location {loc}", 422)
        q = int(b.get("qty") or 0)
        if q <= 0:
            raise HITLError("reorder quantity must be positive", 422)
        od = b.get("order_day")
        if od is None:
            plan = cand.reorder_plan(eng, sku, loc)
            od = plan["decision"]["order_day"] if plan else 0
        if not (0 <= int(od) <= eng.H):
            raise HITLError("order_day must be within the horizon", 422)
        return {"type": "reorder", "sku": sku, "location": loc, "qty": q, "order_day": int(od)}
    if t == "bring_forward":
        po = po_of(b.get("po_id"))
        days = int(b.get("days") or po.get("expedite_days") or 0)
        if days <= 0:
            raise HITLError("days must be positive", 422)
        return {"type": "bring_forward", "sku": sku, "po_id": po["po_id"], "days": days}
    if t == "reduce_inbound":
        po = po_of(b.get("po_id"))
        nq = int(b.get("new_qty", -1))
        if not (0 <= nq < po["qty"]):
            raise HITLError(f"new_qty must be between 0 and {po['qty'] - 1}", 422)
        return {"type": "reduce_inbound", "sku": sku, "po_id": po["po_id"], "new_qty": nq}
    if t == "delay_inbound":
        po = po_of(b.get("po_id"))
        days = int(b.get("days") or 0)
        if days <= 0:
            raise HITLError("days must be positive", 422)
        return {"type": "delay_inbound", "sku": sku, "po_id": po["po_id"], "days": days}
    loc = b.get("location")
    if loc not in locs:
        raise HITLError(f"Unknown location {loc}", 422)
    if t == "discount":
        units = int(b.get("units") or 0)
        free = st.free(sku, loc)
        if not (0 < units <= free):
            raise HITLError(f"units must be between 1 and the {free} free units at {loc}", 422)
        depth = float(b.get("depth_pct") if b.get("depth_pct") is not None else 15)
        uplift = float(b.get("uplift_pct") if b.get("uplift_pct") is not None else 20)
        if not (0 < depth <= 90):
            raise HITLError("depth_pct must be above 0 and at most 90", 422)
        if not (0 <= uplift <= 300):
            raise HITLError("uplift_pct must be between 0 and 300", 422)
        return {"type": "discount", "sku": sku, "location": loc, "units": units, "depth_pct": depth, "uplift_pct": uplift}
    return {"type": "wait", "sku": sku, "location": loc, "po_id": b.get("po_id")}


def cap_against_path(eng: Engine, d: Dict[str, Any], ancestors: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[str]]:
    """A transfer drawing on spare already used higher up the path is capped to what is left (S1)."""
    if d["type"] != "transfer":
        return d, []
    sku = d["sku"]
    pack = eng.state.skus[sku]["pack_size"]
    flags = []
    legs = []
    for leg in d["legs"]:
        used = sum(l["qty"] for a in ancestors if a["type"] == "transfer" and a["sku"] == sku
                   for l in a["legs"] if l["from"] == leg["from"])
        if used > 0:
            left = floor_pack(eng.spare(sku, leg["from"], ancestors), pack)
            if leg["qty"] > left:
                flags.append(f"capped {leg['from']} leg from {leg['qty']} to {left}: {used} units already allocated higher up the path")
                leg = dict(leg, qty=left)
        if leg["qty"] > 0:
            legs.append(leg)
    if not legs:
        raise HITLError("No spare left at the source after the decisions higher up the path.", 409, {"flags": flags})
    return dict(d, legs=legs, variant="split" if len(legs) > 1 else d.get("variant", "single")), flags


# ---------------------------------------------------------------------- node evaluation
def evaluate_path(eng: Engine, path: List[Dict[str, Any]], parent: List[Dict[str, Any]], pct: str,
                  base_decisions: Dict[str, List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    base_decisions = base_decisions or {}
    skus = sorted({d["sku"] for d in path}) or []
    out = {"per_sku": {}, "totals": {}, "rule": {"passes": True, "failing_checks": [], "details": []}}
    worst = "Robust"
    rank = {"Robust": 0, "Fragile": 1, "Harmful": 2}
    totals = {"units_sold": 0.0, "lost_sales": 0.0, "excess_units": 0.0, "aged_units": 0.0, "holding_cost_aed": 0.0,
              "transfer_cost_aed": 0.0, "net_vs_do_nothing_aed": 0.0, "net_vs_parent_aed": 0.0}
    failing_cases = []
    for sku in skus:
        base = list(base_decisions.get(sku, []))
        mine = [d for d in path if d["sku"] == sku]
        mine_parent = [d for d in parent if d["sku"] == sku]
        root = eng.simulate(sku, base, pct, "likely")
        node = eng.simulate(sku, base + mine, pct, "likely")
        par = eng.simulate(sku, base + mine_parent, pct, "likely")
        n_root = eng.net(sku, node, root)
        n_par = eng.net(sku, node, par)
        chk = eng.check(sku, mine, base, pct)
        # feasibility decision by decision: each leg must fit the spare left by everything above it
        feas = []
        for i, d in enumerate(mine):
            c = eng.check(sku, [d], base + mine[:i], pct)
            feas += c["feasible"]["failures"]
        rob = eng.robustness(sku, mine, base) if mine else {"label": "Robust", "failing_cases": [], "cases": []}
        if rank[rob["label"]] > rank[worst]:
            worst = rob["label"]
        failing_cases += [f"{sku}: {c}" for c in rob["failing_cases"]]
        failing = [n for n, ok in (("Economic", chk["economic"]["pass"]), ("Safe", chk["safe"]["pass"]),
                                   ("Feasible", not feas)) if not ok]
        if failing:
            out["rule"]["passes"] = False
            out["rule"]["failing_checks"] += [f"{sku}: {f}" for f in failing]
        out["rule"]["details"].append({"sku": sku, "economic": chk["economic"], "safe": chk["safe"],
                                       "feasible": {"pass": not feas, "failures": feas}})
        s = node.summary()
        out["per_sku"][sku] = {"results": s, "do_nothing": root.summary(), "parent": par.summary(),
                               "net_vs_do_nothing": n_root, "net_vs_parent": n_par, "robustness": rob,
                               "picked_batches": node.picked_batches}
        totals["units_sold"] += s["units_sold"]
        totals["lost_sales"] += s["lost_sales"]
        totals["excess_units"] += s["excess_units"]
        totals["aged_units"] += s["aged_units"]
        totals["holding_cost_aed"] += s["holding_cost_aed"]
        totals["transfer_cost_aed"] += s["transfer_cost_aed"]
        totals["net_vs_do_nothing_aed"] += n_root["net_benefit_aed"]
        totals["net_vs_parent_aed"] += n_par["net_benefit_aed"]
    out["totals"] = {k: round(v, 2) for k, v in totals.items()}
    out["robustness"] = worst
    out["robustness_failing_cases"] = failing_cases
    return out


def _root_results(eng: Engine, sku: str, pct: str) -> Dict[str, Any]:
    s = eng.simulate(sku, [], pct, "likely").summary()
    return {"per_sku": {sku: {"results": s}}, "totals": {"units_sold": s["units_sold"], "lost_sales": s["lost_sales"],
            "excess_units": s["excess_units"], "aged_units": s["aged_units"], "holding_cost_aed": s["holding_cost_aed"],
            "transfer_cost_aed": 0.0, "net_vs_do_nothing_aed": 0.0, "net_vs_parent_aed": 0.0},
            "robustness": None, "rule": None}


# ---------------------------------------------------------------------- tree store
class Trees:
    def __init__(self, svc: Service):
        self.svc = svc

    def _tree(self, tree_id: str) -> Dict[str, Any]:
        rows = self.svc.store.query("SELECT * FROM scenario_trees WHERE tree_id = ?", [tree_id])
        if not rows:
            raise HITLError(f"Tree {tree_id} not found", 404)
        t = rows[0]
        t["assumptions"] = json.loads(t["assumptions"])
        return t

    def _nodes(self, tree_id: str) -> List[Dict[str, Any]]:
        rows = self.svc.store.query("SELECT * FROM scenario_nodes WHERE tree_id = ? ORDER BY created_at, node_id", [tree_id])
        for r in rows:
            r["decision"] = json.loads(r["decision"]) if r["decision"] else None
            r["results"] = json.loads(r["results"]) if r["results"] else None
        return rows

    def _path(self, nodes: Dict[str, Dict[str, Any]], node_id: str) -> List[Dict[str, Any]]:
        chain = []
        n = nodes.get(node_id)
        while n and n["parent_id"]:
            chain.append(n)
            n = nodes.get(n["parent_id"])
        return list(reversed(chain))

    def _compute(self, eng: Engine, tree: Dict[str, Any], nodes: List[Dict[str, Any]], node: Dict[str, Any]) -> Dict[str, Any]:
        sbx = sandbox_engine(eng, tree["assumptions"])
        pct = tree["assumptions"]["demand"]
        by_id = {n["node_id"]: n for n in nodes}
        by_id[node["node_id"]] = node
        if not node["parent_id"]:
            res = _root_results(sbx, tree["sku"], pct)
        else:
            chain = self._path(by_id, node["node_id"])
            path = [n["decision"] for n in chain]
            res = evaluate_path(sbx, path, path[:-1], pct)
            res["flags"] = node.get("_flags") or (node.get("results") or {}).get("flags", [])
        res["plan_version"] = self.svc.version
        res["computed_at"] = now_iso()
        res["sandbox"] = sbx is not eng
        return res

    def create(self, sku: str, location: str, assumptions: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        eng, plan = self.svc.ensure()
        if (sku, location) not in eng.state.inventory:
            raise HITLError(f"Unknown SKU-location {sku} / {location}", 404)
        a = validate_assumptions(eng, assumptions or {})
        tree_id = f"TREE-{uuid.uuid4().hex[:8]}"
        self.svc.store.insert("scenario_trees", {"tree_id": tree_id, "sku": sku, "location": location, "assumptions": a,
                                                 "created_at": now_iso()})
        root = {"node_id": f"{tree_id}-N0", "tree_id": tree_id, "parent_id": None,
                "decision": {"type": "do_nothing", "sku": sku, "location": location}, "source": "Root",
                "question_text": None, "created_at": now_iso()}
        self._save(eng, self._tree(tree_id), [], root)
        # The engine's top two options load as first branches, labelled Suggested
        res = plan["results"].get((sku, location))
        if res is None:
            status = cand.cell_status(eng, sku, location)
            spare = {l: eng.spare(sku, l) for l in eng.locs}
            res = cand.cell_options(eng, sku, location, spare, status)
        ranked = [o for o in res["options"] if o["status"] in ("Recommended", "Alternative")]
        ranked += [o for o in res["options"] if o["status"] == "Rejected"]
        for o in ranked[:2]:
            self.add_node(tree_id, root["node_id"], o["decision"], source="Suggested", _raw=True)
        self.svc.audit("tree_created", tree_id, None, {"sku": sku, "location": location})
        return self.get(tree_id)

    def _save(self, eng, tree, nodes, node, flags: List[str] = ()) -> Dict[str, Any]:
        node["_flags"] = list(flags)
        res = self._compute(eng, tree, nodes, node)
        row = {k: node[k] for k in ("node_id", "tree_id", "parent_id", "decision", "source", "question_text", "created_at")}
        row["results"] = res
        row["robustness"] = res.get("robustness")
        row["input_hash"] = eng.sku_input_hash(tree["sku"])
        self.svc.store.execute("DELETE FROM scenario_nodes WHERE node_id = ?", [node["node_id"]])
        self.svc.store.insert("scenario_nodes", row)
        return row

    def add_node(self, tree_id: str, parent_id: str, builder: Dict[str, Any], source: str = "Controls",
                 question_text: Optional[str] = None, _raw: bool = False) -> Dict[str, Any]:
        with self.svc.lock:
            eng, _ = self.svc.ensure()
            tree = self._tree(tree_id)
            nodes = self._nodes(tree_id)
            by_id = {n["node_id"]: n for n in nodes}
            if parent_id not in by_id:
                raise HITLError(f"Parent node {parent_id} not in tree {tree_id}", 404)
            if source not in ("Suggested", "Controls", "Question"):
                raise HITLError("source must be Controls or Question", 422)
            if source == "Question" and not (question_text or "").strip():
                raise HITLError("question_text is required for a node built from a typed question", 422)
            sbx = sandbox_engine(eng, tree["assumptions"])
            d = builder if _raw else to_decision(sbx, builder)
            ancestors = [n["decision"] for n in self._path(by_id, parent_id)]
            d, flags = cap_against_path(sbx, d, ancestors)
            node = {"node_id": f"{tree_id}-N{len(nodes)}", "tree_id": tree_id, "parent_id": parent_id, "decision": d,
                    "source": source, "question_text": (question_text or "")[:500] or None, "created_at": now_iso()}
            row = self._save(eng, tree, nodes, node, flags)
            return self._public_node(eng, row)

    def set_assumptions(self, tree_id: str, assumptions: Dict[str, Any]) -> Dict[str, Any]:
        with self.svc.lock:
            eng, _ = self.svc.ensure()
            tree = self._tree(tree_id)
            a = validate_assumptions(eng, {**tree["assumptions"], **(assumptions or {})})
            self.svc.store.execute("UPDATE scenario_trees SET assumptions = ? WHERE tree_id = ?", [json.dumps(a), tree_id])
            self._recompute_all(eng, tree_id)
            return self.get(tree_id)

    def _recompute_all(self, eng, tree_id):
        tree = self._tree(tree_id)
        nodes = self._nodes(tree_id)
        for n in nodes:
            self._save(eng, tree, nodes, n, (n.get("results") or {}).get("flags", []))

    def get(self, tree_id: str) -> Dict[str, Any]:
        with self.svc.lock:
            eng, _ = self.svc.ensure()
            tree = self._tree(tree_id)
            nodes = self._nodes(tree_id)
            if any((n["results"] or {}).get("plan_version") != self.svc.version for n in nodes):
                self._recompute_all(eng, tree_id)  # shared inputs changed since the tree was computed
                nodes = self._nodes(tree_id)
            return {**tree, "plan_input_hash": eng.sku_input_hash(tree["sku"]),
                    "nodes": [self._public_node(eng, n) for n in nodes]}

    def node(self, node_id: str) -> Tuple[Dict[str, Any], Dict[str, Any], List[Dict[str, Any]]]:
        rows = self.svc.store.query("SELECT tree_id FROM scenario_nodes WHERE node_id = ?", [node_id])
        if not rows:
            raise HITLError(f"Node {node_id} not found", 404)
        tree = self.get(rows[0]["tree_id"])
        nodes = {n["node_id"]: n for n in tree["nodes"]}
        return tree, nodes[node_id], [nodes[x] for x in nodes[node_id]["path_ids"]]

    def _public_node(self, eng: Engine, n: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(n)
        out["label"] = "Do nothing" if not n["parent_id"] else cand.label(eng, n["decision"])
        rows = self.svc.store.query("SELECT node_id, parent_id FROM scenario_nodes WHERE tree_id = ?", [n["tree_id"]])
        parents = {r["node_id"]: r["parent_id"] for r in rows}
        chain, cur = [], n["node_id"]
        while cur and parents.get(cur):
            chain.append(cur)
            cur = parents[cur]
        out["path_ids"] = list(reversed(chain))
        return out

    # ------------------------------------------------------------------ S6 approve a path
    def approve(self, tree_id: str, node_id: str, expected_input_hash: str, reason: Optional[str]) -> Dict[str, Any]:
        with self.svc.lock:
            eng, _ = self.svc.ensure()
            tree, node, chain = self.node(node_id)
            if tree["tree_id"] != tree_id:
                raise HITLError("Node does not belong to this tree", 422)
            if not chain:
                raise HITLError("Choose a node below Do nothing to approve a path.", 422)
            sku = tree["sku"]
            current = eng.sku_input_hash(sku)
            if expected_input_hash != current:
                raise HITLError("Inputs changed since this tree was computed. Review the recomputed tree before approving.",
                                409, {"current_input_hash": current})
            active = [d for d in self.svc.decisions() if d["status"] in planmod.ACTIVE]
            path = [n["decision"] for n in chain]
            cells = {(d["sku"], d.get("location") or d.get("legs", [{}])[0].get("to") or tree["location"]) for d in path}
            clash = [d for d in active if (d["sku"], d["location"]) in cells]
            if clash:
                raise HITLError(f"{clash[0]['sku']} at {clash[0]['location']} already has an approved decision "
                                f"({clash[0]['decision_id']}).", 409)
            # Re-check the whole path together against the SHARED plan (never the sandbox assumptions)
            base = planmod.approved_by_sku(active)
            shared = evaluate_path(eng, path, path[:-1], "P50", base)
            if shared["robustness"] == "Harmful":
                raise HITLError("This path contains a Harmful decision and cannot be approved.", 409,
                                {"failing_cases": shared["robustness_failing_cases"]})
            if not shared["rule"]["passes"]:
                raise HITLError("This path fails the recommendation rule when evaluated together on the shared plan: " +
                                "; ".join(shared["rule"]["failing_checks"]), 409, {"rule": shared["rule"]})
            if shared["robustness"] == "Fragile":
                reason = require_reason(reason, "approve a path containing a Fragile node (failing cases: " +
                                        "; ".join(shared["robustness_failing_cases"][:5]) + ")")
            opts = []
            for n in chain:
                d = n["decision"]
                per = shared["per_sku"][d["sku"]]
                opts.append({"option_id": n["node_id"], "label": cand.label(eng, d), "decision": d,
                             "status": "Approved", "net_benefit_aed": per["net_vs_do_nothing"]["net_benefit_aed"],
                             "robustness": per["robustness"], "picked_batches": per["picked_batches"],
                             "checks": {"economic": next(x for x in shared["rule"]["details"] if x["sku"] == d["sku"])["economic"],
                                        "safe": next(x for x in shared["rule"]["details"] if x["sku"] == d["sku"])["safe"],
                                        "feasible": next(x for x in shared["rule"]["details"] if x["sku"] == d["sku"])["feasible"]}})
            siblings = [{"option_id": n["node_id"], "label": n["label"], "status": "Not chosen",
                         "net_benefit_aed": (n["results"] or {}).get("totals", {}).get("net_vs_do_nothing_aed"),
                         "robustness": (n["results"] or {}).get("robustness"), "note": "other branch in the decision tree"}
                        for n in tree["nodes"] if n["parent_id"] and n["node_id"] not in node["path_ids"]]
            decs = self.svc.record_path(sku, tree["location"], node_id, opts, opts + siblings, reason,
                                        tree["assumptions"] if tree["assumptions"] != DEFAULT_ASSUMPTIONS else None)
            wc = self.svc.replan(f"path approval {node_id}")
            return {"decisions": decs, "what_changed": wc}

    # ------------------------------------------------------------------ S7 breaking points
    def breaking_points(self, node_id: str) -> Dict[str, Any]:
        with self.svc.lock:
            eng, _ = self.svc.ensure()
            tree, node, chain = self.node(node_id)
            if not chain:
                raise HITLError("Breaking points apply to a decision node, not the root.", 422)
            sbx = sandbox_engine(eng, tree["assumptions"])
            pct = tree["assumptions"]["demand"]
            path = [n["decision"] for n in chain]
            sku, dst = tree["sku"], tree["location"]

            def net_with(e: Engine) -> float:
                return evaluate_path(e, path, path[:-1], pct)["totals"]["net_vs_do_nothing_aed"]

            def top_with(e: Engine) -> Optional[str]:
                spare = {l: e.spare(sku, l) for l in e.locs}
                status = cand.cell_status(e, sku, dst)
                ds = (cand.destination_candidates(e, sku, dst, spare) if status["breach_day"] is not None
                      else cand.excess_candidates(e, sku, dst, status))
                best, best_net = None, None
                for d in ds:
                    if d["type"] in ("wait", "flag_clearance"):
                        continue
                    c = e.check(sku, [d], [], pct, spare)
                    if c["passes"] and (best_net is None or c["economic"]["net_benefit_aed"] > best_net):
                        best, best_net = cand.label(e, d), c["economic"]["net_benefit_aed"]
                return best

            lanes_used = sorted({(l["from"], l["to"]) for d in path if d["type"] == "transfer" for l in d["legs"]})
            out = {"node_id": node_id, "current_net_aed": round(net_with(sbx), 2), "current_top_option": top_with(sbx),
                   "inputs": []}

            def lane_engine(field: str, value: float) -> Engine:
                st = sbx.state.copy()
                for k in lanes_used:
                    st.lanes[k][field] = int(round(value)) if field == "lead_days" else value
                return sbx.derive(st)

            def demand_engine(k: float) -> Engine:
                cells = dict(sbx.fc)
                c = cells[(sku, dst)]
                cells[(sku, dst)] = replace(c, base=c.base * k)
                return Engine(sbx.state, {"cells": cells, "meta": sbx.fc_meta})

            if lanes_used:
                cur = sbx.lane(*lanes_used[0])["cost_per_unit_aed"]
                out["inputs"].append(self._bisect("per-unit shipping cost (AED) on " + ", ".join(f"{a}->{b}" for a, b in lanes_used),
                                                  cur, 0.0, max(50.0, cur * 20), lambda v: lane_engine("cost_per_unit_aed", v),
                                                  net_with, top_with, out["current_top_option"]))
                curl = sbx.lane(*lanes_used[0])["lead_days"]
                out["inputs"].append(self._scan_int("transfer lead days", curl, 0, sbx.H,
                                                    lambda v: lane_engine("lead_days", v), net_with, top_with,
                                                    out["current_top_option"]))
            out["inputs"].append(self._bisect(f"{dst} demand (multiple of current forecast)", 1.0, 0.1, 3.0, demand_engine,
                                              net_with, top_with, out["current_top_option"]))
            return out

    @staticmethod
    def _bisect(name, current, lo, hi, make, net_with, top_with, top0, iters: int = 14) -> Dict[str, Any]:
        n0 = net_with(make(current))
        res = {"input": name, "current_value": round(current, 3), "net_zero_at": None, "top_option_changes_at": None}
        # net benefit reaches zero
        for a, b in ((current, hi), (lo, current)):
            na, nb = net_with(make(a)), net_with(make(b))
            if (na > 0) != (nb > 0):
                for _ in range(iters):
                    m = (a + b) / 2
                    if (net_with(make(m)) > 0) == (na > 0):
                        a = m
                    else:
                        b = m
                res["net_zero_at"] = round((a + b) / 2, 3)
                break
        # top option changes (coarse scan then refine)
        steps = [current + (hi - current) * i / 6 for i in range(1, 7)] + [current - (current - lo) * i / 6 for i in range(1, 7)]
        for v in steps:
            if top_with(make(v)) != top0:
                a, b = current, v
                for _ in range(6):
                    m = (a + b) / 2
                    if top_with(make(m)) == top0:
                        a = m
                    else:
                        b = m
                res["top_option_changes_at"] = round(b, 3)
                res["new_top_option"] = top_with(make(b))
                break
        res["net_at_current"] = round(n0, 2)
        return res

    @staticmethod
    def _scan_int(name, current, lo, hi, make, net_with, top_with, top0) -> Dict[str, Any]:
        res = {"input": name, "current_value": current, "net_zero_at": None, "top_option_changes_at": None}
        pos = net_with(make(current)) > 0
        for v in list(range(int(current) + 1, hi + 1)) + list(range(int(current) - 1, lo - 1, -1)):
            if res["net_zero_at"] is None and (net_with(make(v)) > 0) != pos:
                res["net_zero_at"] = v
            if res["top_option_changes_at"] is None:
                t = top_with(make(v))
                if t != top0:
                    res["top_option_changes_at"], res["new_top_option"] = v, t
            if res["net_zero_at"] is not None and res["top_option_changes_at"] is not None:
                break
        return res
