"""DuckDB storage (design doc section 14.1).

Input tables are append-only: every load adds rows tagged with a load id, and the
current view of a table is the latest row per key. Source data is never overwritten;
corrections and signal edits live in their own logs and are applied as overlays when
the engine builds its state (section 15: "Corrections keep the original value").
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import duckdb
import pandas as pd

from .config import settings
from .schemas import LINEAGE_COLUMNS, TABLES

RUNTIME_DDL = [
    """CREATE TABLE IF NOT EXISTS loads (load_id INTEGER, table_name VARCHAR, file_name VARCHAR,
        file_hash VARCHAR, rows INTEGER, loaded_at VARCHAR, loaded_by VARCHAR)""",
    """CREATE TABLE IF NOT EXISTS corrections_log (correction_id VARCHAR, record_id VARCHAR, field VARCHAR,
        old_value VARCHAR, new_value VARCHAR, "user" VARCHAR, timestamp VARCHAR, reason VARCHAR,
        evidence_record_id VARCHAR)""",
    """CREATE TABLE IF NOT EXISTS signal_edits (edit_id VARCHAR, signal_id VARCHAR, field VARCHAR,
        old_value VARCHAR, new_value VARCHAR, "user" VARCHAR, timestamp VARCHAR, reason VARCHAR)""",
    """CREATE TABLE IF NOT EXISTS inbox_events (event_id VARCHAR, file_name VARCHAR, file_hash VARCHAR,
        received_at VARCHAR, target_table VARCHAR, status VARCHAR, rows INTEGER, errors VARCHAR,
        what_changed VARCHAR, seen BOOLEAN)""",
    """CREATE TABLE IF NOT EXISTS scenario_trees (tree_id VARCHAR, sku VARCHAR, location VARCHAR,
        assumptions VARCHAR, created_at VARCHAR)""",
    """CREATE TABLE IF NOT EXISTS scenario_nodes (node_id VARCHAR, tree_id VARCHAR, parent_id VARCHAR,
        decision VARCHAR, source VARCHAR, question_text VARCHAR, results VARCHAR, robustness VARCHAR,
        input_hash VARCHAR, created_at VARCHAR)""",
    """CREATE TABLE IF NOT EXISTS decisions (decision_id VARCHAR, sku VARCHAR, location VARCHAR,
        node_id VARCHAR, options VARCHAR, chosen VARCHAR, approver VARCHAR, timestamp VARCHAR,
        input_hash VARCHAR, status VARCHAR, reason VARCHAR, receipt VARCHAR, path_id VARCHAR)""",
    """CREATE TABLE IF NOT EXISTS audit_log (audit_id VARCHAR, ts VARCHAR, "user" VARCHAR, action VARCHAR,
        target VARCHAR, reason VARCHAR, details VARCHAR)""",
    """CREATE TABLE IF NOT EXISTS lab_tabs (tab_id VARCHAR, sku VARCHAR, location VARCHAR, letter VARCHAR,
        name VARCHAR, decision VARCHAR, settings VARCHAR, status VARCHAR, source VARCHAR, chat VARCHAR,
        results VARCHAR, cache_key VARCHAR, changelog VARCHAR, forked_from VARCHAR, status_reason VARCHAR,
        decision_id VARCHAR, created_at VARCHAR, updated_at VARCHAR)""",
    """CREATE TABLE IF NOT EXISTS review_suggestions (suggestion_id VARCHAR, status VARCHAR, reason VARCHAR,
        "user" VARCHAR, ts VARCHAR, correction_id VARCHAR, proposed INTEGER)""",
    """CREATE TABLE IF NOT EXISTS agent_runs (run_id VARCHAR, kind VARCHAR, sku VARCHAR, location VARCHAR,
        trigger VARCHAR, event_id VARCHAR, question VARCHAR, status VARCHAR, mode VARCHAR, steps VARCHAR,
        answer VARCHAR, tabs VARCHAR, recommended_tab_id VARCHAR, question_for_planner VARCHAR,
        created_at VARCHAR, updated_at VARCHAR)""",
    """CREATE TABLE IF NOT EXISTS llm_cache (cache_key VARCHAR, kind VARCHAR, output VARCHAR, created_at VARCHAR)""",
    """CREATE TABLE IF NOT EXISTS llm_log (ts VARCHAR, kind VARCHAR, cache_key VARCHAR, attempts INTEGER,
        passed BOOLEAN, first_pass BOOLEAN, errors VARCHAR)""",
]

RUNTIME_TABLES = ["loads", "corrections_log", "signal_edits", "inbox_events", "scenario_trees",
                  "scenario_nodes", "decisions", "audit_log", "llm_cache", "llm_log", "lab_tabs", "review_suggestions", "agent_runs"]


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat()


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _raw_columns(table: str) -> List[str]:
    cols = list(TABLES[table].columns)
    return cols + [c for c in LINEAGE_COLUMNS if c not in cols]


class Store:
    """Thread-safe wrapper around one DuckDB connection (API threads + watcher thread)."""

    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.con = duckdb.connect(str(db_path))
        self._ensure_schema()

    # ------------------------------------------------------------------ schema
    def _ensure_schema(self) -> None:
        with self.lock:
            for ddl in RUNTIME_DDL:
                self.con.execute(ddl)
            for table in TABLES:
                cols = ", ".join(f'"{c}" VARCHAR' for c in _raw_columns(table))
                self.con.execute(f'CREATE TABLE IF NOT EXISTS "raw_{table}" ({cols}, _load_id INTEGER, _row INTEGER)')

    def wipe(self) -> None:
        with self.lock:
            for table in TABLES:
                self.con.execute(f'DELETE FROM "raw_{table}"')
            for table in RUNTIME_TABLES:
                self.con.execute(f'DELETE FROM "{table}"')

    # ------------------------------------------------------------------ input tables
    def next_load_id(self) -> int:
        with self.lock:
            return int(self.con.execute("SELECT COALESCE(MAX(load_id), 0) + 1 FROM loads").fetchone()[0])

    def append_rows(self, table: str, df: pd.DataFrame, file_name: str, fhash: str, user: str) -> int:
        """Append validated rows as a new load. Never updates or deletes earlier rows."""
        cols = _raw_columns(table)
        out = pd.DataFrame({c: (df[c] if c in df.columns else None) for c in cols})
        out = out.astype(object).where(out.notna(), None)
        for c in cols:
            out[c] = out[c].map(lambda v: None if v is None else str(v))
        loaded_at = now_iso()
        out["source_file"] = file_name
        out["loaded_at"] = loaded_at
        if table != "inventory":  # inventory's as_of is a data field; others default to load time
            out["as_of"] = out["as_of"].fillna(loaded_at) if "as_of" in out else loaded_at
        with self.lock:
            load_id = self.next_load_id()
            out["_load_id"] = load_id
            out["_row"] = range(1, len(out) + 1)
            self.con.register("_incoming", out)
            self.con.execute(f'INSERT INTO "raw_{table}" SELECT * FROM _incoming')
            self.con.unregister("_incoming")
            self.con.execute("INSERT INTO loads VALUES (?, ?, ?, ?, ?, ?, ?)",
                             [load_id, table, file_name, fhash, len(out), loaded_at, user])
        return load_id

    def current(self, table: str) -> pd.DataFrame:
        """Latest row per key (string values), with lineage columns."""
        key = list(TABLES[table].key)
        with self.lock:
            df = self.con.execute(f'SELECT * FROM "raw_{table}" ORDER BY _load_id, _row').df()
        if df.empty:
            return df
        return df.drop_duplicates(subset=key, keep="last").reset_index(drop=True)

    def seen_hash(self, fhash: str) -> bool:
        with self.lock:
            n = self.con.execute("SELECT COUNT(*) FROM loads WHERE file_hash = ?", [fhash]).fetchone()[0]
            m = self.con.execute("SELECT COUNT(*) FROM inbox_events WHERE file_hash = ? AND status <> 'Duplicate'",
                                 [fhash]).fetchone()[0]
        return (n + m) > 0

    # ------------------------------------------------------------------ generic runtime helpers
    def insert(self, table: str, row: Dict[str, Any]) -> None:
        cols = list(row)
        vals = [json.dumps(v, default=str) if isinstance(v, (dict, list)) else v for v in row.values()]
        q = ", ".join("?" for _ in cols)
        names = ", ".join(f'"{c}"' for c in cols)
        with self.lock:
            self.con.execute(f'INSERT INTO "{table}" ({names}) VALUES ({q})', vals)

    def query(self, sql: str, params: Optional[Iterable[Any]] = None) -> List[Dict[str, Any]]:
        with self.lock:
            cur = self.con.execute(sql, list(params or []))
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def execute(self, sql: str, params: Optional[Iterable[Any]] = None) -> None:
        with self.lock:
            self.con.execute(sql, list(params or []))

    def count(self, table: str) -> int:
        with self.lock:
            return int(self.con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


_store: Optional[Store] = None


def get_store() -> Store:
    global _store
    if _store is None:
        _store = Store(settings.db_path)
    return _store


def set_store(store: Store) -> None:
    global _store
    _store = store
