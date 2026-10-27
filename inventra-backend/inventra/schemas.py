"""Input table schemas (design doc section 10.1).

Every input table is stored raw (all text) with its source file and load time; types
are checked here and converted when the engine builds its state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

LOCATIONS = ["Dubai", "Abu Dhabi", "Sharjah"]
LINEAGE_COLUMNS = ["source_file", "loaded_at", "as_of"]


@dataclass(frozen=True)
class TableSpec:
    name: str
    key: Tuple[str, ...]
    columns: Dict[str, str]  # column -> type: str, int, float, date, datetime, bool, list
    nonneg: Tuple[str, ...] = ()
    correctable: Tuple[str, ...] = ()  # fields a planner may correct (G2, D4)


TABLES: Dict[str, TableSpec] = {
    "sku_master": TableSpec(
        "sku_master", ("sku",),
        {"sku": "str", "name": "str", "category": "str", "components": "list", "unit_cost": "float",
         "unit_price": "float", "pack_size": "int", "moq": "int", "supplier": "str", "inbound_route": "str"},
        nonneg=("unit_cost", "unit_price", "pack_size", "moq"),
        correctable=("unit_cost", "unit_price", "pack_size", "moq"),
    ),
    "sales_daily": TableSpec(
        "sales_daily", ("date", "sku", "location"),
        {"date": "date", "sku": "str", "location": "str", "units": "int", "in_stock_flag": "int"},
        nonneg=("units",), correctable=("units", "in_stock_flag"),
    ),
    "inventory": TableSpec(
        "inventory", ("sku", "location"),
        {"sku": "str", "location": "str", "available": "int", "reserved": "int", "as_of": "datetime"},
        nonneg=("available", "reserved"), correctable=("available", "reserved"),
    ),
    "stock_counts": TableSpec(
        "stock_counts", ("sku", "location", "count_date"),
        {"sku": "str", "location": "str", "counted_units": "int", "count_date": "datetime", "counted_by": "str"},
        nonneg=("counted_units",),
    ),
    "stock_age": TableSpec(
        "stock_age", ("sku", "location", "batch_id"),
        {"sku": "str", "location": "str", "batch_id": "str", "units": "int", "received_date": "date"},
        nonneg=("units",), correctable=("units", "received_date"),
    ),
    "safety_stock": TableSpec(
        "safety_stock", ("sku", "location"),
        {"sku": "str", "location": "str", "units": "int"},
        nonneg=("units",), correctable=("units",),
    ),
    "inbound_orders": TableSpec(
        "inbound_orders", ("po_id",),
        {"po_id": "str", "sku": "str", "location": "str", "qty": "int", "eta": "date", "supplier": "str",
         "route": "str", "status": "str", "changeable": "bool", "expedite_days": "int?",
         "expedite_cost_aed": "float?"},
        nonneg=("qty",), correctable=("qty", "eta", "status"),
    ),
    "supplier_lead_history": TableSpec(
        "supplier_lead_history", ("supplier", "sku", "order_date"),
        {"supplier": "str", "sku": "str", "order_date": "date", "received_date": "date"},
    ),
    "transfer_lanes": TableSpec(
        "transfer_lanes", ("from_loc", "to_loc"),
        {"from_loc": "str", "to_loc": "str", "fixed_cost_aed": "float", "cost_per_unit_aed": "float",
         "lead_days": "int"},
        nonneg=("fixed_cost_aed", "cost_per_unit_aed", "lead_days"),
        correctable=("fixed_cost_aed", "cost_per_unit_aed", "lead_days"),
    ),
    "holding_costs": TableSpec(
        "holding_costs", ("location",),
        {"location": "str", "cost_per_unit_day_aed": "float"},
        nonneg=("cost_per_unit_day_aed",), correctable=("cost_per_unit_day_aed",),
    ),
    "signals": TableSpec(
        "signals", ("signal_id",),
        {"signal_id": "str", "date": "date", "source": "str", "type": "str", "components": "list",
         "routes": "list", "skus": "list", "locations": "list", "delay_min": "float?", "delay_likely": "float?",
         "delay_max": "float?", "uplift_min": "float?", "uplift_likely": "float?", "uplift_max": "float?",
         "start_day": "int", "end_day": "int", "status": "str"},
    ),
    "planning_parameters": TableSpec(
        "planning_parameters", ("name",),
        {"name": "str", "value": "str", "unit": "str", "description": "str", "changed_by": "str",
         "changed_at": "datetime"},
        correctable=("value",),
    ),
}

# Tables the watcher / Data Hub upload may write. Runtime tables (decisions, corrections_log, ...)
# and the judges' answer key can never be uploaded.
UPLOADABLE = tuple(TABLES)

INBOUND_STATUSES = ("Confirmed", "Unconfirmed")
SIGNAL_STATUSES = ("Unverified", "Confirmed")
SIGNAL_TYPES = ("supply", "demand")


def record_id(table: str, row) -> str:
    spec = TABLES[table]
    return f"{table}:" + "|".join(str(row[k]) for k in spec.key)


def parse_record_id(rid: str) -> Tuple[str, List[str]]:
    table, _, rest = rid.partition(":")
    if table not in TABLES or not rest:
        raise ValueError(f"Unknown record id '{rid}'")
    parts = rest.split("|")
    if len(parts) != len(TABLES[table].key):
        raise ValueError(f"Record id '{rid}' must have {len(TABLES[table].key)} key parts")
    return table, parts
