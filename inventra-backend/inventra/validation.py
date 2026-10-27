"""Schema checks for incoming files (W2 gate) and the validation report on loaded data (D2)."""
from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Any, Dict, List, Optional, Set

import pandas as pd

from .schemas import INBOUND_STATUSES, LINEAGE_COLUMNS, LOCATIONS, SIGNAL_STATUSES, SIGNAL_TYPES, TABLES
from .state import State, parse_value, record_id

MAX_ERRORS_LISTED = 50


def detect_table(columns: List[str]) -> Optional[str]:
    """Match a file to a table by its header: every required column present, no unknown columns."""
    cols = set(columns)
    best = None
    for name, spec in TABLES.items():
        required = set(spec.columns) - ({"as_of"} if name != "inventory" else set())
        if required <= cols and cols <= set(spec.columns) | set(LINEAGE_COLUMNS):
            if best is None or len(spec.columns) > len(TABLES[best].columns):
                best = name
    return best


def check_file(table: str, df: pd.DataFrame, known_skus: Set[str], known_locations: Set[str],
               known_pos: Set[str] = frozenset()) -> List[str]:
    """Blocking checks. Any error quarantines the whole file; nothing is loaded or re-planned."""
    spec = TABLES[table]
    errors: List[str] = []
    missing = [c for c in spec.columns if c not in df.columns and not (c == "as_of" and table != "inventory")]
    if missing:
        return [f"missing columns: {', '.join(missing)}"]
    unknown_cols = [c for c in df.columns if c not in spec.columns and c not in LINEAGE_COLUMNS]
    if unknown_cols:
        errors.append(f"unknown columns: {', '.join(unknown_cols)}")
    if df.empty:
        errors.append("file has no data rows")
    dup = df.duplicated(subset=list(spec.key), keep=False)
    if dup.any():
        for i in df.index[dup]:
            errors.append(f"row {i + 2}: duplicate key {', '.join(str(df.at[i, k]) for k in spec.key)}")
    for i, row in df.iterrows():
        line = i + 2  # header is line 1
        typed: Dict[str, Any] = {}
        for col, kind in spec.columns.items():
            if col not in df.columns:
                continue
            try:
                typed[col] = parse_value(kind, row[col])
            except (ValueError, TypeError) as e:
                errors.append(f"row {line}: {col} '{row[col]}' invalid ({e})")
        for col in spec.nonneg:
            v = typed.get(col)
            if isinstance(v, (int, float)) and v < 0:
                errors.append(f"row {line}: negative {col} ({v})")
        sku = typed.get("sku")
        if sku and table != "sku_master" and sku not in known_skus:
            errors.append(f"row {line}: unknown SKU {sku}")
        for loc_col in ("location", "from_loc", "to_loc"):
            loc = typed.get(loc_col)
            if loc and loc not in known_locations:
                errors.append(f"row {line}: unknown location {loc}")
        if table == "inventory" and isinstance(typed.get("reserved"), int) and isinstance(typed.get("available"), int):
            if typed["reserved"] > typed["available"]:
                errors.append(f"row {line}: reserved {typed['reserved']} above available {typed['available']}")
        if table == "sales_daily" and typed.get("in_stock_flag") not in (0, 1, None):
            errors.append(f"row {line}: in_stock_flag must be 0 or 1")
        if table == "inbound_orders" and typed.get("status") not in INBOUND_STATUSES:
            errors.append(f"row {line}: status must be one of {', '.join(INBOUND_STATUSES)}")
        if table == "signals":
            if typed.get("type") not in SIGNAL_TYPES:
                errors.append(f"row {line}: type must be supply or demand")
            if typed.get("status") not in SIGNAL_STATUSES:
                errors.append(f"row {line}: status must be Unverified or Confirmed")
            for pre in ("delay", "uplift"):
                vals = [typed.get(f"{pre}_{k}") for k in ("min", "likely", "max")]
                if any(v is not None for v in vals):
                    if any(v is None for v in vals) or not (vals[0] <= vals[1] <= vals[2]):
                        errors.append(f"row {line}: {pre} needs min <= likely <= max")
            if typed.get("type") == "supply" and typed.get("delay_likely") is None:
                errors.append(f"row {line}: supply signal needs a delay range")
            if typed.get("type") == "demand" and typed.get("uplift_likely") is None:
                errors.append(f"row {line}: demand signal needs an uplift range")
            if typed.get("type") == "supply" and not (typed.get("components") or typed.get("routes")):
                errors.append(f"row {line}: supply signal must name components or routes")
            if typed.get("type") == "demand" and not (typed.get("skus") and typed.get("locations")):
                errors.append(f"row {line}: demand signal must name SKUs and locations")
            for s in typed.get("skus") or []:
                if s not in known_skus:
                    errors.append(f"row {line}: unknown SKU {s}")
            for l in typed.get("locations") or []:
                if l not in known_locations:
                    errors.append(f"row {line}: unknown location {l}")
        if len(errors) > MAX_ERRORS_LISTED:
            errors.append("... more errors not listed")
            break
    return errors


