# Inventra

AI-assisted inventory planning for a fictional UAE B2B seller (GulfTrade Supplies): what stock to move, reorder or hold, where, when and why, with every number traced to source data. The system recommends; the planner decides.

| Folder | What it is |
|---|---|
| [`inventra-backend/`](inventra-backend/) | Python engine, watcher, LLM layer (optional) and FastAPI API. Synthetic seed data, generator, judges' checklist and answer key, tests. |
| [`inventra-frontend/`](inventra-frontend/) | The Inventra UI, wired to the API. It computes nothing in the browser. |

## Quick start

```bash
cd inventra-backend && make setup     # Python 3.9+ venv and dependencies
cd ../inventra-frontend && ./start.sh # API on 127.0.0.1:8000, UI on http://localhost:3000
```

Open http://localhost:3000. The app runs fully without an LLM key; see `inventra-backend/.env.example` to enable one.

All data is synthetic. No credentials or real buyer or supplier data are included.
