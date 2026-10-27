"""Engine state: the current input tables, typed, with corrections and signal edits overlaid.

Every engine module is a pure function of a State, so alternative states (sandbox
corrections, signal on/off, replay arms) are cheap to build and never touch storage.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from .schemas import LOCATIONS, TABLES, record_id
from .storage import Store


# ---------------------------------------------------------------------- parsing
def parse_value(kind: str, value: Any) -> Any:
    optional = kind.endswith("?")
    kind = kind.rstrip("?")
    if value is None or (isinstance(value, float) and pd.isna(value)) or str(value).strip() in ("", "nan", "None", "<NA>"):
        if optional or kind in ("list", "str"):
            return [] if kind == "list" else (None if optional else "")
        raise ValueError("missing value")
    s = str(value).strip()
    if kind == "str":
        return s
    if kind == "int":
        f = float(s)
        if f != int(f):
            raise ValueError(f"'{s}' is not a whole number")
        return int(f)
    if kind == "float":
        return float(s)
    if kind == "bool":
        if s.lower() in ("true", "1", "yes"):
            return True
        if s.lower() in ("false", "0", "no"):
            return False
        raise ValueError(f"'{s}' is not true/false")
    if kind == "date":
        return date.fromisoformat(s[:10])
    if kind == "datetime":
        return datetime.fromisoformat(s)
    if kind == "list":
        return [x.strip() for x in s.split(";") if x.strip()]
    raise ValueError(f"unknown type {kind}")


def typed_frame(table: str, raw: pd.DataFrame) -> pd.DataFrame:
    spec = TABLES[table]
    df = raw.copy()
    for col, kind in spec.columns.items():
        df[col] = [parse_value(kind, v) for v in df[col]] if len(df) else []
    return df


PARAM_TYPES = {
    "horizon_days": int, "max_cover_days": int, "ageing_days": int, "aged_days": int,
    "ageing_cost_pct_per_30_days": float, "review_buffer_days": int, "review_period_days": int,
    "base_rate_window_days": int, "low_confidence_band_widening": float, "urgency_soon_days": int,
    "freshness_days": int, "baseline_reorder_days": int, "backtest_step_days": int,
}


def parse_params(rows: pd.DataFrame) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for _, r in rows.iterrows():
        name, val = r["name"], str(r["value"])
        if name == "confidence_thresholds":
            a, b, c = (float(x) for x in val.split(";"))
            out[name] = {"medium_pct": a, "low_pct": b, "min_units": c}
        elif name in PARAM_TYPES:
            out[name] = PARAM_TYPES[name](float(val)) if PARAM_TYPES[name] is int else float(val)
        else:
            out[name] = val
    return out


# ---------------------------------------------------------------------- state
@dataclass
class State:
    day90: date
    params: Dict[str, Any]
    skus: Dict[str, Dict[str, Any]]
    sales: pd.DataFrame
    inventory: Dict[Tuple[str, str], Dict[str, Any]]
    counts: pd.DataFrame
    batches: pd.DataFrame
    safety: Dict[Tuple[str, str], int]
    inbound: List[Dict[str, Any]]
    lead_history: pd.DataFrame
    lanes: Dict[Tuple[str, str], Dict[str, Any]]
    holding: Dict[str, float]
    signals: List[Dict[str, Any]]
    params_meta: List[Dict[str, Any]]
    lineage: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    corrected_fields: Dict[str, List[str]] = field(default_factory=dict)
    sandbox: bool = False
    include_unconfirmed: Tuple[str, ...] = ()  # PO ids treated as arriving although Unconfirmed (S8 what-if)
    order_delay_add: int = 0  # Scenario Lab sandbox: extra days on new supplier orders

    # ---------------------------------------------------------------- helpers
    @property
    def horizon(self) -> int:
        return int(self.params.get("horizon_days", 30))

    @property
    def history_days(self) -> int:
        return 90

    @property
    def day1(self) -> date:
        return self.day90 - timedelta(days=self.history_days - 1)

    def plan_day(self, d: date) -> int:
        return (d - self.day90).days

    def plan_date(self, t: int) -> date:
        return self.day90 + timedelta(days=t)

    def free(self, sku: str, loc: str) -> int:
        inv = self.inventory.get((sku, loc))
        return max(0, inv["available"] - inv["reserved"]) if inv else 0

    def margin(self, sku: str) -> float:
        s = self.skus[sku]
        return s["unit_price"] - s["unit_cost"]

    def is_semiconductor(self, sku: str) -> bool:
        return "power_management_ic" in self.skus[sku]["components"]

    def copy(self) -> "State":
        return copy.deepcopy(self)


def _lineage(table: str, raw: pd.DataFrame) -> Dict[str, Dict[str, Any]]:
    out = {}
    for _, r in raw.iterrows():
        out[record_id(table, r)] = {
            "table": table, "source_file": r.get("source_file"), "row": int(r.get("_row") or 0),
            "loaded_at": r.get("loaded_at"), "as_of": r.get("as_of"),
        }
    return out


def _apply_corrections(raw: Dict[str, pd.DataFrame], corrections: List[Dict[str, Any]]):
    corrected: Dict[str, List[str]] = {}
    for c in corrections:
        table, _, rest = c["record_id"].partition(":")
        if table not in raw:
            continue
        df = raw[table]
        keys = TABLES[table].key
        parts = rest.split("|")
        mask = pd.Series(True, index=df.index)
        for k, v in zip(keys, parts):
            mask &= df[k].astype(str) == v
        if mask.any():
            df.loc[mask, c["field"]] = c["new_value"]
            if table == "planning_parameters":
                df.loc[mask, "changed_by"] = c.get("user")
                df.loc[mask, "changed_at"] = c.get("timestamp")
            corrected.setdefault(c["record_id"], []).append(c["field"])
    return corrected


def build_state(store: Store, extra_corrections: Optional[List[Dict[str, Any]]] = None,
                apply_corrections: bool = True) -> State:
    raw = {t: store.current(t) for t in TABLES}
    corrections = store.query('SELECT * FROM corrections_log ORDER BY timestamp, correction_id') if apply_corrections else []
    corrections = list(corrections) + list(extra_corrections or [])
    lineage: Dict[str, Dict[str, Any]] = {}
    for t, df in raw.items():
        lineage.update(_lineage(t, df))
    corrected = _apply_corrections(raw, corrections)
    typed = {t: typed_frame(t, df) if not df.empty else df for t, df in raw.items()}
    return assemble(typed, store.query("SELECT * FROM signal_edits ORDER BY timestamp, edit_id"), lineage, corrected)


def assemble(typed: Dict[str, pd.DataFrame], signal_edits: List[Dict[str, Any]],
             lineage: Dict[str, Dict[str, Any]], corrected: Dict[str, List[str]]) -> State:
    sales = typed["sales_daily"].copy()
    day90 = max(sales["date"]) if not sales.empty else date.today()
    sales["day"] = [(d - day90).days + 90 for d in sales["date"]]

    params = parse_params(typed["planning_parameters"])
    skus = {r["sku"]: dict(r) for _, r in typed["sku_master"].iterrows()}

    inventory = {}
    for _, r in typed["inventory"].iterrows():
        inventory[(r["sku"], r["location"])] = {
            "available": r["available"], "reserved": r["reserved"], "as_of": r["as_of"],
            "record_id": record_id("inventory", r),
        }
    safety = {(r["sku"], r["location"]): r["units"] for _, r in typed["safety_stock"].iterrows()}

    batches = typed["stock_age"].copy()
    if not batches.empty:
        batches["age_days"] = [(day90 - d).days for d in batches["received_date"]]

    inbound = []
    for _, r in typed["inbound_orders"].iterrows():
        d = dict(r)
        d["eta_day"] = (r["eta"] - day90).days
        d["record_id"] = record_id("inbound_orders", r)
        inbound.append(d)

    lanes = {(r["from_loc"], r["to_loc"]): dict(r) for _, r in typed["transfer_lanes"].iterrows()}
    holding = {r["location"]: r["cost_per_unit_day_aed"] for _, r in typed["holding_costs"].iterrows()}

    signals = []
    for _, r in typed["signals"].iterrows():
        s = dict(r)
        s["include"] = True
        s["record_id"] = record_id("signals", r)
        signals.append(s)
    by_id = {s["signal_id"]: s for s in signals}
    for e in signal_edits:
        s = by_id.get(e["signal_id"])
        if s is None:
            continue
        f, v = e["field"], e["new_value"]
        if f == "include":
            s["include"] = str(v).lower() in ("true", "1")
        elif f == "status":
            s["status"] = v
        else:
            s[f] = None if v in (None, "", "None") else float(v)

    params_meta = [dict(r) for _, r in typed["planning_parameters"].iterrows()]
    return State(day90=day90, params=params, skus=skus, sales=sales, inventory=inventory,
                 counts=typed["stock_counts"], batches=batches, safety=safety, inbound=inbound,
                 lead_history=typed["supplier_lead_history"], lanes=lanes, holding=holding,
                 signals=signals, params_meta=params_meta, lineage=lineage, corrected_fields=corrected)


def stable_hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:16]


ALL_LOCATIONS = LOCATIONS
