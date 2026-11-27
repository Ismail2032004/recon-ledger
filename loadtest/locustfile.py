"""Load test: many users hit the ledger at the same time, then we PROVE the books are still right.

What the users do (all at once):
  - normal transfers between customers
  - payments into one shared "merchant" account (everyone fighting over the same row)
  - retries: the same request sent twice with the same Idempotency-Key
  - overdraw attempts that must be declined
  - a few "poor" accounts hammered by many people at once (must never go below zero)

When it finishes, test_stop checks the books and writes results/latest.md.
"""
import os
import random
import time
import uuid
from pathlib import Path

import requests
from locust import HttpUser, between, events, task

RUN_ID = uuid.uuid4().hex[:8]
CUSTOMERS = int(os.getenv("LOAD_CUSTOMERS", "50"))
POOR_ACCOUNTS = 5
CUSTOMER_START = 1_000_000  # GH₵10,000.00 in pesewas
POOR_START = 5_000          # GH₵50.00
POOR_DRAIN = 1_000          # each drain takes GH₵10.00, so at most 5 can ever succeed

S = {
    "host": None,
    "bank": None,
    "merchant": None,
    "customers": [],
    "poor": [],
    "funded_total": 0,
    "funded_txns": 0,
    "baseline_txns": 0,
    "created_keys": set(),       # keys that got 201 (a real new transfer)
    "poor_successes": {},        # poor account id -> successful drains
    "bad_replays": 0,            # retries that did not behave correctly
    "declines": 0,
}


def api(method, path, **kw):
    r = requests.request(method, S["host"] + path, timeout=180, **kw)
    return r


def make_account(name, allow_negative=False):
    r = api("POST", "/accounts", json={"name": f"{name}-{RUN_ID}", "allow_negative": allow_negative})
    r.raise_for_status()
    return r.json()["id"]


def fund(account_id, amount):
    r = api(
        "POST",
        "/transfers",
        json={"from_account_id": S["bank"], "to_account_id": account_id, "amount": amount},
        headers={"Idempotency-Key": f"fund-{RUN_ID}-{account_id}"},
    )
    r.raise_for_status()
    S["funded_total"] += amount
    S["funded_txns"] += 1


@events.test_start.add_listener
def setup(environment, **kwargs):
    S["host"] = environment.host.rstrip("/")
    S["baseline_txns"] = api("GET", "/audit/integrity").json()["transaction_count"]
    S["bank"] = make_account("bank-cash", allow_negative=True)
    S["merchant"] = make_account("merchant")
    S["customers"] = [make_account(f"cust{i}") for i in range(CUSTOMERS)]
    S["poor"] = [make_account(f"poor{i}") for i in range(POOR_ACCOUNTS)]
    for acc in S["customers"]:
        fund(acc, CUSTOMER_START)
    for acc in S["poor"]:
        fund(acc, POOR_START)
        S["poor_successes"][acc] = 0
    print(f"Setup done: {len(S['customers'])} customers, {len(S['poor'])} poor accounts, 1 merchant")


class Customer(HttpUser):
    wait_time = between(0, 0.05)

    def post_transfer(self, key, frm, to, amount, label, allowed=(201, 200, 422)):
        """Send a transfer. Expected answers are not failures. Anything else is."""
        with self.client.post(
            "/transfers",
            json={"from_account_id": frm, "to_account_id": to, "amount": amount},
            headers={"Idempotency-Key": key},
            name=label,
            catch_response=True,
        ) as resp:
            if resp.status_code in allowed:
                resp.success()
            else:
                resp.failure(f"unexpected status {resp.status_code}: {resp.text[:100]}")
            if resp.status_code == 201:
                S["created_keys"].add(key)
            if resp.status_code == 422:
                S["declines"] += 1
            return resp

    @task(10)
    def normal_transfer(self):
        a, b = random.sample(S["customers"], 2)
        self.post_transfer(uuid.uuid4().hex, a, b, random.randint(1, 5_000), "transfer")

    @task(4)
    def pay_merchant(self):
        a = random.choice(S["customers"])
        self.post_transfer(uuid.uuid4().hex, a, S["merchant"], random.randint(1, 5_000), "pay merchant (hot account)")

    @task(3)
    def retry_same_key(self):
        a, b = random.sample(S["customers"], 2)
        key, amount = uuid.uuid4().hex, random.randint(1, 5_000)
        first = self.post_transfer(key, a, b, amount, "retry: first send")
        second = self.post_transfer(key, a, b, amount, "retry: second send")
        if first.status_code == 201:
            ok = (
                second.status_code == 200
                and second.headers.get("Idempotent-Replay") == "true"
                and second.json()["transaction_id"] == first.json()["transaction_id"]
            )
            if not ok:
                S["bad_replays"] += 1

    @task(1)
    def overdraw_attempt(self):
        a, b = random.sample(S["customers"], 2)
        r = self.post_transfer(uuid.uuid4().hex, a, b, 10**12, "overdraw (must decline)")
        if r.status_code != 422:
            S["bad_replays"] += 1  # an overdraw that was NOT declined is a serious failure

    @task(3)
    def drain_poor_account(self):
        acc = random.choice(S["poor"])
        r = self.post_transfer(uuid.uuid4().hex, acc, S["merchant"], POOR_DRAIN, "drain poor account")
        if r.status_code == 201:
            S["poor_successes"][acc] += 1


