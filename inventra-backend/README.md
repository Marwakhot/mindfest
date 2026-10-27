# Inventra backend

Inventra is an AI-assisted inventory planning workspace for GulfTrade Supplies, a fictional UAE B2B seller. It was previously called StockPilot. It tells the planner what stock to move, reorder or hold, where, when and why, and it proves every number against the source data. This repository is the backend (engine, watcher, LLM layer and API), built to *StockPilot — System Design Document v2* with no added features.

**The system recommends; the planner decides.** Nothing executes. Transfers and purchase orders exist only as drafts with staff instructions.

## Setup and run

```bash
make setup     # python3 -m venv .venv && pip install -r requirements.txt  (Python 3.9+)
make demo      # API on http://127.0.0.1:8000, interactive docs at /docs
make test      # 57 tests: section 6 formulas on the worked example, validator, keyed LLM (mocked), full demo flow
```

### With Docker
```bash
make docker-up            # docker compose up -d --build → http://127.0.0.1:8000/docs
make docker-drop-sig001   # copies SIG-001 into ./inbox; the watcher re-plans on its own
make docker-test          # runs the test suite inside the image
make docker-wipe          # removes the container and its runtime volume
```
- The container runs as a non-root user with a read-only root filesystem, all capabilities dropped and `no-new-privileges`.
- The port is published on `127.0.0.1` only.
- The DuckDB file, archive and quarantine live in the `inventra-runtime` volume. `./inbox` on the host is the watched inbox, which the watcher polls because Docker Desktop doesn't forward file events.
- LLM keys come from an optional `.env` at run time and are never copied into the image (`.dockerignore`).

On first start, the app loads the seeded dataset into `runtime/inventra.duckdb`. `POST /reset` reloads it. The app runs fully in **keyless mode**. To enable LLM explanations and typed questions, copy `.env.example` to `.env` and set `LLM_BASE_URL`, `LLM_API_KEY` and `LLM_MODEL` (OpenRouter).

### Demo helpers
| Step | Command |
|---|---|
| Drop SIG-001 into the inbox (watcher re-plans with no clicks) | `make drop-sig001` |
| Resend the same file (Duplicate) | `make drop-dup` |
| Drop the malformed inventory file (Quarantined) | `make drop-bad` |
| Regenerate the dataset byte-for-byte | `make data` |

## Layout
| Path | Contents |
|---|---|
| `inventra/forecast.py` | Section 7: stockout correction, imputation, weighted base rate, day-of-week (kept only if backtest WAPE drops), demand signals, pooled P10/P90 bands, confidence |
| `inventra/engine.py` | Section 6: stock path, spare, need, day-by-day simulation with FIFO batches, net benefit, recommendation rule, robustness grid, input hash |
| `inventra/candidates.py` | Option generation (single, split, naive, reorder, bring forward, reduce/delay, wait, clearance), urgency |
| `inventra/plan.py` | Portfolio allocation (most urgent first; approvals lock spare), ranked queue, rejected list, KPIs, S8 question |
| `inventra/outcomes.py` | Decision Log, outcome review (Hit / Right call, unlucky / Wrong call / Inconclusive), track record, suggestions applied only through the correction flow |
| `inventra/lab.py` | Scenario Lab tabs: one decision per tab, per-tab disruption settings, simulated-path statistics (stockout probability, net-benefit range), net-benefit bridge, Breaking Point panel, Compare view, event impact, Approve / Reject / Send back |
| `inventra/tree.py` | Earlier decision-tree API (still served; the UI now uses `/lab`) |
| `inventra/service.py` | Global changes (corrections, signal edits), re-plan + What changed, Stale marking, approvals, audit |
| `inventra/watcher.py` | Inbox watcher: notice → check → load → re-plan → report |
| `inventra/llm/` | OpenRouter client, fact sets, section 9.3 validator, cache, keyless fallback, typed-question parsing |
| `inventra/replay.py` | Evaluation replay vs baseline, constraint breaches, answer-key checklist; **the only reader of the answer key** |
| `data/seed`, `data/inbox_drops`, `data/judges` | Copied from `stockpilot-data`; the answer key is under `data/judges/answer_key` |

