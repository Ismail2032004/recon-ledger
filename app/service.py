"""Ledger business logic. All the money rules live here."""
import hashlib
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import Account, Entry, LedgerTransaction


class LedgerError(Exception):
    pass


class AccountNotFound(LedgerError):
    pass


class InsufficientFunds(LedgerError):
    pass


class InvalidTransfer(LedgerError):
    pass


class IdempotencyConflict(LedgerError):
    """Same key reused with a different request body."""


@dataclass
class TransactionInfo:
    id: int
    idempotency_key: str
    reference: str | None
    created_at: datetime


@dataclass
class TransferResult:
    transaction: "TransactionInfo | LedgerTransaction"
    from_account_id: int
    to_account_id: int
    amount: int
    replayed: bool


def _request_hash(from_id: int, to_id: int, amount: int, reference: str | None) -> str:
    raw = f"{from_id}|{to_id}|{amount}|{reference or ''}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _result_from_transaction(db: Session, tx: LedgerTransaction, replayed: bool) -> TransferResult:
    entries = db.execute(select(Entry).where(Entry.transaction_id == tx.id)).scalars().all()
    out = next(e for e in entries if e.amount < 0)
    inn = next(e for e in entries if e.amount > 0)
    # Copy into a plain object: the session is closed after the request, and a closed
    # session can't lazily reload database rows.
    info = TransactionInfo(tx.id, tx.idempotency_key, tx.reference, tx.created_at)
    return TransferResult(info, out.account_id, inn.account_id, inn.amount, replayed)


def create_account(db: Session, name: str, allow_negative: bool = False) -> Account:
    account = Account(name=name, allow_negative=allow_negative, balance=0)
    db.add(account)
    db.commit()
    return account


def transfer(
    db: Session,
    idempotency_key: str,
    from_account_id: int,
    to_account_id: int,
    amount: int,
    reference: str | None = None,
) -> TransferResult:
    if amount <= 0:
        raise InvalidTransfer("Amount must be greater than zero")
    if from_account_id == to_account_id:
        raise InvalidTransfer("Cannot transfer to the same account")

    req_hash = _request_hash(from_account_id, to_account_id, amount, reference)

    try:
        # STEP 1: claim the idempotency key.
        # If another request with the same key is mid-flight, this line WAITS for it
        # to finish, then does nothing (ON CONFLICT DO NOTHING). So only one wins.
        stmt = (
            pg_insert(LedgerTransaction)
            .values(idempotency_key=idempotency_key, request_hash=req_hash, reference=reference)
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(LedgerTransaction.id, LedgerTransaction.created_at)
        )
        inserted = db.execute(stmt).first()

        if inserted is None:
            # Key already used. Same request = replay the old answer. Different = error.
            existing = db.execute(
                select(LedgerTransaction).where(LedgerTransaction.idempotency_key == idempotency_key)
            ).scalar_one()
            if existing.request_hash != req_hash:
                raise IdempotencyConflict("Idempotency key was already used with a different request")
            result = _result_from_transaction(db, existing, replayed=True)
            db.rollback()
            return result

        # STEP 2: lock both accounts, always in id order so two opposite transfers
        # (A->B and B->A) can never deadlock each other.
        accounts = (
            db.execute(
                select(Account)
                .where(Account.id.in_([from_account_id, to_account_id]))
                .order_by(Account.id)
                .with_for_update()
            )
            .scalars()
            .all()
        )
        by_id = {a.id: a for a in accounts}
        if from_account_id not in by_id or to_account_id not in by_id:
            raise AccountNotFound("One or both accounts do not exist")

        sender = by_id[from_account_id]
        receiver = by_id[to_account_id]

        # STEP 3: check funds (safe, because we hold the lock on the sender)
        if not sender.allow_negative and sender.balance < amount:
            raise InsufficientFunds("Insufficient funds")

        # STEP 4: write the two entries (they sum to zero) and update cached balances
        new_tx_id, created_at = inserted
        db.add(Entry(transaction_id=new_tx_id, account_id=sender.id, amount=-amount))
        db.add(Entry(transaction_id=new_tx_id, account_id=receiver.id, amount=amount))
        sender.balance -= amount
        receiver.balance += amount

        db.commit()
    except Exception:
        # Any failure undoes everything, including the claimed idempotency key
        db.rollback()
        raise

    # Built from the INSERT's own RETURNING values: no extra query after commit, so no
    # connection is held after we're done.
    tx = TransactionInfo(new_tx_id, idempotency_key, reference, created_at)
    return TransferResult(tx, from_account_id, to_account_id, amount, replayed=False)


def integrity_report(db: Session) -> dict:
    """Self-check: the books must always add up."""
    total = db.execute(select(func.coalesce(func.sum(Entry.amount), 0))).scalar_one()

    # Transactions whose entries don't sum to zero (should be none)
    unbalanced = db.execute(
        select(Entry.transaction_id)
        .group_by(Entry.transaction_id)
        .having(func.sum(Entry.amount) != 0)
    ).scalars().all()

    # Accounts whose cached balance disagrees with the sum of their entries (should be none)
    sums = (
        select(Entry.account_id, func.sum(Entry.amount).label("s"))
        .group_by(Entry.account_id)
        .subquery()
    )
    mismatched = db.execute(
        select(Account.id)
        .outerjoin(sums, sums.c.account_id == Account.id)
        .where(Account.balance != func.coalesce(sums.c.s, 0))
    ).scalars().all()

    # Normal accounts must never be below zero (should be none)
    overdrawn = db.execute(
        select(Account.id).where(Account.balance < 0, Account.allow_negative.is_(False))
    ).scalars().all()

    transaction_count = db.execute(select(func.count(LedgerTransaction.id))).scalar_one()

    return {
        "ok": total == 0 and not unbalanced and not mismatched and not overdrawn,
        "total_of_all_entries": total,
        "transaction_count": transaction_count,
        "unbalanced_transactions": list(unbalanced),
        "accounts_with_balance_mismatch": list(mismatched),
        "overdrawn_accounts": list(overdrawn),
    }
