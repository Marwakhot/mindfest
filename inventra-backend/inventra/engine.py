"""Calculation engine (design doc section 6). Every number shown anywhere comes from here.

Decision objects (plain dicts) used throughout:
  {"type": "transfer", "sku", "legs": [{"from", "to", "qty"}], "variant": "single" | "split" | "naive"}
  {"type": "reorder", "sku", "location", "qty", "order_day"}
  {"type": "bring_forward", "sku", "po_id", "days"}
  {"type": "reduce_inbound", "sku", "po_id", "new_qty"}
  {"type": "delay_inbound", "sku", "po_id", "days"}
  {"type": "wait", "sku", "location", "po_id"}
  {"type": "flag_clearance", "sku", "location"}
  {"type": "discount", "sku", "location", "units", "depth_pct", "uplift_pct"}   # Scenario Lab; uplift is an Assumption
  {"type": "do_nothing", "sku", "location"}
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np

from . import signals as sigmod
from .forecast import CellForecast, forecast_all
from .schemas import LOCATIONS
from .state import State, stable_hash

EPS = 1e-6
LOST_EPS = 0.5  # half a unit: below this, fractional simulated demand is not counted as a lost sale
PCTS = ("P10", "P50", "P90")
LEVELS = ("min", "likely", "max")
NO_EFFECT = ("wait", "flag_clearance", "do_nothing")
_BATCH_INDEX: Dict[int, Tuple[Any, Dict]] = {}


def ceil_pack(q: float, pack: int) -> int:
    return int(math.ceil(max(0.0, q) / pack - 1e-9) * pack)


def floor_pack(q: float, pack: int) -> int:
    return int(math.floor(max(0.0, q) / pack + 1e-9) * pack)


@dataclass
class LocResult:
    sold: float = 0.0
    lost: float = 0.0
    lost_daily: List[float] = field(default_factory=list)
    sold_daily: List[float] = field(default_factory=list)
    end_daily: List[float] = field(default_factory=list)
    below_ss_days: int = 0
    breach_days: int = 0      # 0 < free < safety stock
    shortage_days: int = 0
    min_stock: float = 0.0
    end_stock: float = 0.0
    holding: float = 0.0
    ageing_cost: float = 0.0
    aged_units: float = 0.0
    aged_value: float = 0.0
    ageing_units: float = 0.0
    ageing_value: float = 0.0
    excess_units: float = 0.0


@dataclass
class SimResult:
    locs: Dict[str, LocResult]
    transfer_cost: float = 0.0
    expedite_cost: float = 0.0
    notes: List[str] = field(default_factory=list)
    picked_batches: List[Dict[str, Any]] = field(default_factory=list)
    discount_cost: float = 0.0  # margin given up on discounted units sold

    @property
    def direct_cost(self) -> float:
        return self.transfer_cost + self.expedite_cost + self.discount_cost

    def total(self, attr: str) -> float:
        return float(sum(getattr(r, attr) for r in self.locs.values()))

    def summary(self) -> Dict[str, Any]:
        return {
            "units_sold": round(self.total("sold"), 1), "lost_sales": round(self.total("lost"), 1),
            "buffer_breach_days": {l: r.breach_days for l, r in self.locs.items()},
            "below_safety_days": {l: r.below_ss_days for l, r in self.locs.items()},
            "excess_units": round(self.total("excess_units"), 1), "aged_units": round(self.total("aged_units"), 1),
            "aged_value_aed": round(self.total("aged_value"), 2), "ageing_units": round(self.total("ageing_units"), 1),
            "holding_cost_aed": round(self.total("holding"), 2), "ageing_cost_aed": round(self.total("ageing_cost"), 2),
            "transfer_cost_aed": round(self.transfer_cost, 2), "expedite_cost_aed": round(self.expedite_cost, 2),
            "discount_margin_lost_aed": round(self.discount_cost, 2),
            "lost_by_location": {l: round(r.lost, 1) for l, r in self.locs.items()},
        }


class Engine:
    """Engine bound to one State. Forecasts are computed once and shared with derived states."""

    def __init__(self, state: State, fc_bundle: Optional[Dict[str, Any]] = None):
        self.state = state
        bundle = fc_bundle or forecast_all(state)
        self.fc: Dict[Tuple[str, str], CellForecast] = bundle["cells"]
        self.fc_meta = bundle["meta"]
        self.locs = [l for l in LOCATIONS if any(k[1] == l for k in state.inventory)]
        self._lead_cache: Dict[str, float] = {}
        self._sim_cache: Dict[Tuple, "SimResult"] = {}

    def derive(self, state: State, refresh_uplift: bool = True) -> "Engine":
        """Engine for a modified state (sandbox / signal what-if). History is unchanged, so only
        signal uplift is recomputed; the backtested base rates and bands are reused. Pass
        refresh_uplift=False when no demand signal changed (supply settings, lanes, stock)."""
        if not refresh_uplift:
            return Engine(state, {"cells": self.fc, "meta": self.fc_meta})
        cells = {}
        for k, c in self.fc.items():
            uplift, ids = {}, []
            for lvl in sigmod.LEVELS:
                uplift[lvl], ids_l = sigmod.demand_uplift(state, k[0], k[1], state.horizon, lvl)
                ids = ids or ids_l
            cells[k] = replace(c, uplift=uplift, signal_ids=ids)
        return Engine(state, {"cells": cells, "meta": self.fc_meta})

    # ------------------------------------------------------------------ inputs
    @property
    def H(self) -> int:
        return self.state.horizon

    def p(self, name: str) -> Any:
        return self.state.params[name]

    def demand(self, sku: str, loc: str, pct: str = "P50", level: str = "likely", scale: float = 1.0) -> np.ndarray:
        return self.fc[(sku, loc)].daily(pct, level) * scale

    def d50(self, sku: str, loc: str) -> float:
        return float(self.demand(sku, loc).mean())

    def supplier_mean_lead(self, supplier: str) -> float:
        if supplier not in self._lead_cache:
            lh = self.state.lead_history
            rows = lh[lh["supplier"] == supplier]
            days = [(r - o).days for o, r in zip(rows["order_date"], rows["received_date"])]
            self._lead_cache[supplier] = float(np.mean(days)) if days else 14.0
        return self._lead_cache[supplier]

    def supplier_lead(self, sku: str, level: str = "likely") -> Tuple[int, Dict[str, Any]]:
        sup = self.state.skus[sku]["supplier"]
        mean = self.supplier_mean_lead(sup)
        delay, ids = sigmod.new_order_delay(self.state, sku, level)
        extra = int(getattr(self.state, "order_delay_add", 0) or 0)  # Scenario Lab shipment-delay setting
        lead = int(math.ceil(mean - 1e-9)) + delay + extra
        return lead, {"supplier": sup, "mean_past_lead_days": round(mean, 2), "signal_delay_days": delay,
                      "signals": ids, "lead_days": lead, **({"scenario_delay_days": extra} if extra else {})}

    def lane(self, a: str, b: str) -> Dict[str, Any]:
        return self.state.lanes[(a, b)]

    def counts_inbound(self, po: Dict[str, Any]) -> bool:
        return po["status"] == "Confirmed" or po["po_id"] in self.state.include_unconfirmed

    def inbound(self, sku: str, level: str = "likely", decisions: Iterable[Dict[str, Any]] = ()) -> List[Dict[str, Any]]:
        mods: Dict[str, Dict[str, Any]] = {}
        for d in decisions:
            if d["type"] in ("bring_forward", "reduce_inbound", "delay_inbound") and d.get("sku") == sku:
                mods.setdefault(d["po_id"], {}).update({d["type"]: d})
        out = []
        for po in self.state.inbound:
            if po["sku"] != sku or not self.counts_inbound(po):
                continue
            delay, ids = sigmod.po_delay(self.state, po, level)
            day, qty = po["eta_day"] + delay, po["qty"]
            m = mods.get(po["po_id"], {})
            if "bring_forward" in m:
                day -= int(m["bring_forward"]["days"])
            if "delay_inbound" in m:
                day += int(m["delay_inbound"]["days"])
            if "reduce_inbound" in m:
                qty = int(m["reduce_inbound"]["new_qty"])
            out.append({"po_id": po["po_id"], "loc": po["location"], "day": max(1, day), "qty": qty,
                        "delay": delay, "signals": ids, "status": po["status"]})
        return out

    # ------------------------------------------------------------------ 6.1 stock path, spare, need
    def flows(self, sku: str, loc: str, level: str, decisions: Iterable[Dict[str, Any]] = ()) -> Tuple[float, np.ndarray]:
        """Opening free stock after day-0 departures, and arrivals per plan day 1..H at a location."""
        decisions = list(decisions)
        start = float(self.state.free(sku, loc))
        arr = np.zeros(self.H)
        for a in self.inbound(sku, level, decisions):
            if a["loc"] == loc and 1 <= a["day"] <= self.H:
                arr[a["day"] - 1] += a["qty"]
        for d in decisions:
            if d.get("sku") != sku:
                continue
            if d["type"] == "transfer":
                for leg in d["legs"]:
                    if leg["from"] == loc:
                        start -= leg["qty"]
                    if leg["to"] == loc:
                        lt = int(self.lane(leg["from"], leg["to"])["lead_days"])
                        if 1 <= lt <= self.H:
                            arr[lt - 1] += leg["qty"]
            elif d["type"] == "reorder" and d["location"] == loc:
                lt = self.supplier_lead(sku, level)[0] + int(d.get("order_day", 0))
                if 1 <= lt <= self.H:
                    arr[lt - 1] += d["qty"]
        return start, arr

    def stock_path(self, sku: str, loc: str, pct: str = "P50", level: str = "likely",
                   decisions: Iterable[Dict[str, Any]] = (), scale: float = 1.0) -> np.ndarray:
        """S_t(P, delta) = Free + inbound arrived by t - cumulative demand (unconstrained), t = 1..H."""
        start, arr = self.flows(sku, loc, level, decisions)
        return start + np.cumsum(arr) - np.cumsum(self.demand(sku, loc, pct, level, scale))

    def spare(self, sku: str, loc: str, decisions: Iterable[Dict[str, Any]] = ()) -> float:
        """Spare = max(0, min_t S_t(P90, delta_max) - SafetyStock)."""
        s = self.stock_path(sku, loc, "P90", "max", decisions)
        start = self.flows(sku, loc, "max", decisions)[0]
        low = min(float(s.min()), start)
        return max(0.0, low - self.state.safety.get((sku, loc), 0))

    def need(self, sku: str, loc: str, lead: int, decisions: Iterable[Dict[str, Any]] = ()) -> Tuple[int, float]:
        """Need = max(0, max_{L<=t<=H} (SafetyStock - S_t(P50, delta_likely))), rounded up to pack."""
        s = self.stock_path(sku, loc, "P50", "likely", decisions)
        ss = self.state.safety.get((sku, loc), 0)
        lo = max(1, lead)
        if lo > self.H:
            return 0, 0.0
        raw = max(0.0, float((ss - s[lo - 1:]).max()))
        return ceil_pack(raw, self.state.skus[sku]["pack_size"]), raw

    def breach_day(self, sku: str, loc: str, decisions: Iterable[Dict[str, Any]] = ()) -> Optional[int]:
        """First plan day free stock is below safety stock at P50 / likely delay (0 = already below)."""
        ss = self.state.safety.get((sku, loc), 0)
        if self.state.free(sku, loc) < ss:
            return 0
        s = self.stock_path(sku, loc, "P50", "likely", decisions)
        idx = np.where(s < ss - EPS)[0]
        return int(idx[0]) + 1 if len(idx) else None

    # ------------------------------------------------------------------ simulation (6.4)
    def _opening_batches(self, sku: str, loc: str) -> Tuple[List[List[float]], List[List[float]], List[str]]:
        """Free and reserved batches as [units, age_days, batch_id], oldest first.
        Reserved units are taken from the oldest batches (assumption); unexplained units get age 0."""
        inv = self.state.inventory.get((sku, loc), {"available": 0, "reserved": 0})
        batches = [list(x) for x in self._batch_index().get((sku, loc), [])]
        notes = []
        total = sum(x[0] for x in batches)
        avail = float(inv["available"])
        if total > avail + EPS:
            excess = total - avail
            for x in reversed(batches):
                cut = min(x[0], excess)
                x[0] -= cut
                excess -= cut
            batches = [x for x in batches if x[0] > EPS]
            notes.append(f"batch totals exceed available at {loc}; newest batches trimmed")
        elif total < avail - EPS:
            batches.append([avail - total, 0.0, "UNKNOWN-AGE"])
            notes.append(f"{avail - total:.0f} units at {loc} have no batch record; treated as fresh (Assumption)")
        reserved, left = [], float(inv["reserved"])
        free = []
        for x in batches:
            take = min(x[0], left)
            if take > EPS:
                reserved.append([take, x[1], x[2]])
                left -= take
            if x[0] - take > EPS:
                free.append([x[0] - take, x[1], x[2]])
        return free, reserved, notes

    def _batch_index(self) -> Dict[Tuple[str, str], List[List[Any]]]:
        """Batches per SKU-location, oldest first. Built once per batch table and shared by every
        derived (sandbox) engine, since sandboxes never edit batches."""
        b = self.state.batches
        hit = _BATCH_INDEX.get(id(b))
        if hit is not None and hit[0] is b:
            return hit[1]
        idx: Dict[Tuple[str, str], List[List[Any]]] = {}
        if not b.empty:
            for (sk, lc), g in b.groupby(["sku", "location"]):
                g = g.sort_values("age_days", ascending=False)
                idx[(sk, lc)] = [[float(u), float(a), str(i)] for u, a, i in zip(g["units"], g["age_days"], g["batch_id"])]
        if len(_BATCH_INDEX) > 8:
            _BATCH_INDEX.clear()
        _BATCH_INDEX[id(b)] = (b, idx)
        return idx

    @staticmethod
    def _take(batches: List[List[float]], qty: float) -> Tuple[float, List[List[float]]]:
        """Remove qty oldest-first; returns units taken and the batches picked."""
        taken, picked = 0.0, []
        while qty - taken > EPS and batches:
            b = batches[0]
            t = min(b[0], qty - taken)
            b[0] -= t
            taken += t
            picked.append([t, b[1], b[2]])
            if b[0] <= EPS:
                batches.pop(0)
        return taken, picked

    def simulate(self, sku: str, decisions: Iterable[Dict[str, Any]] = (), pct: str = "P50", level: str = "likely",
                 scale: Optional[Dict[str, float]] = None) -> SimResult:
        decisions = [d for d in decisions if d.get("sku") == sku]
        key = (sku, stable_hash(decisions), pct, level, tuple(sorted((scale or {}).items())))
        if key not in self._sim_cache:
            self._sim_cache[key] = self._simulate(sku, decisions, pct, level, scale)
        return self._sim_cache[key]

    def _simulate(self, sku: str, decisions: List[Dict[str, Any]], pct: str, level: str,
                  scale: Optional[Dict[str, float]]) -> SimResult:
        st = self.state
        H = self.H
        srow = st.skus[sku]
        cost = srow["unit_cost"]
        age_d, aged_d = self.p("ageing_days"), self.p("aged_days")
        age_rate = cost * self.p("ageing_cost_pct_per_30_days") / 100.0 / 30.0
        free_b, res_b, arrivals = {}, {}, {l: [[] for _ in range(H + 1)] for l in self.locs}
        notes: List[str] = []
        for l in self.locs:
            free_b[l], res_b[l], n = self._opening_batches(sku, l)
            notes += n
        for a in self.inbound(sku, level, decisions):
            if 1 <= a["day"] <= H:
                arrivals[a["loc"]][a["day"]].append([float(a["qty"]), 0.0, a["po_id"]])
        transfer_cost = expedite_cost = 0.0
        picked_all = []
        for d in decisions:
            if d["type"] == "transfer":
                for leg in d["legs"]:
                    if leg["qty"] <= 0:
                        continue
                    lane = self.lane(leg["from"], leg["to"])
                    got, picked = self._take(free_b[leg["from"]], float(leg["qty"]))
                    if got < leg["qty"] - EPS:
                        notes.append(f"only {got:.0f} free units at {leg['from']} for a {leg['qty']}-unit leg")
                    transfer_cost += lane["fixed_cost_aed"] + leg["qty"] * lane["cost_per_unit_aed"]
                    lt = int(lane["lead_days"])
                    picked_all.append({"from": leg["from"], "to": leg["to"],
                                       "batches": [{"batch_id": p[2], "units": round(p[0], 1), "age_days": int(p[1])} for p in picked]})
                    if 1 <= lt <= H:
                        arrivals[leg["to"]][lt].extend([[p[0], p[1] + lt, p[2]] for p in picked])
            elif d["type"] == "reorder":
                lt = self.supplier_lead(sku, level)[0] + int(d.get("order_day", 0))
                if 1 <= lt <= H:
                    arrivals[d["location"]][lt].append([float(d["qty"]), 0.0, "REORDER"])
            elif d["type"] == "bring_forward":
                po = next((p for p in st.inbound if p["po_id"] == d["po_id"]), None)
                expedite_cost += float(po.get("expedite_cost_aed") or 0.0) if po else 0.0
        # discount: the oldest `units` free units at a location are marked down; demand there rises by the
        # assumed uplift while discounted units remain, and each discounted unit sold gives up depth x price
        disc = {d["location"]: d for d in decisions if d["type"] == "discount"}
        discount_cost = 0.0
        out: Dict[str, LocResult] = {}
        for l in self.locs:
            dem = self.demand(sku, l, pct, level, (scale or {}).get(l, 1.0))
            ss = st.safety.get((sku, l), 0)
            hc = st.holding.get(l, 0.0)
            fb, rb = free_b[l], res_b[l]
            r = LocResult(min_stock=float("inf"))
            dd = disc.get(l)
            disc_left = min(float(dd["units"]), sum(b[0] for b in fb)) if dd else 0.0
            for t in range(1, H + 1):
                for b in arrivals[l][t]:
                    fb.append(list(b))
                fb.sort(key=lambda x: -x[1])  # oldest first
                stock = sum(b[0] for b in fb)
                want = dem[t - 1] * (1 + float(dd["uplift_pct"]) / 100.0) if dd and disc_left > EPS else dem[t - 1]
                sold = min(want, stock)
                lost = max(0.0, dem[t - 1] - sold)  # uplifted demand that goes unmet is not a lost sale
                if dd and disc_left > EPS:
                    sold_disc = min(sold, disc_left)  # FIFO: the discounted (oldest) units sell first
                    disc_left -= sold_disc
                    discount_cost += sold_disc * srow["unit_price"] * float(dd["depth_pct"]) / 100.0
                self._take(fb, sold)
                for b in fb:
                    b[1] += 1
                for b in rb:
                    b[1] += 1
                end = sum(b[0] for b in fb)
                on_hand = end + sum(b[0] for b in rb)
                r.sold += sold
                r.lost += lost
                r.lost_daily.append(lost)
                r.sold_daily.append(sold)
                r.end_daily.append(end)
                r.min_stock = min(r.min_stock, end)
                if end < ss - EPS:
                    r.below_ss_days += 1
                    if end > EPS:
                        r.breach_days += 1
                if lost > LOST_EPS:
                    r.shortage_days += 1
                r.holding += on_hand * hc
                r.ageing_cost += sum(b[0] for b in fb + rb if b[1] >= age_d) * age_rate
            r.end_stock = r.end_daily[-1] if r.end_daily else 0.0
            for b in fb + rb:
                if b[1] >= aged_d:
                    r.aged_units += b[0]
                    r.aged_value += b[0] * cost
                elif b[1] >= age_d:
                    r.ageing_units += b[0]
                    r.ageing_value += b[0] * cost
            r.excess_units = max(0.0, r.end_stock - self.p("max_cover_days") * float(dem.mean()))
            if r.min_stock == float("inf"):
                r.min_stock = 0.0
            out[l] = r
        return SimResult(out, transfer_cost, expedite_cost, notes, picked_all, discount_cost)

    # ------------------------------------------------------------------ 6.4 net benefit
    def net(self, sku: str, opt: SimResult, base: SimResult) -> Dict[str, float]:
        # Margin x extra units sold. With unchanged demand this equals Margin x lost sales avoided (section 6.4);
        # a discount also raises demand, so units sold is the general form.
        margin = self.state.margin(sku)
        d_lost = base.total("lost") - opt.total("lost")
        d_sold = opt.total("sold") - base.total("sold")
        d_hold = opt.total("holding") - base.total("holding")
        d_age = opt.total("ageing_cost") - base.total("ageing_cost")
        direct = opt.direct_cost - base.direct_cost
        return {
            "net_benefit_aed": round(margin * d_sold - d_hold - d_age - direct, 2),
            "margin_per_unit_aed": round(margin, 2),
            "lost_sales_avoided": round(d_lost, 1),
            "extra_units_sold": round(d_sold, 1),
            "margin_protected_aed": round(margin * d_sold, 2),
            "discount_margin_lost_aed": round(opt.discount_cost - base.discount_cost, 2),
            "delta_holding_aed": round(d_hold, 2),
            "delta_ageing_aed": round(d_age, 2),
            "direct_cost_aed": round(direct, 2),
        }

    # ------------------------------------------------------------------ 6.5 recommendation rule
    def check(self, sku: str, decisions: List[Dict[str, Any]], base_decisions: List[Dict[str, Any]] = (),
              pct: str = "P50", spare_ref: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
        """Economic / Safe / Feasible checks for `decisions` (applied on top of `base_decisions`) vs `base_decisions`."""
        base_decisions = list(base_decisions)
        full = base_decisions + list(decisions)
        base = self.simulate(sku, base_decisions, pct, "likely")
        opt = self.simulate(sku, full, pct, "likely")
        econ = self.net(sku, opt, base)
        economic = {"pass": econ["net_benefit_aed"] > 0, **econ}
        if not economic["pass"]:
            economic["why"] = (f"Net {econ['net_benefit_aed']:.0f} AED: margin protected {econ['margin_protected_aed']:.0f} AED "
                               f"vs direct cost {econ['direct_cost_aed']:.0f} AED, holding {econ['delta_holding_aed']:.0f} AED, "
                               f"ageing {econ['delta_ageing_aed']:.0f} AED")
        # Safe: at P90 demand and the likely delay
        b90 = self.simulate(sku, base_decisions, "P90", "likely")
        o90 = self.simulate(sku, full, "P90", "likely")
        safe_fail = []
        for l in self.locs:
            ss = self.state.safety.get((sku, l), 0)
            b, o = b90.locs[l], o90.locs[l]
            if b.below_ss_days == 0:
                if o.below_ss_days > 0:
                    safe_fail.append(f"{l} falls to {o.min_stock:.0f}, below its safety stock of {ss}, under P90 demand")
            elif o.below_ss_days > b.below_ss_days or o.lost > b.lost + LOST_EPS or o.min_stock < b.min_stock - 0.5:
                safe_fail.append(f"{l} breach gets worse under P90 demand ({b.below_ss_days} to {o.below_ss_days} days below safety stock)")
        safe = {"pass": not safe_fail, "failures": safe_fail}
        # Feasible
        feas_fail, labels = [], []
        spare_ref = spare_ref or {}
        for d in decisions:
            if d["type"] == "transfer":
                for leg in d["legs"]:
                    avail = self.state.free(sku, leg["from"])
                    if leg["qty"] > avail:
                        feas_fail.append(f"leg {leg['from']}->{leg['to']} {leg['qty']} units exceeds free stock {avail} (reserved stock is never moved)")
                    sp = spare_ref.get(leg["from"], self.spare(sku, leg["from"], base_decisions))
                    if leg["qty"] > sp + EPS:
                        feas_fail.append(f"leg {leg['from']}->{leg['to']} {leg['qty']} units exceeds spare {floor_pack(sp, self.state.skus[sku]['pack_size'])} at {leg['from']}")
                    if int(self.lane(leg["from"], leg["to"])["lead_days"]) > self.H:
                        feas_fail.append("arrives after horizon")
                        labels.append("arrives after horizon")
            elif d["type"] == "reorder":
                if self.supplier_lead(sku)[0] + int(d.get("order_day", 0)) > self.H:
                    feas_fail.append("arrives after horizon")
                    labels.append("arrives after horizon")
            elif d["type"] == "discount":
                avail = self.state.free(sku, d["location"])
                if d["units"] > avail:
                    feas_fail.append(f"discount covers {d['units']} units but only {avail} are free at {d['location']}")
                if not (0 < float(d["depth_pct"]) < 100):
                    feas_fail.append("discount depth must be between 0% and 100%")
            elif d["type"] in ("bring_forward", "reduce_inbound", "delay_inbound"):
                po = next((p for p in self.state.inbound if p["po_id"] == d["po_id"]), None)
                if po is None or not po.get("changeable"):
                    feas_fail.append(f"{d['po_id']} is not marked changeable")
        feasible = {"pass": not feas_fail, "failures": feas_fail, "labels": labels}
        failing = [n for n, c in (("Economic", economic), ("Safe", safe), ("Feasible", feasible)) if not c["pass"]]
        return {"economic": economic, "safe": safe, "feasible": feasible, "passes": not failing, "failing_checks": failing,
                "sim": opt.summary(), "base_sim": base.summary(), "picked_batches": opt.picked_batches, "notes": opt.notes}

    # ------------------------------------------------------------------ 6.6 robustness
    def robustness(self, sku: str, decisions: List[Dict[str, Any]], base_decisions: List[Dict[str, Any]] = ()) -> Dict[str, Any]:
        base_decisions = list(base_decisions)
        full = base_decisions + list(decisions)
        cases, failing, harmful = [], [], []
        for pct in PCTS:
            for lvl in LEVELS:
                b = self.simulate(sku, base_decisions, pct, lvl)
                o = self.simulate(sku, full, pct, lvl)
                n = self.net(sku, o, b)["net_benefit_aed"]
                new_lost = [l for l in self.locs if o.locs[l].lost > LOST_EPS and b.locs[l].lost <= LOST_EPS]
                new_breach = [l for l in self.locs if o.locs[l].below_ss_days > 0 and b.locs[l].below_ss_days == 0]
                case = {"demand": pct, "delay": lvl, "net_benefit_aed": n, "new_lost_sales_at": new_lost,
                        "new_buffer_breach_at": new_breach,
                        "lost_sales": {l: round(o.locs[l].lost, 1) for l in self.locs}}
                cases.append(case)
                if new_lost:
                    harmful.append(f"{pct}/{lvl}: lost sales at {', '.join(new_lost)} " +
                                   ", ".join(f"({o.locs[l].lost:.0f} units)" for l in new_lost))
                if n < -EPS or new_breach:
                    failing.append(f"{pct}/{lvl}: " + ("net %.0f AED" % n if n < -EPS else "") +
                                   (" new buffer breach at " + ", ".join(new_breach) if new_breach else ""))
        label = "Harmful" if harmful else ("Robust" if not failing else "Fragile")
        return {"label": label, "failing_cases": harmful if harmful else failing, "cases": cases}

    # ------------------------------------------------------------------ input hash (G3)
    def sku_input_hash(self, sku: str) -> str:
        st = self.state
        payload = {
            "sku": {k: v for k, v in st.skus[sku].items()},
            "inv": {l: st.inventory.get((sku, l)) for l in self.locs},
            "ss": {l: st.safety.get((sku, l)) for l in self.locs},
            "inbound": [(p["po_id"], p["location"], p["qty"], str(p["eta"]), p["status"], p.get("changeable"))
                        for p in st.inbound if p["sku"] == sku],
            "include_unconfirmed": sorted(st.include_unconfirmed),
            "signals": [(s["signal_id"], s.get("include"), s["status"], s.get("delay_min"), s.get("delay_likely"),
                         s.get("delay_max"), s.get("uplift_min"), s.get("uplift_likely"), s.get("uplift_max"))
                        for s in st.signals],
            "fc": {l: [round(float(x), 3) for x in np.concatenate([self.demand(sku, l, p) for p in PCTS])] for l in self.locs},
            "lanes": sorted((k, v["fixed_cost_aed"], v["cost_per_unit_aed"], v["lead_days"]) for k, v in st.lanes.items()),
            "holding": st.holding, "params": st.params,
            "batches": [] if st.batches.empty else
            st.batches[st.batches["sku"] == sku][["location", "batch_id", "units", "age_days"]].values.tolist(),
            "lead": self.supplier_lead(sku),
        }
        return stable_hash(payload)
