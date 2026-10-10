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
7. `make test`: backend tests (database rules, label firewall)

The held-out set is generated once with `make heldout-generate` from a private `HELDOUT_SEED` in `.env`,
and sealed in `data/heldout/LOCK.json`; `make heldout-verify` checks the seal.
