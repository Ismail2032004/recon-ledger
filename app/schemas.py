from datetime import datetime

from pydantic import BaseModel, Field


class AccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    allow_negative: bool = False


class AccountOut(BaseModel):
    id: int
    name: str
    balance: int  # pesewas
    allow_negative: bool


class TransferIn(BaseModel):
    from_account_id: int
    to_account_id: int
    amount: int = Field(gt=0, description="Amount in pesewas (GH₵1 = 100)")
    reference: str | None = Field(default=None, max_length=200)


class TransferOut(BaseModel):
    transaction_id: int
    idempotency_key: str
    from_account_id: int
    to_account_id: int
    amount: int
    reference: str | None
    created_at: datetime


class EntryOut(BaseModel):
    id: int
    transaction_id: int
    amount: int
    created_at: datetime
