PY := .venv/bin/python

.PHONY: setup demo test reset drop-sig001 drop-bad drop-dup data docker-build docker-up docker-down docker-logs docker-test docker-wipe docker-drop-sig001 docker-drop-dup docker-drop-bad

setup:
	python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt

demo:            ## run the API on http://127.0.0.1:8000 (docs at /docs); local only
	$(PY) -m uvicorn inventra.api.main:app --host 127.0.0.1 --port 8000

test:
	$(PY) -m pytest -q tests

reset:
	curl -s -X POST http://127.0.0.1:8000/reset

drop-sig001:     ## demo step 1:30: the watcher picks this up with no button press
	mkdir -p runtime/inbox && cp data/inbox_drops/signals_SIG-001.csv runtime/inbox/

drop-dup:
	mkdir -p runtime/inbox && cp data/inbox_drops/signals_SIG-001_resent.csv runtime/inbox/

drop-bad:
	mkdir -p runtime/inbox && cp data/inbox_drops/inventory_update_bad.csv runtime/inbox/

data:            ## regenerate the synthetic dataset byte-for-byte (fixed seed)
	cd generator && ../$(PY) generate.py

# ---------------------------------------------------------------- Docker
docker-build:
	docker compose build

docker-up:       ## API on http://127.0.0.1:8000; drop files into ./inbox
	mkdir -p inbox && docker compose up -d --build && echo "Inventra on http://127.0.0.1:8000/docs"

docker-down:
	docker compose down

docker-logs:
	docker compose logs -f inventra

docker-test:
	docker compose run --rm --no-deps -e WATCHER_ENABLED=0 -e RUNTIME_DIR=/tmp/rt inventra python -m pytest -q -p no:cacheprovider tests

docker-wipe:     ## remove the container and its runtime volume (decisions, logs, DuckDB file)
	docker compose down -v

docker-drop-sig001:
	mkdir -p inbox && cp data/inbox_drops/signals_SIG-001.csv inbox/

docker-drop-dup:
	mkdir -p inbox && cp data/inbox_drops/signals_SIG-001_resent.csv inbox/

docker-drop-bad:
	mkdir -p inbox && cp data/inbox_drops/inventory_update_bad.csv inbox/
