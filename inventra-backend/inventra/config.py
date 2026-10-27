"""Runtime settings, read once from the environment / a local .env file.

The .env file is never committed (see .gitignore) and the API key it may hold is
never returned by any endpoint (see /settings/mode).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _path(name: str, default: Path) -> Path:
    value = os.getenv(name)
    return Path(value).expanduser().resolve() if value else default


@dataclass(frozen=True)
class Settings:
    seed_dir: Path
    judges_dir: Path
    answer_key_dir: Path
    runtime_dir: Path
    db_path: Path
    inbox_dir: Path
    archive_dir: Path
    quarantine_dir: Path
    llm_base_url: Optional[str]
    llm_api_key: Optional[str]
    llm_model: Optional[str]
    llm_fallback_models: List[str]
    llm_timeout_seconds: float
    planner_user: str
    cors_origins: List[str]
    max_upload_bytes: int
    max_upload_rows: int
    watcher_enabled: bool
    watcher_polling: bool
    agent_watch_enabled: bool
    agent_max_steps: int
    agent_watch_cells: int

    @property
    def keyed(self) -> bool:
        return bool(self.llm_api_key and self.llm_base_url and self.llm_model)


def load_settings() -> Settings:
    runtime = _path("RUNTIME_DIR", ROOT / "runtime")
    judges = _path("JUDGES_DIR", ROOT / "data" / "judges")
    return Settings(
        seed_dir=_path("SEED_DIR", ROOT / "data" / "seed"),
        judges_dir=judges,
        answer_key_dir=_path("ANSWER_KEY_DIR", judges / "answer_key"),
        runtime_dir=runtime,
        db_path=_path("DB_PATH", runtime / "inventra.duckdb"),
        inbox_dir=_path("INBOX_DIR", runtime / "inbox"),
        archive_dir=_path("ARCHIVE_DIR", runtime / "archive"),
        quarantine_dir=_path("QUARANTINE_DIR", runtime / "quarantine"),
        llm_base_url=(os.getenv("LLM_BASE_URL") or "").rstrip("/") or None,
        llm_api_key=os.getenv("LLM_API_KEY") or None,
        llm_model=os.getenv("LLM_MODEL") or None,
        # tried in order by OpenRouter when the main model is rate-limited or down
        llm_fallback_models=[m.strip() for m in os.getenv("LLM_FALLBACK_MODELS", "").split(",") if m.strip()],
        llm_timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "15")),
        # Single planner login (design doc section 15): every action is attributed to this user.
        planner_user=os.getenv("PLANNER_USER", "planner"),
        cors_origins=[o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if o.strip()],
        max_upload_bytes=int(os.getenv("MAX_UPLOAD_BYTES", str(5 * 1024 * 1024))),
        max_upload_rows=int(os.getenv("MAX_UPLOAD_ROWS", "50000")),
        watcher_enabled=os.getenv("WATCHER_ENABLED", "1") not in ("0", "false", "False"),
        # Polling observer for bind-mounted folders (Docker Desktop does not forward file events reliably)
        watcher_polling=os.getenv("WATCHER_POLLING", "0") in ("1", "true", "True"),
        # Agents: the watcher agent investigates after every loaded file; it never approves anything
        agent_watch_enabled=os.getenv("AGENT_WATCH_ENABLED", "1") not in ("0", "false", "False"),
        agent_max_steps=int(os.getenv("AGENT_MAX_STEPS", "10")),
        agent_watch_cells=int(os.getenv("AGENT_WATCH_CELLS", "2")),
    )


settings = load_settings()
