# Load test results

Run: 2026-11-27 09:39:02 | concurrent users: 200 | accounts: 57

| Metric | Value |
|---|---|
| Total requests | 2,358 |
| New transfers posted | 1,685 |
| Declined (insufficient funds, expected) | 382 |
| Requests per second | 76 |
| Median latency | 2300 ms |
| 95th percentile latency | 4900 ms |
| 99th percentile latency | 6400 ms |
| Unexpected errors | 0 |

## Correctness checks

| Check | Result |
|---|---|
| Books balance (all entries sum to 0, no unbalanced transactions) | PASS |
| Cached balances match entries | PASS |
| No account overdrawn | PASS |
| Transaction count matches successful transfers (no double posting, none lost) | PASS |
| Money conserved (customers + merchant hold exactly what was funded) | PASS |
| No customer balance below zero | PASS |
| Poor accounts hammered concurrently: never more than 5 drains each, never negative | PASS |
| Every retry replayed the original result; every overdraw was declined | PASS |
| No unexpected HTTP errors | PASS |

**Overall: PASS**