@events.test_stop.add_listener
def verify(environment, **kwargs):
    checks = []

    def check(name, passed, detail=""):
        checks.append((name, bool(passed), detail))

    report = api("GET", "/audit/integrity").json()
    check("Books balance (all entries sum to 0, no unbalanced transactions)",
          report["total_of_all_entries"] == 0 and not report["unbalanced_transactions"],
          f"sum of all entries = {report['total_of_all_entries']}")
    check("Cached balances match entries", not report["accounts_with_balance_mismatch"])
    check("No account overdrawn", not report["overdrawn_accounts"])

    expected_txns = S["baseline_txns"] + S["funded_txns"] + len(S["created_keys"])
    check("Transaction count matches successful transfers (no double posting, none lost)",
          report["transaction_count"] == expected_txns,
          f"database has {report['transaction_count']}, expected {expected_txns}")

    ids = S["customers"] + S["poor"] + [S["merchant"]]
    balances = {i: api("GET", f"/accounts/{i}").json()["balance"] for i in ids}
    bank_balance = api("GET", f"/accounts/{S['bank']}").json()["balance"]
    check("Money conserved (customers + merchant hold exactly what was funded)",
          sum(balances.values()) == S["funded_total"] and bank_balance == -S["funded_total"],
          f"funded {S['funded_total']}, held {sum(balances.values())}")
    check("No customer balance below zero", all(v >= 0 for v in balances.values()))

    poor_ok = all(
        balances[a] == POOR_START - POOR_DRAIN * S["poor_successes"][a] and S["poor_successes"][a] <= 5
        for a in S["poor"]
    )
    check("Poor accounts hammered concurrently: never more than 5 drains each, never negative",
          poor_ok, f"successes per account: {list(S['poor_successes'].values())}")
    check("Every retry replayed the original result; every overdraw was declined",
          S["bad_replays"] == 0, f"{S['bad_replays']} bad")

    total = environment.stats.total
    check("No unexpected HTTP errors", total.num_failures == 0, f"{total.num_failures} failures")

    passed = all(c[1] for c in checks)
    if not passed:
        environment.process_exit_code = 1

    users = environment.parsed_options.num_users if environment.parsed_options else "?"
    lines = [
        "# Load test results",
        "",
        f"Run: {time.strftime('%Y-%m-%d %H:%M:%S')} | concurrent users: {users} | accounts: {len(ids) + 1}",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Total requests | {total.num_requests:,} |",
        f"| New transfers posted | {len(S['created_keys']):,} |",
        f"| Declined (insufficient funds, expected) | {S['declines']:,} |",
        f"| Requests per second | {total.total_rps:.0f} |",
        f"| Median latency | {total.get_response_time_percentile(0.5):.0f} ms |",
        f"| 95th percentile latency | {total.get_response_time_percentile(0.95):.0f} ms |",
        f"| 99th percentile latency | {total.get_response_time_percentile(0.99):.0f} ms |",
        f"| Unexpected errors | {total.num_failures} |",
        "",
        "## Correctness checks",
        "",
        "| Check | Result |",
        "|---|---|",
    ]
    for name, ok, detail in checks:
        lines.append(f"| {name} | {'PASS' if ok else 'FAIL'}{' (' + detail + ')' if detail and not ok else ''} |")
    lines += ["", f"**Overall: {'PASS' if passed else 'FAIL'}**", ""]
    out = "\n".join(lines)

    print("\n" + out)
    results_dir = Path(os.getenv("RESULTS_DIR", "results"))
    results_dir.mkdir(parents=True, exist_ok=True)
    (results_dir / "latest.md").write_text(out, encoding="utf-8")
