"""Agent 2, the Watcher agent: after the inbox watcher loads a file and re-plans, investigate the cells that got worse.

The inbox watcher's five steps stay as they are (notice, check, load, re-plan, report). This adds a sixth that
runs afterwards in the background: pick the cells What changed made worse, open Lab tabs for them, and leave
a briefing. It never approves, rejects or corrects anything.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from ..config import settings
from ..service import Service
from . import runner

WORSE = ("Short", "Buffer breach")


def pick_cells(svc: Service, wc: Dict[str, Any]) -> List[Tuple[str, str, List[str]]]:
    """Cells worth a look, ranked by AED at stake: stale approvals first, then lost margin and moved net benefit."""
    eng, _ = svc.ensure()
    score: Dict[Tuple[str, str], float] = {}
    why: Dict[Tuple[str, str], List[str]] = {}

    def add(sku, loc, aed, reason):
        k = (sku, loc)
        if k not in eng.state.inventory:
            return
        score[k] = score.get(k, 0) + aed
        why.setdefault(k, []).append(reason)

    for s in wc.get("decisions_turned_stale") or []:
        add(s["sku"], s["location"], 1e6, f"approved decision {s['decision_id']} turned Stale")
    for c in wc.get("changes") or []:
        if "sku" not in c:
            continue
        sku, loc, d = c["sku"], c["location"], c["changes"]
        margin = eng.state.margin(sku)
        lost = d.get("lost_p50")
        if lost and (lost["after"] or 0) > (lost["before"] or 0) + 0.5:
            add(sku, loc, margin * (lost["after"] - (lost["before"] or 0)),
                f"projected lost sales rose from {round(lost['before'] or 0)} to {round(lost['after'])} units")
        st = d.get("status")
        if st and st["after"] in WORSE and st["before"] != st["after"]:
            add(sku, loc, 100, f"status went from {st['before']} to {st['after']}")
        top, net = d.get("top"), d.get("net")
        if top and top["after"]:
            add(sku, loc, 50, f"top option changed to {top['after']}")
        if net and net["before"] is not None and net["after"] is not None and abs(net["after"] - net["before"]) >= 100:
            add(sku, loc, abs(net["after"] - net["before"]),
                f"the top option's net benefit moved from AED {round(net['before']):,} to AED {round(net['after']):,}")
    ranked = sorted((k for k in score if score[k] >= 100), key=lambda k: (-score[k], k))
    return [(k[0], k[1], why[k]) for k in ranked[:settings.agent_watch_cells]]


def on_loaded(svc: Service, event: Dict[str, Any], background: bool = True) -> List[Dict[str, Any]]:
    """Called by the inbox watcher after a file is loaded and re-planned. Returns the queued runs."""
    wc = event.get("what_changed")
    if not settings.agent_watch_enabled or event.get("status") != "Loaded" or not wc:
        return []
    cause, stale = wc.get("cause") or event["file_name"], wc.get("decisions_turned_stale") or []
    eng, _ = svc.ensure()
    jobs = []
    for sku, loc, reasons in pick_cells(svc, wc):
        # the trigger (shown above the briefing) already says what changed, so the briefing starts with the cell
        run = runner.new_run(svc, "watcher", sku, loc, f"{cause}: {'; '.join(reasons)}", None, event["event_id"])
        user_msg = (f"Shared data just changed: {cause}. Nobody asked you; investigate before the planner arrives.\n"
                    f"Workspace: {sku} ({eng.state.skus[sku]['name']}) at {loc}. Planning day {eng.state.day90}.\n"
                    f"Why this cell was flagged: {'; '.join(reasons)}.\n"
                    + (f"Approved decisions now Stale: {', '.join(s['decision_id'] + ' (' + s['sku'] + ' ' + s['location'] + ')' for s in stale)}.\n" if stale else "")
                    + "There is no question to answer: start with the cell's situation, then what you tested, "
                      "what you recommend and the main risk.")
        jobs.append((run, user_msg))
    if not jobs:
        return []

    def work():
        for run, user_msg in jobs:
            runner.execute(svc, run, None, stale, user_msg)
    if background:
        runner.in_background(work)
    else:
        work()
    return [j[0] for j in jobs]
