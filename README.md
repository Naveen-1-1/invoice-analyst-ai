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

- `make api` (backend on http://127.0.0.1:8000) and `make web` (app on http://127.0.0.1:5180) in two terminals
- Runs → a run's documents → a document: the page with a box around every value read, and each value's confidence
  tier with the evidence behind it (hover a value to find it on the page)
- Each document's page also shows its steps (received, read, checked, matched, decided, booked, paid) and every model
  call behind its reading: what was sent, the answer, the model's reasoning, tokens and time
- **Vendors**: the vendor list (bank accounts, terms, past invoices), purchase orders (ordered, received, invoiced and
  open per line) and deliveries; view-only. **Settings**: every threshold, cap, tolerance, rate and model in use, each
  with the decision that set it; view-only
- `make gen-api` regenerates the frontend's typed client after an API change

## AP checks (inbox mode)

- `make companion`: generate the dev vendor list, POs, deliveries, vendor history, inbox stream and 24 planted
  problems (the labels stay in `data/dev/labels/`, which only `bench/` reads)
- Live: `make worker` (exactly one; it holds the shared LLM rate limiter), then `make inbox-live` (arrivals replayed
  at 2 s per simulated day; `SPEED=0.5 make inbox-live` for faster) and watch the **Board** in the app. Stop and
  restart the worker at any time: interrupted jobs are put back on the queue and nothing already read is read again.
- Upload your own invoices by dropping them on the board: they go to the `manual` workspace, checked against a copy
  of the dev vendors and POs
- `make inbox-dev`: the whole dev inbox at once in one process, then score the checks against the labels
- Each document's Checks tab in the app shows what was found and why

## Decisions and review

- After its checks, each invoice is **approved automatically** only if nothing needs review or blocks payment,
  every key value was read with high confidence, a PO matched, and the total is at most USD 10,000 at the built-in
  approximate rates (`backend/src/invoice_analyst/decision/rules.py`). Each automatic approval records its reasons.
- Everything else goes to the **Review** queue (payment-blocked first, then soonest due) with plain-language labels.
  The review screen shows the page next to the values: correct a value by typing it or clicking the words on the
  page (the checks run again), then approve, approve the received part (VAT in proportion; the rest on hold),
  reject, hold, or have the model read it again. Every action needs a note and goes into the audit trail.
  Keys: `a` approve, `p` received part, `r` reject, `h` hold/release, `x` read again, `j`/`k` next/previous.
- `make e2e`: web tests of the review flow on five dev arrivals replayed from cached readings (no model calls)
- `ia inbox advance RUN`: decide any checked-but-undecided invoices of an inbox run and rewrite its results

## Payments, books and audit

- Approving an invoice (automatically or by you) books it in a built-in double-entry ledger: the cost to the PO
  line's expense account (else the vendor's), VAT to VAT input, withholding to its own account, and what we owe to
  accounts payable, in the invoice's currency, for the company it's addressed to.
- It's then planned into a weekly **payment run** (Thursdays): the last run before it's due, the last run before
  the discount deadline when the vendor gives one, or the next run when overdue. Credit notes are subtracted from
  the same vendor's payment. Paying a run books the payments and marks the invoices paid.
- **Today**: a live inbox uses its simulated clock and pays runs as it passes them; elsewhere you set today, or
  press "Pay the next run" on the **Payments** page (`make pay-due WS=n` pays everything due).
- **Ledger** page: trial balance per currency (with the checks that debits equal credits and that payables equal
  the unpaid invoices), what we owe by age, the journal, and CSV downloads; filter by company.
- **Audit** page: every event in order, and a "chain verified" badge; `make audit-verify` checks every chain.

## Evaluation

- `make score RUN=n` scores a benchmark run with the official scorer; `apbench ext-score n` scores the AP fields it
  leaves out (PO number, tax ID, bank account, terms, addressee, line quantity, unit price and code), only where
  they're printed (`apbench extended-keys` builds that key from the generator's specs)
- The Evaluation page also compares any two scored runs on the same set (from each run's `scorecard.json`, which
  records which fields were right without the answers; `apbench scorecards` writes any missing) and charts how often
  each confidence tier was right
- `make eval-dev BENCH=n INBOX=m` logs a dev reference to `data/eval/EVAL_LOG.jsonl`; the **Evaluation** page shows
  it next to the held-out result
- `make final`: **the** held-out evaluation, once, on a clean committed tree: extraction scored by both
  scorers, a confidence check, and the held-out inbox (checks, decisions, payment timing). About 185 model calls.
  If it's interrupted, run it again: it resumes where it stopped, at the same commit

The held-out set is generated once with `make heldout-generate` from a private `HELDOUT_SEED` in `.env`,
and sealed in `data/heldout/LOCK.json`, together with its companion data (vendors, POs, deliveries, inbox,
planted problems and answer keys, from a seed derived from `HELDOUT_SEED`); `make heldout-verify` checks the seal.
Nothing reads the held-out set except `make final`.