## API (section 14.3)
`POST /reset` · `GET /settings/mode` · `POST /data/upload/{table}` · `GET /inbox/events` · `POST /inbox/events/seen` · `GET /data/validation` · `GET /stock` · `POST|GET /corrections` · `GET /forecast` (`granularity=daily|weekly`) · `GET /forecast/accuracy` · `GET /network/matrix` · `GET /network/projection` · `GET /network/ageing` · `GET /freshness` · `GET /signals` · `PATCH /signals/{id}` · `GET /signals/{id}/impact` · `GET /plan` · `GET /what-changed` · `POST /scenario/trees` · `GET /scenario/trees/{id}` · `PATCH /scenario/trees/{id}/assumptions` · `POST /scenario/trees/{id}/nodes` · `POST /scenario/parse` · `GET /explain/{node_id}` · `GET /explain/recommendation` · `GET /scenario/nodes/{id}/breaking-points` · `POST|GET /decisions` · `GET /decisions/{id}` · `POST /decisions/{id}/recalculate` · `POST /drafts/po` · `POST /evaluation/replay` · `GET /evaluation/report` · `GET /audit` · Scenario Lab tabs: `GET /lab/workspace` · `POST /lab/tabs` · `GET/PATCH /lab/tabs/{id}` · `POST /lab/tabs/{id}/fork` · `POST /lab/tabs/{id}/decision` · `POST /lab/tabs/{id}/chat` · `POST /lab/tabs/{id}/review` · `GET /lab/compare` · `GET /lab/event-impact`

`POST /decisions` takes `action`: `approve` / `reject` (one option from the queue) or `approve_path` (a tree node).

## Human-in-the-loop guardrails
- **Only the planner decides.** The watcher can load and re-plan, but it can never approve, execute or delete. The LLM only explains or fills builder fields. A typed question never runs until the planner presses Run.
- **Approvals quote what was seen.** Every approval must send the `expected_input_hash` it was shown. If inputs changed since then, the API refuses with 409 and returns What changed.
- **Approvals are checked against the shared plan.** A path is re-checked against the shared plan, never against sandbox assumptions. A path with a Harmful or rejected decision cannot be approved. A Fragile path needs a reason.
- **Reasons are required.** Reject, override, a Fragile approval, a correction and a signal edit each need a reason of up to 500 characters.
- **One active decision per SKU and location.** A Stale decision releases its spare-stock lock. **Recalculate** returns a fresh recommendation but never re-approves.
- **Draft PO guardrail.** A quantity outside the safe range (P10 need to max cover) shows the risk in numbers. Saving it requires an override reason and is recorded as **Overridden**.
- **Unconfirmed inbound is excluded until the planner confirms it.** S8 asks one targeted question when confirming would change a top option.

## Data-security guardrails
- **Source data is never overwritten.** Loads are append-only with source file, row, load time and file hash. Corrections and signal edits are overlay logs that keep the old value. Where a cycle count exists, a stock correction must cite it as evidence.
- **Uploads are restricted.** Only input tables can be uploaded; decisions, logs and the answer key cannot. Uploads must be `.csv`, UTF-8, within the size and row limits, and pass schema and value checks. File names are sanitised.
- **Bad files are kept.** A failing file is quarantined with its errors next to it, and nothing re-plans. Duplicate files (same content hash) are archived and ignored.
- **The answer key is isolated.** It is read only by `replay.py`, after the day-90 plan is built. The API returns aggregate results and the checklist, never answer-key rows.
- **The LLM sees the minimum.** It receives only the computed fact set, or the question plus lists of valid IDs. Every output is validated: numbers must match cited facts, causes must cite reasons, Assumptions must be named, and no outside SKUs or locations may appear. Unknown builder fields or IDs are rejected.
- **The API key stays on the server.** It lives only in `.env`, which is git-ignored. It is never logged or returned, and `/settings/mode` reports only keyed or keyless.
- **The API surface is locked down.** The server binds to `127.0.0.1`, and CORS is limited to `CORS_ORIGINS`. Request bodies reject unknown fields. Errors never include stack traces. Responses carry `nosniff`, `DENY` and `no-store` headers.
- **Everything is audited.** The audit log (`GET /audit`) records every correction, signal edit, approval, rejection, override, stale flag, reset and inbox event.
- **The data is synthetic.** It contains no credentials and no identifiable buyer or supplier data. Counters are staff IDs.

## Interpretations where the design doc is silent
These are documented choices, not new features.
- **Base-rate weights:** weeks in the 28-day window are weighted 1, 2, 3 and 4 (most recent highest).
- **Stockout correction:** kept unless it *raises* backtest WAPE. On this data it ties, because no stockout falls inside a backtest base window. Backtest errors are measured on in-stock, non-missing days.
- **Overlapping supply signals:** if two signals hit the same PO, the larger delay applies (delays do not add). Route signals (SIG-002) delay open POs only; component signals (SIG-001) also delay new orders, as in the section 8 table.
- **Reserved stock:** taken from the oldest batches. Units with no batch record are treated as fresh (tagged Assumption).
- **Portfolio allocation order:** first breach day, then projected lost sales.
- **Delaying an excess PO:** the delay runs until the units are needed (the first day stock without the PO falls to safety stock).

