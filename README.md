# Recon Ledger

A mini bank ledger that reconciles itself: it checks its own records against an external partner's records, finds the mismatches ("breaks"), and uses AI to explain why each one happened and propose a fix. A human approves every fix.

## The problem

Two systems keep their own record of the same money movement, and the records sometimes disagree. Example: the ledger says GH₵500 was sent, the mobile money provider's file says nothing was received.

Today an ops officer investigates each break by hand (logs, files, emails to the partner), works out the cause, and drafts a correcting entry that a second person approves. The slow parts are finding out *why* it broke and the queue of breaks waiting for a human. Meanwhile customers can be out of money for days.

## What this project does

1. Double-entry **ledger** with idempotent transfers
2. **Reconciliation engine** that matches the ledger against a partner file and flags breaks
3. **AI agent** that diagnoses each break and drafts a correcting entry
4. **Approval queue** where a human approves or rejects (the AI never posts entries itself)

All data is synthetic.

## Status

- [x] Phase 0: Setup
- [x] Phase 1: Ledger
- [x] Phase 2: Load test
- [ ] Phase 3: Fake partner file generator
- [ ] Phase 4: Reconciliation engine
- [ ] Phase 5: AI agent
- [ ] Phase 6: Approval queue
- [ ] Phase 7: Polish

See [SPEC.md](SPEC.md) for the full plan.

## Run it

```
cp .env.example .env      # then edit the password
docker compose up --build
```

Check it works: open http://localhost:8000/health and you should see `{"status":"ok"}`.

## Run tests

Tests need Postgres, so run them inside Docker (each run uses its own throwaway database, your data is never touched):

```
docker compose run --rm api pytest -q
```

## The ledger (Phase 1)

Money is stored as whole pesewas (GH₵1 = 100), never decimals.

| Rule | How it's enforced |
|---|---|
| Debits and credits always sum to zero | Each transfer writes two signed entries (-amount, +amount) in one database transaction |
| No double charging on retries | `Idempotency-Key` header, backed by a UNIQUE column so even simultaneous retries create only one transfer |
| No overdrafts or lost money under concurrency | Both accounts are locked (`SELECT ... FOR UPDATE`) in id order, so no overdraft and no deadlocks |
| Entries are never edited | A database trigger blocks UPDATE and DELETE on `entries`. Fixes are new entries |
| Books can be audited | `GET /audit/integrity` checks totals and cached balances |

Try it (interactive docs at http://localhost:8000/docs):

```
POST /accounts       {"name": "bank-cash", "allow_negative": true}
POST /accounts       {"name": "alice"}
POST /transfers      header Idempotency-Key: abc-1
                     {"from_account_id": 1, "to_account_id": 2, "amount": 50000}
```
Send the same transfer again with the same key: you get the same transaction back and Alice is only charged once.

## Load test (Phase 2)

200 simulated users hit the API at the same time for 30 seconds, mixing normal transfers, payments into one shared "merchant" account (everyone fighting over the same row), retries with the same Idempotency-Key, overdraw attempts, and a few low-balance accounts that many users try to drain at once. When it stops, the test checks the books.

```
docker compose --profile loadtest run --rm loadtest
```

Results are written to [loadtest/results/latest.md](loadtest/results/latest.md). What it proves:

| Check | Why it matters |
|---|---|
| All entries sum to zero, cached balances match entries | Money is never created or lost |
| Transaction count equals the number of successful new transfers | Retries never double-post, nothing is lost |
| Customers + merchant hold exactly what was funded | Money is conserved |
| Low-balance accounts never go negative and never pay out more than they hold | No overdrafts under concurrency |
| Every retry replays the original result, every overdraw is declined | Idempotency and funds checks hold under load |

### Latest results

200 concurrent users for 30 seconds, one API worker, running in Docker on a laptop.

| Metric | Value |
|---|---|
| Total requests | 2,358 |
| New transfers posted | 1,685 |
| Declined (insufficient funds, expected) | 382 |
| Requests per second | 76 |
| Median latency | 2300 ms |
| 95th percentile latency | 4900 ms |
| Unexpected errors | 0 |

| Correctness check | Result |
|---|---|
| Books balance (all entries sum to 0) | PASS |
| Cached balances match entries | PASS |
| No account overdrawn | PASS |
| Transaction count matches successful transfers (no double posting, none lost) | PASS |
| Money conserved | PASS |
| Low-balance accounts never negative, never pay out more than they hold | PASS |
| Every retry replayed, every overdraw declined | PASS |

**Overall: PASS**

*This test measures correctness under concurrency, not raw speed. Latency is high because all 200 users share a single API worker.*