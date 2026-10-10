# invoice-analyst-ai

An AI system that does an accounts-payable analyst's job: upload invoices in any format, extract them into one
template (with confidence and page location per field), check them (arithmetic, duplicates, vendor and bank details,
three-way match with POs and deliveries, unusual patterns), send doubtful cases to a person, then schedule payment
and post to a double-entry ledger, with every step visible in the web app and recorded in an audit trail.

Built and evaluated on the InvoiceParser Pro Invoice Extraction Benchmark v1 (see `NOTICE`), plus a held-out set
generated with its generator.

## Setup

1. `make venv`: creates `backend/.venv` and `bench/.venv` (Python 3.12)
2. `make submodule`: fetches the benchmark at its pinned commit
3. `make lock install`: pins and installs the Python dependencies
4. Copy `.env.example` to `.env` (project root) and add your `NVIDIA_API_KEY`
5. `make db-up migrate`: starts Postgres 18 in Docker (port 5433) and creates the tables
6. `make verify-specs`: checks the generator reproduces the published answer keys (181/181)
7. `make test`: backend tests (database rules, LLM client, extraction rules, label firewall)

## Benchmark mode

- `make bench-smoke`: 20 dev documents (2 per category), then the official scorer
- `make bench-dev`: all 181 dev documents, then the official scorer; results in `data/runs/<n>/`
- `make score RUN=n`: score run n again; `PYTHONPATH=bench/src bench/.venv/bin/python -m apbench compare A B`
  compares two runs
- Answers are cached: rerunning the same documents with the same prompt and settings makes no API calls

## Web app

- `make api` (backend on http://127.0.0.1:8000) and `make web` (app on http://127.0.0.1:5173) in two terminals
- Runs → a run's documents → a document: the page with a box around every value read, and each value's confidence
  tier with the evidence behind it (hover a value to find it on the page)
- `make gen-api` regenerates the frontend's typed client after an API change

## AP checks (inbox mode)

- `make companion`: generate the dev vendor list, POs, deliveries, vendor history, inbox stream and 24 planted
  problems (the labels stay in `data/dev/labels/`, which only `bench/` reads)
- `make inbox-dev`: read every arrival, run every check in arrival order, then score the checks against the labels
- Each document's Checks tab in the app shows what was found and why

The held-out set is generated once with `make heldout-generate` from a private `HELDOUT_SEED` in `.env`,
and sealed in `data/heldout/LOCK.json`; `make heldout-verify` checks the seal.
