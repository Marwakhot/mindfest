"""Seeded dataset load and one-click reset (G4)."""
from __future__ import annotations

from pathlib import Path
from typing import Dict

import pandas as pd

from .config import settings
from .schemas import LOCATIONS, TABLES
from .storage import Store, file_hash
from .validation import check_file


def read_csv_text(data: bytes) -> pd.DataFrame:
    """CSV bytes -> all-text DataFrame. Blank cells stay empty strings so types are checked explicitly."""
    from io import BytesIO
    return pd.read_csv(BytesIO(data), dtype=str, keep_default_na=False, encoding="utf-8")


def load_seed(store: Store, seed_dir: Path = None, user: str = "system (seed)") -> Dict[str, int]:
    seed_dir = seed_dir or settings.seed_dir
    store.wipe()
    loaded: Dict[str, int] = {}
    order = ["sku_master"] + [t for t in TABLES if t != "sku_master"]
    known_skus = set()
    for table in order:
        path = seed_dir / f"{table}.csv"
        if not path.exists():
            continue
        data = path.read_bytes()
        df = read_csv_text(data)
        if table == "sku_master":
            known_skus = set(df["sku"])
        errors = check_file(table, df, known_skus, set(LOCATIONS))
        if errors:
            raise ValueError(f"seed file {path.name} failed validation: {errors[:5]}")
        store.append_rows(table, df, path.name, file_hash(data), user)
        loaded[table] = len(df)
    return loaded
