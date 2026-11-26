# Self-Reconciling Ledger: Project Spec

Read this file at the start of every session. Build ONE phase at a time. Do not start the next phase until the current phase's "Done when" is true and its tests pass.

## 1. The problem (plain language)

Two systems keep their own record of the same money movement, and the records sometimes disagree. Example: the bank's ledger says GH₵500 was sent, the mobile money provider's file says nothing was received. Each disagreement is called a **break**.

Today, an ops officer opens each break, digs through logs, guesses the cause (duplicate, missing, wrong amount, timing cutoff), drafts a correcting entry, and a second person approves it (maker-checker). The slow parts are (1) working out WHY the break happened and (2) the queue of breaks waiting for a human. Meanwhile customers can be out of money for days.

## 2. What this project does

1. A correct, safe double-entry **ledger** (our side of the books)
2. A **reconciliation engine** that matches our ledger against an external partner file and flags breaks
3. An **AI agent** that investigates each break, explains the likely cause, and drafts a correcting entry
4. A **human approval queue**: nothing is posted until a person approves

Goal: turn 15 to 30 minutes of manual detective work per break into a 2 to 5 minute review-and-approve.

## 3. Hard rules (never violate)

- The AI agent NEVER posts ledger entries. It only proposes. A human must approve.
- Every ledger transaction creates debit and credit entries that sum to zero. Entries are immutable (corrections are new entries, never edits or deletes).
- Every transfer endpoint requires an idempotency key. Retrying the same key must never double-post.
- Money is stored as integers (pesewas), never floats.
- All data is synthetic. No real customer or bank data anywhere in the repo.
- Every phase ships with tests.

## 4. Stack

- Python 3.12, FastAPI
- PostgreSQL
- SQLAlchemy + Alembic (migrations)
- Docker Compose (whole system runs with `docker compose up`)
- pytest (unit and integration), Locust (load test)
- Claude API (Anthropic SDK) for the agent
- Simple web UI for the approval queue (FastAPI + Jinja templates or a small React app, keep it minimal)

## 5. Phases

### Phase 0: Setup
- Repo structure, README skeleton (problem section from above), `docker-compose.yml`, `.env.example`, CI that runs tests
- **Done when:** `docker compose up` starts the API and database and `/health` returns OK

### Phase 1: Ledger
- Accounts, transfers, double-entry postings, balance queries
- Idempotency keys on transfers
- Concurrency safety (row locking or optimistic concurrency) so simultaneous transfers cannot overdraw or lose money
- **Done when:** retrying the same transfer never charges twice, debits always equal credits, and tests prove both

### Phase 2: Load test
- Locust script hitting transfers concurrently, including duplicate retries
- **Done when:** 1,000+ concurrent transfers finish with zero balance discrepancies, and results (screenshot or table) are in the README

### Phase 3: Fake partner file generator
- Script that takes ledger transactions and produces a partner CSV, with planted errors: missing, duplicate, wrong amount, timing cutoff (date off by one)
- Also outputs an **answer key** file listing every planted error and its type
- **Done when:** running the generator produces a partner file plus a matching answer key, reproducible with a random seed

### Phase 4: Reconciliation engine
- Match ledger vs partner file by reference, amount, and date
- Exact matches auto-clear. Near matches (small date tolerance) are handled by rules. Everything else becomes a break with a type guess
- **Done when:** engine output is scored against the answer key, and it reports precision and recall for break detection

### Phase 5: AI agent
- For each break, gather context (the ledger entries, the partner row, related transactions), call the Claude API, and return: likely cause, confidence, explanation in plain English, and a proposed correcting entry
- Output must be structured JSON and validated
- **Done when:** diagnosis accuracy vs the answer key is measured on 100+ breaks and the number is reported (e.g. "correct on 85 of 100")

### Phase 6: Approval queue
- UI listing open breaks with the AI explanation and proposed fix
- Approve posts the correcting entry through the normal ledger rules. Reject records a reason
- Maker-checker: the person who triggers a fix cannot be the one who approves it (simple two-user simulation is fine)
- Audit log of every decision
- **Done when:** a break can go from flagged to approved to posted to closed, with an audit trail

### Phase 7: Polish
- Architecture diagram, 3 to 4 short decision notes in `/docs/adr` (why Postgres, why human approval, why integer money, why this matching approach)
- Demo GIF in the README, results table (load test, recon scores, AI accuracy)
- **Done when:** a stranger can clone the repo, run one command, and see the whole flow

## 6. Working instructions for Claude Code

- Start with Phase 0. After each phase, summarize what was built and how to verify it, then stop and wait.
- Keep code simple and readable. Add short comments on anything non-obvious (locking, idempotency, matching rules).
- Prefer a few well-tested modules over many half-built ones.
- If a decision has real tradeoffs, write a short note in `/docs/adr` instead of silently picking.

## 7. Possible upgrades later (do NOT build now)

- Streaming layer so breaks are caught in real time
- Agent learns from past approved fixes
- Graph view to spot repeated suspicious patterns across breaks
