# Inventra frontend (joined to the backend)

This is the Inventra UI (from `Inventra.html`) wired to the Inventra backend API. The browser computes nothing:
- **Removed from the original file:** the in-browser dataset, engine, replay and validator, and the state it kept in localStorage.
- **Now done by the backend:** every number, check, approval and correction, through the API in `../inventra-backend`. The backend is unchanged.

## Run

```bash
./start.sh            # backend on 127.0.0.1:8000 + frontend on http://localhost:3000
```

or run them separately:

```bash
cd ../inventra-backend && make demo        # API
python3 serve.py                           # UI on http://localhost:3000
```

Open **http://localhost:3000**, not 127.0.0.1:3000. The backend's default CORS setting allows exactly `http://localhost:3000`. To use an API at another address, add `?api=http://host:port` to the URL. Also add that origin to the backend's `CORS_ORIGINS` environment setting.

## What each tab calls

| Tab | Endpoints |
|---|---|
| Command Center | `GET /plan`, `POST /decisions` (approve / reject), `GET /inbox/events` |
| Data Hub | `GET /stock`, `GET /data/validation`, `POST /data/upload/{table}`, `GET/POST /corrections` |
| Demand Forecast | `GET /forecast` (daily / weekly), `GET /forecast/accuracy` |
| Inventory Network | `GET /network/projection`, `GET /network/ageing`, `POST /drafts/po` |
| Disruptions | `GET /signals`, `PATCH /signals/{id}`, `GET /signals/{id}/impact` |
| Scenario Lab | `POST /agent/copilot`, `GET /agent/runs` (Agent panel), `GET /lab/workspace`, `POST/GET/PATCH /lab/tabs…`, `…/fork`, `…/decision`, `…/chat`, `…/review`, `GET /lab/compare`, `GET /lab/event-impact` |
| Decisions | `GET /decision-log`, `GET /decision-log/{id}`, `POST /decision-log/{id}/explain`, `GET /track-record`, `POST /suggestions/{id}/review`, plus `GET /decisions/{id}` and `POST /decisions/{id}/recalculate` for real receipts |
| Evaluation | `POST /evaluation/replay`, `GET /evaluation/report` |
| Shared | `GET /settings/mode`, `GET /freshness`, `POST /reset` |

## Human-in-the-loop behaviour in the UI
- **Approving:** every approval sends the input hash the planner was shown. If the plan changed since, the server refuses with 409; the UI shows why and refreshes.
- **Reasons:** reject, override, Fragile approvals, signal edits, corrections and confirming an unconfirmed PO all ask for a reason.
- **Blocked approvals:** Harmful or rule-failing paths can't be approved. The server re-checks every path on the shared plan, never on sandbox assumptions.
- **Evidence:** if a stock correction has a cycle count, the server requires it as evidence, and the form fills it in for the planner.
- **Recalculate:** recalculating a Stale decision shows the fresh option; it is approved only when the planner presses Approve.
- **Typed what-ifs:** they only fill the builder. Nothing runs until Run is pressed. They are available only when the backend has an LLM key.
- **Background loads:** the activity feed polls every 6 s, so files loaded by the backend's inbox-folder watcher appear here, with What changed.

## Reduced vs the original file (no backend endpoint exists for these)
- **Removed action:** you can't undo a correction (record a new one instead).
- **Data Hub tables:** the hub shows the tables the API serves. SKU master, lanes, holding costs, lead history, batches and cycle counts appear inside decision receipts instead.
- **Network projection charts:** they show the Do-nothing path and band. Each Scenario Lab tab plots its own decision against Do nothing.
- **Planning parameters:** current values aren't served. The panel shows changes made through corrections and lets you submit new ones.
- **Evaluation checklist:** it appears after the replay runs, because the server scores it as part of the replay.
- **Local-only state:** browser storage keeps only conveniences: theme, sound, keyless display, the tree id per SKU-location, and "not yet" answers to the PO question.

## Scenario Lab
- **Agent panel:** the chat column has an **Agent / Tab** switch. Agent shows Copilot runs and Watcher briefings for the workspace, each with its live step trace, its answer and buttons to open the tabs it made. With a key you can ask a question. Without one, **Investigate this cell** runs the rule-based agent. Watcher briefings also appear in Command Center alerts, with a toast when one is ready.
- **Layout:** follows the "Scenario Lab" spec and mockups, in the site's colours and Geist font.
- **Tab bar:** a pinned Do nothing tab, then A, B, C… tabs with tick boxes, "+ New decision" and "Compare 2 selected".
- **Inside a tab:** chat on the left, the settings sidebar on the right (Hide settings slides it away), and the results in the middle, in this order:
  1. Summary cards
  2. Projected stock with the P10–P90 band
  3. Stockout probability per location
  4. Net-benefit bridge
  5. Costs, ageing and holding
  6. Event impact table
  7. Assumptions and data quality
  8. Alternatives checked
  9. Breaking Point panel
  10. Approve / Reject / Send back
- **Compare view:** a verdict, read-only setting chips per side, a mismatch warning, headline metrics with the better value in green, and charts on the same scale. You can approve either side from here.
- **Chat:** typed what-ifs only need the backend's LLM key. Without one, the chat column shows a form instead.

## Decisions section
The section has three sub-tabs: Sandbox (jumps to the Scenario Lab), Decision Log and Track record.
- **Decision Log:** count cards, a filterable and searchable log, and a "Frozen at approval" panel.
- **Record view:** "Why this label", a chat that explains from the record, the expected → realised net benefit, WAPE vs baseline, the forecast interval, assumptions held or crossed, pay-off, the frozen record, and the suggested next step with Approve / Reject / Send back. Real decisions show "Awaiting actuals" and their full receipt.
- **Track record:** interval coverage, average predicted vs realised benefit, confidence, bias by category and location, the assumptions that failed most often, and outcome-label counts.
- **No time machine.** `DL-` rows are labelled demo history.
