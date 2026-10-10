PY := python3.12
BACKEND_PY := backend/.venv/bin/python
BENCH_PY := bench/.venv/bin/python
APBENCH := PYTHONPATH=bench/src $(BENCH_PY) -m apbench
IA := PYTHONPATH=backend/src $(BACKEND_PY) -m invoice_analyst

.PHONY: help venv lock install submodule db-up db-down verify-specs heldout-generate heldout-verify migrate test bench-smoke bench-dev score api web gen-api companion inbox-dev ap-eval worker inbox-live e2e audit-verify pay-due

help:
	@echo "venv       create backend/.venv and bench/.venv (Python 3.12)"
	@echo "lock       pin exact versions with hashes (pip-compile)"
	@echo "install    install the pinned versions into both venvs (pip-sync)"
	@echo "submodule  fetch the benchmark at its pinned commit"
	@echo "db-up      start Postgres in Docker and wait until it is healthy"
	@echo "migrate    apply database migrations (alembic upgrade head)"
	@echo "test       run the backend tests (uses a throwaway test database)"
	@echo "bench-smoke  extract 20 dev documents (2 per category) and score them"
	@echo "bench-dev    extract all 181 dev documents and score them"
	@echo "score RUN=n  score run n with the official scorer"
	@echo "api        run the backend API on http://127.0.0.1:8000 (reloads on change)"
	@echo "web        run the web app on http://127.0.0.1:5173 (needs make api)"
	@echo "gen-api    regenerate the frontend's typed API client from the backend"
	@echo "companion  generate dev vendors, POs, deliveries, history, inbox stream and planted problems"
	@echo "worker     process the job queue (arrivals, reading, checks); run exactly one"
	@echo "inbox-live start a dev inbox replayed in simulated time (needs make worker); watch it on the board"
	@echo "inbox-dev  run the whole dev inbox at once in this process, then score the checks"
	@echo "ap-eval RUN=n  score inbox run n's checks and decisions against the labels"
	@echo "audit-verify  recompute every workspace's audit chain (fails if any event was altered)"
	@echo "pay-due WS=n  pay every planned payment run up to workspace n's today"
	@echo "e2e        web tests of the review flow (Playwright, installed Chrome; starts api/web if needed)"
	@echo "verify-specs  check the generator specs (seed 42) reproduce the published answer keys"
	@echo "heldout-generate  render and seal the held-out set from HELDOUT_SEED (once)"
	@echo "heldout-verify    check the sealed held-out set is intact"

venv:
	test -d backend/.venv || $(PY) -m venv backend/.venv
	test -d bench/.venv || $(PY) -m venv bench/.venv
	$(BACKEND_PY) -m pip install --quiet --upgrade pip pip-tools
	$(BENCH_PY) -m pip install --quiet --upgrade pip pip-tools

lock:
	cd backend && .venv/bin/pip-compile --quiet --generate-hashes --strip-extras -o requirements.txt requirements.in
	cd backend && .venv/bin/pip-compile --quiet --generate-hashes --strip-extras -o requirements-dev.txt requirements-dev.in
	cd bench && .venv/bin/pip-compile --quiet --generate-hashes --strip-extras -o requirements.txt requirements.in

install:
	cd backend && .venv/bin/pip-sync requirements.txt requirements-dev.txt
	cd bench && .venv/bin/pip-sync requirements.txt

BENCH_REPO := https://github.com/DrewKraken/invoice-extraction-benchmark.git
BENCH_DIR := third_party/invoice-extraction-benchmark
BENCH_COMMIT := 467c815

submodule:
	test -e $(BENCH_DIR)/.git || git submodule add $(BENCH_REPO) $(BENCH_DIR)
	git submodule update --init $(BENCH_DIR)
	git -C $(BENCH_DIR) checkout --quiet $(BENCH_COMMIT)
	@echo "benchmark at $$(git -C $(BENCH_DIR) rev-parse --short HEAD)"

db-up:
	docker compose up -d --wait db

db-down:
	docker compose down

verify-specs:
	$(APBENCH) verify-specs

heldout-generate:
	$(APBENCH) heldout generate

heldout-verify:
	$(APBENCH) heldout verify

migrate:
	cd backend && .venv/bin/alembic upgrade head

test:
	cd backend && .venv/bin/pytest -q

bench-smoke:
	$(IA) bench run --smoke --label smoke
	$(MAKE) score RUN=latest

bench-dev:
	$(IA) bench run --label dev
	$(MAKE) score RUN=latest

score:
	$(APBENCH) score $(if $(filter latest,$(RUN)),$$(ls -d data/runs/[0-9]* | tail -1),$(RUN))

api:
	PYTHONPATH=backend/src $(BACKEND_PY) -m uvicorn invoice_analyst.api.main:app --host 127.0.0.1 --port 8000 --reload --reload-dir backend/src

web:
	cd frontend && npm run dev

gen-api:
	PYTHONPATH=backend/src $(BACKEND_PY) -c "import json; from invoice_analyst.api.main import app; print(json.dumps(app.openapi(), indent=1))" > frontend/openapi.json
	cd frontend && npx orval --config orval.config.ts

companion:
	$(APBENCH) companion --set dev

worker:
	$(IA) worker

inbox-live:
	$(IA) inbox run --set dev --label live --speed $(or $(SPEED),2)

inbox-dev:
	$(IA) inbox run --set dev --label dev --speed 0 --worker
	$(MAKE) ap-eval RUN=latest

audit-verify:
	$(IA) audit verify

pay-due:
	$(IA) payments run-due $(WS)

e2e:
	cd frontend && npx playwright test

ap-eval:
	$(APBENCH) ap-eval $(if $(filter latest,$(RUN)),$$(ls -d data/runs/[0-9]* | tail -1),$(RUN))
