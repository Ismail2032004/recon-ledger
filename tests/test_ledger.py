import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app import service
from app.models import Account, Entry, LedgerTransaction


def key():
    return f"k-{uuid.uuid4().hex}"


# ---------- basic double-entry ----------

def test_transfer_moves_money_and_entries_sum_to_zero(db, make_account):
    a = make_account(balance=10_000)  # GH₵100.00
    b = make_account()
    result = service.transfer(db, key(), a.id, b.id, 2_500)

    db.refresh(a)
    db.refresh(b)
    assert a.balance == 7_500
    assert b.balance == 2_500

    entries = db.execute(
        select(Entry).where(Entry.transaction_id == result.transaction.id)
    ).scalars().all()
    assert sorted(e.amount for e in entries) == [-2_500, 2_500]
    assert sum(e.amount for e in entries) == 0


def test_cannot_overdraw(db, make_account):
    a = make_account(balance=1_000)
    b = make_account()
    with pytest.raises(service.InsufficientFunds):
        service.transfer(db, key(), a.id, b.id, 1_001)
    db.refresh(a)
    db.refresh(b)
    assert (a.balance, b.balance) == (1_000, 0)


def test_system_account_can_go_negative(db, make_account):
    funder = make_account(allow_negative=True)
    b = make_account()
    service.transfer(db, key(), funder.id, b.id, 5_000)
    db.refresh(funder)
    assert funder.balance == -5_000


@pytest.mark.parametrize("amount", [0, -5])
def test_rejects_non_positive_amount(db, make_account, amount):
    a, b = make_account(balance=100), make_account()
    with pytest.raises(service.InvalidTransfer):
        service.transfer(db, key(), a.id, b.id, amount)


def test_rejects_same_account(db, make_account):
    a = make_account(balance=100)
    with pytest.raises(service.InvalidTransfer):
        service.transfer(db, key(), a.id, a.id, 10)


def test_unknown_account_leaves_no_trace(db, make_account):
    a = make_account(balance=100)
    k = key()
    with pytest.raises(service.AccountNotFound):
        service.transfer(db, k, a.id, 999_999, 10)
    # the claimed idempotency key was rolled back too
    assert db.execute(
        select(LedgerTransaction).where(LedgerTransaction.idempotency_key == k)
    ).first() is None


# ---------- idempotency ----------

def test_retry_with_same_key_does_not_double_charge(db, make_account):
    a = make_account(balance=10_000)
    b = make_account()
    k = key()
    first = service.transfer(db, k, a.id, b.id, 3_000)
    second = service.transfer(db, k, a.id, b.id, 3_000)

    assert first.replayed is False
    assert second.replayed is True
    assert first.transaction.id == second.transaction.id
    db.refresh(a)
    db.refresh(b)
    assert (a.balance, b.balance) == (7_000, 3_000)  # charged ONCE


def test_same_key_different_request_is_rejected(db, make_account):
    a, b = make_account(balance=10_000), make_account()
    k = key()
    service.transfer(db, k, a.id, b.id, 3_000)
    with pytest.raises(service.IdempotencyConflict):
        service.transfer(db, k, a.id, b.id, 4_000)


def test_failed_transfer_can_be_retried_with_same_key(db, make_account):
    a, b = make_account(balance=100), make_account()
    k = key()
    with pytest.raises(service.InsufficientFunds):
        service.transfer(db, k, a.id, b.id, 500)
    # customer tops up, retries with the same key, it now works
    funder = make_account(allow_negative=True)
    service.transfer(db, key(), funder.id, a.id, 1_000)
    result = service.transfer(db, k, a.id, b.id, 500)
    assert result.replayed is False


# ---------- immutability ----------

def test_entries_cannot_be_updated_or_deleted(db, make_account):
    a, b = make_account(balance=1_000), make_account()
    service.transfer(db, key(), a.id, b.id, 100)
    with pytest.raises(DBAPIError):
        db.execute(text("UPDATE entries SET amount = 1 WHERE id = (SELECT min(id) FROM entries)"))
    db.rollback()
    with pytest.raises(DBAPIError):
        db.execute(text("DELETE FROM entries"))
    db.rollback()


# ---------- concurrency ----------

def test_concurrent_same_key_charges_once(session_factory, make_account):
    a, b = make_account(balance=100_000), make_account()
    k = key()

    def attempt(_):
        s = session_factory()
        try:
            return service.transfer(s, k, a.id, b.id, 1_000).replayed
        finally:
            s.close()

    with ThreadPoolExecutor(max_workers=20) as pool:
        replays = list(pool.map(attempt, range(20)))

    assert replays.count(False) == 1  # exactly one real transfer
    assert replays.count(True) == 19
    s = session_factory()
    assert s.get(Account, a.id).balance == 99_000
    assert s.get(Account, b.id).balance == 1_000
    s.close()


def test_concurrent_transfers_never_overdraw(session_factory, make_account):
    # GH₵10.00 in the account, 40 people each try to take GH₵1.00 at the same moment.
    # Exactly 10 may succeed. Never 11.
    a, b = make_account(balance=1_000), make_account()

    def attempt(_):
        s = session_factory()
        try:
            service.transfer(s, key(), a.id, b.id, 100)
            return "ok"
        except service.InsufficientFunds:
            return "declined"
        finally:
            s.close()

    with ThreadPoolExecutor(max_workers=40) as pool:
        outcomes = list(pool.map(attempt, range(40)))

    assert outcomes.count("ok") == 10
    assert outcomes.count("declined") == 30
    s = session_factory()
    assert s.get(Account, a.id).balance == 0
    assert s.get(Account, b.id).balance == 1_000
    s.close()


def test_opposite_direction_transfers_do_not_deadlock(session_factory, make_account):
    a, b = make_account(balance=100_000), make_account(balance=100_000)

    def attempt(i):
        s = session_factory()
        try:
            frm, to = (a.id, b.id) if i % 2 == 0 else (b.id, a.id)
            service.transfer(s, key(), frm, to, 10)
        finally:
            s.close()

    with ThreadPoolExecutor(max_workers=30) as pool:
        list(pool.map(attempt, range(200)))  # would raise on a deadlock

    s = session_factory()
    assert s.get(Account, a.id).balance + s.get(Account, b.id).balance == 200_000
    s.close()


def test_books_balance_after_all_that(db):
    report = service.integrity_report(db)
    assert report["ok"], report
