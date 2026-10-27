"""Agent 1, the Copilot: the planner asks a question in a Scenario Lab workspace and the agent works it out."""
from __future__ import annotations

import threading
from typing import Any, Dict, Optional

from ..config import settings
from ..service import HITLError, Service
from . import runner

_START = threading.Lock()  # the busy check and the new run happen together


def start(svc: Service, sku: str, loc: str, question: Optional[str] = None, background: bool = True) -> Dict[str, Any]:
    q = (question or "").strip()[:500] or None
    with _START:
        if any(r["status"] in ("queued", "running") for r in runner.list_runs(svc, sku, loc, "copilot", 5)):
            raise HITLError("The agent is already working in this workspace. Wait for it to finish.", 409)
        run = runner.new_run(svc, "copilot", sku, loc, f"Planner asked: {q}" if q else "Planner asked for an investigation", q)
    eng, _ = svc.ensure()
    user_msg = (f"Workspace: {sku} ({eng.state.skus[sku]['name']}) at {loc}. Planning day {eng.state.day90}. "
                f"Locations: {', '.join(eng.locs)}.\n"
                + (f'Planner\'s question:\n"""{q}"""' if q else "The planner asked you to investigate this cell and recommend what to do."))
    lead = "Keyless mode: I ran the standard investigation; typed questions need an LLM key." if q and not settings.keyed else None
    if background:
        runner.in_background(runner.execute, svc, run, lead, None, user_msg)
        return run
    return runner.execute(svc, run, lead, None, user_msg)