## Known result on live data
Unit tests on the section 10.4 fixture reproduce the doc exactly:
- Before SIG-001: Dubai to Abu Dhabi, 180. The split ranks lower, and the full transfer of 210 is rejected because Dubai falls to 30.
- After SIG-001: the split of 180 + 60 is Robust, and the full transfer of 270 is Harmful (30 units lost in Dubai).
- After the cycle-count correction: back to Dubai only.

On the live seeded data, the Chargers P90 band is narrower than the fixture (Dubai P90 247 vs 260). Dubai's spare is therefore 190, and after SIG-001 Dubai-only 190 still beats the split. Checklist case 1 is reported as **missed**. Case 5's Sharjah transfer also appears only as the full-need option (live Sharjah spare is 0).

## Scenario Lab tabs (added after the design doc, from "Scenario Lab: A Decision Playground")
- **Tabs:** one decision per tab, plus a pinned Do nothing tab. Each tab has its own settings: shipment delay, component supply shortage, shipping-route delay, chance a delay happens, demand sensitivity and transfer cost change. Changing a setting re-runs only that tab and never touches the shared plan.
- **Supplied events only:** shortage and route toggles can only switch on events from the dated signal register that match the SKU (409/422 otherwise). Severity maps to the event's own min / likely / max delay.
- **Statistics:** stockout probability, expected lost sales and the net-benefit range come from 80 simulated demand paths. Each path draws the 30-day demand level per location from that cell's P10/P50/P90 band, and whether the delay happens. Charts, timing and breaking points use the base case.
- **Discount:** discount scenarios are new and Lab-only. Demand rises by an **assumed** sell-through uplift while the oldest units are marked down, and the margin given up is charged. The Breaking Point panel shows the uplift needed to pay off. Net benefit now uses margin × extra units sold, which is identical to the design doc's margin × lost sales avoided whenever demand is unchanged.
- **Approval:** Approve re-checks the decision on the shared plan (rule, robustness, input hash, one active decision per cell). Reject needs a reason. Send back needs a reason and records no decision. Approved and rejected tabs are locked; fork them to test changes.

## Agents (Copilot and Watcher agent)
Two agents investigate with the same tools (`inventra/agent/tools.py`): `get_cell`, `get_network`, `list_options`, `create_scenario` (opens a Scenario Lab tab), `stress_test` (re-runs a tab under harsher settings without saving) and `compare_scenarios`. **No tool can approve, reject, correct or execute.** The planner decides.
- **Copilot** (`agent/copilot.py`): the planner asks in the Scenario Lab's Agent panel. With an LLM key, the model picks the tools in a loop (OpenRouter tool calling, at most `AGENT_MAX_STEPS`) and ends with a briefing. Without one, a rule-based investigation runs the same tools in a fixed order.
- **Watcher agent** (`agent/watch.py`): after the inbox watcher loads a file and re-plans, it ranks the cells What changed made worse (stale approvals first, then AED at stake) and investigates the top `AGENT_WATCH_CELLS` in the background.
- **Grounded answers:** every number and ID in an LLM briefing must appear in a tool result. A failing answer is sent back once, then replaced by the rule-based briefing.
- **Lab tabs:** the agents open tabs named `Agent · …` (source `Agent`) and reuse a tab that already tests the same decision and settings. At most 4 scenarios per run. All agent actions are audited as user `agent`.
- **Endpoints:** `POST /agent/copilot` (202, returns the run) · `GET /agent/runs` (`sku`, `location`, `kind`) · `GET /agent/runs/{id}` (live trace while running).

## Decision Log and track record
- **Endpoints:** `GET /decision-log`, `GET /decision-log/{id}`, `POST /decision-log/{id}/explain`, `GET /track-record`, `POST /suggestions/{id}/review`.
- **Two kinds of record:**
  - Real decisions are frozen at approval and stay **Awaiting actuals**. There is no time machine, so no actuals exist for them yet.
  - `DL-` records are **demo history** from `data/seed/demo_decision_history.json` (regenerate with `python3 generator/demo_history.py`). They are synthetic decisions with synthetic actuals, labelled demo everywhere.
- **Computed by the server, never stored:** outcome labels, WAPE vs baseline, bias, interval coverage, assumption failures and suggestions.
- **Label rules:**
  - Inconclusive: a supplied distortion note exists.
  - Right call, unlucky: a breaking point was crossed and the benefit fell short.
  - Wrong call: the assumptions held but there was no benefit.
  - Hit: anything else that paid off.
- **Headline score:** none until 10 decisions are reviewed. A bias cell needs at least 2 decisions.
- **Suggestions:** two or more under-forecasts above 5% in one category and location produce a proposal: raise safety stock by the mean under-forecast, rounded up to 5. Approving it records a normal correction (old value kept, re-plan, Stale marking). Reject and Send back need a reason and change nothing.