# ---------------------------------------------------------------------- D2 report
def report(state: State) -> Dict[str, Any]:
    issues: List[Dict[str, Any]] = []
    skus, locs = set(state.skus), set(LOCATIONS)

    # missing days per location (and per SKU-location when only some SKUs are missing)
    sales = state.sales
    all_days = set(range(1, state.history_days + 1))
    for loc in LOCATIONS:
        sub = sales[sales["location"] == loc]
        per_sku = {s: set(sub[sub["sku"] == s]["day"]) for s in skus}
        common_missing = sorted(set.intersection(*[all_days - d for d in per_sku.values()])) if per_sku else []
        if common_missing:
            issues.append({"check": "missing_days", "severity": "warning", "location": loc,
                           "days": common_missing,
                           "dates": [str(state.day1 + timedelta(days=d - 1)) for d in common_missing],
                           "message": f"{loc}: no sales rows for {len(common_missing)} days (all SKUs); days imputed as Estimate"})
        for s, d in per_sku.items():
            extra = sorted(all_days - d - set(common_missing))
            if extra:
                issues.append({"check": "missing_days", "severity": "warning", "location": loc, "sku": s, "days": extra,
                               "message": f"{s} at {loc}: {len(extra)} missing days"})

    for (sku, loc), inv in state.inventory.items():
        rid = inv["record_id"]
        if sku not in skus:
            issues.append({"check": "unknown_sku", "severity": "error", "record_id": rid, "message": f"unknown SKU {sku}"})
        if loc not in locs:
            issues.append({"check": "unknown_location", "severity": "error", "record_id": rid, "message": f"unknown location {loc}"})
        if inv["available"] < 0 or inv["reserved"] < 0:
            issues.append({"check": "negative_stock", "severity": "error", "record_id": rid,
                           "message": f"{sku} at {loc}: negative stock"})
        if inv["reserved"] > inv["available"]:
            issues.append({"check": "reserved_above_available", "severity": "error", "record_id": rid,
                           "message": f"{sku} at {loc}: reserved {inv['reserved']} above available {inv['available']}"})

    if not state.batches.empty:
        tot = state.batches.groupby(["sku", "location"])["units"].sum()
        for (sku, loc), inv in state.inventory.items():
            bt = int(tot.get((sku, loc), 0))
            if bt != inv["available"]:
                issues.append({"check": "batch_total_mismatch", "severity": "warning", "record_id": inv["record_id"],
                               "sku": sku, "location": loc, "batch_total": bt, "available": inv["available"],
                               "message": f"{sku} at {loc}: batch units {bt} do not match available {inv['available']}"})

    # stock record vs latest cycle count
    if not state.counts.empty:
        latest = state.counts.sort_values("count_date").groupby(["sku", "location"]).tail(1)
        for _, c in latest.iterrows():
            inv = state.inventory.get((c["sku"], c["location"]))
            if inv is None:
                issues.append({"check": "unknown_sku", "severity": "error",
                               "message": f"count for unknown SKU-location {c['sku']} / {c['location']}"})
                continue
            if int(c["counted_units"]) != int(inv["available"]):
                newer = c["count_date"] > inv["as_of"]
                count_rid = record_id("stock_counts", {"sku": c["sku"], "location": c["location"], "count_date": c["count_date"].isoformat()})
                issues.append({
                    "check": "cycle_count_mismatch", "severity": "error" if newer else "warning",
                    "record_id": inv["record_id"], "sku": c["sku"], "location": c["location"],
                    "recorded_available": int(inv["available"]), "counted_units": int(c["counted_units"]),
                    "record_as_of": inv["as_of"].isoformat(), "count_date": c["count_date"].isoformat(),
                    "evidence_record_id": count_rid,
                    "message": (f"{c['sku']} at {c['location']}: stock record says {inv['available']} "
                                f"(as of {inv['as_of'].isoformat()}); a {'later' if newer else 'earlier'} cycle count says "
                                f"{c['counted_units']} ({c['count_date'].isoformat()})"),
                    "suggested_correction": {"record_id": inv["record_id"], "field": "available",
                                             "new_value": int(c["counted_units"]), "evidence_record_id": count_rid},
                })

    # duplicates in sales history (after key collapse they cannot occur in state; checked on raw by the store)
    for (sku, loc) in [(s, l) for s in skus for l in locs]:
        if (sku, loc) not in state.inventory:
            issues.append({"check": "missing_record", "severity": "warning",
                           "message": f"no inventory record for {sku} at {loc}"})
    for po in state.inbound:
        if po["sku"] not in skus or po["location"] not in locs:
            issues.append({"check": "unknown_sku" if po["sku"] not in skus else "unknown_location", "severity": "error",
                           "record_id": po["record_id"], "message": f"{po['po_id']} refers to unknown SKU or location"})

    counts = defaultdict(int)
    for i in issues:
        counts[i["check"]] += 1
    return {"as_of_planning_day": str(state.day90), "issue_counts": dict(counts), "issues": issues}


def raw_duplicates(store, table: str) -> List[Dict[str, Any]]:
    """Duplicate keys within one load (the current view keeps the latest; duplicates within a load are reported)."""
    key = ", ".join(f'"{k}"' for k in TABLES[table].key)
    rows = store.query(f'SELECT {key}, _load_id, COUNT(*) AS n FROM "raw_{table}" GROUP BY {key}, _load_id HAVING COUNT(*) > 1')
    return [{"check": "duplicates", "severity": "warning", "table": table, **r,
             "message": f"duplicate {table} key in one load: " + ", ".join(str(r[k]) for k in TABLES[table].key)} for r in rows]
