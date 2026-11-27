from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    # Cached balance in pesewas (GH₵1 = 100 pesewas). The entries table is the
    # source of truth; this column is just a fast copy we keep in sync.
    balance: Mapped[int] = mapped_column(BigInteger, default=0)
    # Normal customer accounts can't go below zero. System accounts
    # (like "bank cash", used to fund accounts) are allowed to.
    allow_negative: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LedgerTransaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # The idempotency key is UNIQUE in the database itself, so even two requests
    # arriving at the exact same moment cannot both create a transaction.
    idempotency_key: Mapped[str] = mapped_column(String(200), unique=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    reference: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Entry(Base):
    """One line in the books. Never edited or deleted. Corrections are new entries."""

    __tablename__ = "entries"
    __table_args__ = (CheckConstraint("amount <> 0", name="entry_amount_not_zero"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    transaction_id: Mapped[int] = mapped_column(ForeignKey("transactions.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    # Signed, in pesewas: negative = money out (debit), positive = money in (credit).
    # All entries in one transaction add up to exactly 0.
    amount: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
