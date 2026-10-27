"""Inbox watcher (W1-W3). Five steps: notice, check, load, re-plan, report.

The watcher can load and re-plan, but it can never approve, execute or delete: failing
files are moved to quarantine with their reasons and kept for review. After a load, the
watcher agent (agent/watch.py) investigates the cells that got worse, in the background.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

from .config import settings
from .loader import read_csv_text
from .schemas import LOCATIONS, UPLOADABLE
from .service import Service
from .storage import file_hash, now_iso
from .validation import check_file, detect_table

log = logging.getLogger("inventra.watcher")
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def safe_name(name: str) -> str:
    base = Path(name).name or "upload.csv"
    return SAFE_NAME.sub("_", base)[:120]


def _store_copy(folder: Path, name: str, data: bytes, errors: Optional[list] = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    dest = folder / f"{stamp}_{uuid.uuid4().hex[:6]}_{name}"
    dest.write_bytes(data)
    if errors is not None:
        dest.with_suffix(dest.suffix + ".errors.json").write_text(json.dumps(errors, indent=2))
    return dest


def ingest(svc: Service, file_name: str, data: bytes, target_table: Optional[str] = None,
           origin: str = "inbox") -> Dict[str, Any]:
    name = safe_name(file_name)
    fhash = file_hash(data)
    event = {"event_id": f"EVT-{uuid.uuid4().hex[:10]}", "file_name": name, "file_hash": fhash,
             "received_at": now_iso(), "target_table": target_table, "status": None, "rows": 0,
             "errors": [], "what_changed": None, "seen": False}
    with svc.lock:
        svc.ensure()
        # 1. Notice: a file already seen (same content hash) is ignored
        if svc.store.seen_hash(fhash):
            event["status"] = "Duplicate"
            event["errors"] = ["same content as a file already processed; ignored"]
            _store_copy(settings.archive_dir / "duplicates", name, data)
            return _finish(svc, event, origin)
        # 2. Check
        errors = []
        df = None
        if not name.lower().endswith(".csv"):
            errors.append("only .csv files are accepted")
        elif len(data) > settings.max_upload_bytes:
            errors.append(f"file larger than {settings.max_upload_bytes} bytes")
        else:
            try:
                df = read_csv_text(data)
            except Exception as e:  # malformed CSV or not UTF-8
                errors.append(f"cannot read CSV: {type(e).__name__}")
        if df is not None and not errors:
            table = target_table or detect_table(list(df.columns))
            event["target_table"] = table
            if table is None:
                errors.append("header does not match any input table")
            elif table not in UPLOADABLE:
                errors.append(f"table {table} cannot be loaded from a file")
            elif len(df) > settings.max_upload_rows:
                errors.append(f"more than {settings.max_upload_rows} rows")
            else:
                skus = set(svc.engine.state.skus)
                if table == "sku_master" and "sku" in df.columns:
                    skus |= set(df["sku"])
                errors = check_file(table, df, skus, set(LOCATIONS))
            event["rows"] = 0 if df is None else len(df)
        if errors:
            event["status"] = "Quarantined"
            event["errors"] = errors
            path = _store_copy(settings.quarantine_dir, name, data, errors)
            event["quarantine_path"] = str(path.relative_to(settings.runtime_dir)) if path.is_relative_to(settings.runtime_dir) else path.name
            return _finish(svc, event, origin)
        # 3. Load (append-only, with source_file and loaded_at), then archive
        svc.store.append_rows(event["target_table"], df, name, fhash, f"watcher ({origin})")
        _store_copy(settings.archive_dir, name, data)
        # 4. Re-plan
        wc = svc.replan(f"{origin} file {name} loaded into {event['target_table']}", actor="watcher")
        event["status"] = "Loaded"
        event["what_changed"] = wc
        out = _finish(svc, event, origin)
        # 6. Investigate (watcher agent, background): opens Lab tabs and a briefing, never approves
        from .agent import watch as agent_watch
        try:
            out["agent_runs"] = [r["run_id"] for r in agent_watch.on_loaded(svc, out)]
        except Exception:
            log.exception("watcher agent could not start")
        return out


def _finish(svc: Service, event: Dict[str, Any], origin: str) -> Dict[str, Any]:
    # 5. Report: activity feed entry + alert badge (unseen events). Never approves or executes anything.
    row = {k: event[k] for k in ("event_id", "file_name", "file_hash", "received_at", "target_table", "status", "rows",
                                 "errors", "what_changed", "seen")}
    svc.store.insert("inbox_events", row)
    svc.audit("inbox_" + event["status"].lower(), event["file_name"], None,
              {"event_id": event["event_id"], "table": event["target_table"], "rows": event["rows"],
               "errors": event["errors"][:10], "origin": origin}, user="watcher")
    return event


# ---------------------------------------------------------------------- background observer
class InboxWatcher:
    def __init__(self, svc: Service, inbox: Path = None):
        self.svc = svc
        self.inbox = inbox or settings.inbox_dir
        self.observer = None
        self._busy = threading.Lock()

    def start(self) -> None:
        from watchdog.events import FileSystemEventHandler
        from watchdog.observers import Observer
        from watchdog.observers.polling import PollingObserver

        self.inbox.mkdir(parents=True, exist_ok=True)
        watcher = self

        class Handler(FileSystemEventHandler):
            def on_created(self, event):
                if not event.is_directory:
                    threading.Thread(target=watcher.handle, args=(Path(event.src_path),), daemon=True).start()

            def on_moved(self, event):
                if not event.is_directory and Path(event.dest_path).parent == watcher.inbox:
                    threading.Thread(target=watcher.handle, args=(Path(event.dest_path),), daemon=True).start()

        self.observer = PollingObserver(timeout=1) if settings.watcher_polling else Observer()
        self.observer.schedule(Handler(), str(self.inbox), recursive=False)
        self.observer.daemon = True
        self.observer.start()
        for p in sorted(self.inbox.iterdir()):  # files that landed while the app was down
            if p.is_file():
                threading.Thread(target=self.handle, args=(p,), daemon=True).start()

    def stop(self) -> None:
        if self.observer:
            self.observer.stop()
            self.observer.join(timeout=5)

    def handle(self, path: Path) -> None:
        if path.name.startswith(".") or path.name.endswith((".tmp", ".part", ".crdownload")):
            return
        if not self._wait_stable(path):
            return
        with self._busy:
            if not path.exists():
                return
            try:
                data = path.read_bytes()
                ingest(self.svc, path.name, data, origin="inbox")
            except Exception:
                log.exception("watcher failed on %s", path.name)
            finally:
                try:
                    path.unlink()  # a copy is already in archive/ or quarantine/
                except FileNotFoundError:
                    pass

    @staticmethod
    def _wait_stable(path: Path, tries: int = 20) -> bool:
        last = -1
        for _ in range(tries):
            if not path.exists():
                return False
            size = path.stat().st_size
            if size == last:
                return True
            last = size
            time.sleep(0.25)
        return path.exists()
