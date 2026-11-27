import uuid


def new_key():
    return {"Idempotency-Key": f"k-{uuid.uuid4().hex}"}


def make(client, name=None, allow_negative=False):
    r = client.post(
        "/accounts",
        json={"name": name or f"a-{uuid.uuid4().hex[:8]}", "allow_negative": allow_negative},
    )
    assert r.status_code == 201
    return r.json()


def test_full_flow_over_http(client):
    bank = make(client, allow_negative=True)
    alice = make(client)
    bob = make(client)

    headers = new_key()
    body = {"from_account_id": bank["id"], "to_account_id": alice["id"], "amount": 50_000}
    r = client.post("/transfers", json=body, headers=headers)
    assert r.status_code == 201
    assert r.headers["Idempotent-Replay"] == "false"

    # retry with same key: 200, same transaction, no extra money
    r2 = client.post("/transfers", json=body, headers=headers)
    assert r2.status_code == 200
    assert r2.headers["Idempotent-Replay"] == "true"
    assert r2.json()["transaction_id"] == r.json()["transaction_id"]
    assert client.get(f"/accounts/{alice['id']}").json()["balance"] == 50_000

    client.post(
        "/transfers",
        json={"from_account_id": alice["id"], "to_account_id": bob["id"], "amount": 12_000, "reference": "rent"},
        headers=new_key(),
    )
    assert client.get(f"/accounts/{bob['id']}").json()["balance"] == 12_000
    entries = client.get(f"/accounts/{alice['id']}/entries").json()
    assert [e["amount"] for e in entries] == [50_000, -12_000]
    assert client.get("/audit/integrity").json()["ok"] is True


def test_missing_idempotency_key_is_rejected(client):
    a, b = make(client, allow_negative=True), make(client)
    r = client.post("/transfers", json={"from_account_id": a["id"], "to_account_id": b["id"], "amount": 1})
    assert r.status_code == 422


def test_error_codes(client):
    a, b = make(client), make(client)
    base = {"from_account_id": a["id"], "to_account_id": b["id"], "amount": 100}
    assert client.post("/transfers", json=base, headers=new_key()).status_code == 422  # no funds
    bad = {**base, "to_account_id": 999_999}
    assert client.post("/transfers", json=bad, headers=new_key()).status_code == 404
    same = {**base, "to_account_id": a["id"]}
    assert client.post("/transfers", json=same, headers=new_key()).status_code == 422
    assert client.get("/accounts/999999").status_code == 404


def test_duplicate_account_name(client):
    make(client, name="dup-name-test")
    r = client.post("/accounts", json={"name": "dup-name-test"})
    assert r.status_code == 409
