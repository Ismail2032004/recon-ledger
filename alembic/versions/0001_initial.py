"""initial ledger tables

Revision ID: 0001
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("balance", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("allow_negative", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "transactions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("idempotency_key", sa.String(200), nullable=False, unique=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("reference", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "entries",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("transaction_id", sa.Integer, sa.ForeignKey("transactions.id"), nullable=False),
        sa.Column("account_id", sa.Integer, sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("amount", sa.BigInteger, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("amount <> 0", name="entry_amount_not_zero"),
    )
    op.create_index("ix_entries_transaction_id", "entries", ["transaction_id"])
    op.create_index("ix_entries_account_id", "entries", ["account_id"])

    # Entries are immutable: the database itself refuses UPDATE and DELETE on them.
    op.execute(
        """
        CREATE FUNCTION entries_immutable() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'entries are immutable: post a correcting entry instead';
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER entries_no_update_delete
        BEFORE UPDATE OR DELETE ON entries
        FOR EACH ROW EXECUTE FUNCTION entries_immutable();
        """
    )


def downgrade():
    op.execute("DROP TRIGGER IF EXISTS entries_no_update_delete ON entries")
    op.execute("DROP FUNCTION IF EXISTS entries_immutable()")
    op.drop_table("entries")
    op.drop_table("transactions")
    op.drop_table("accounts")
