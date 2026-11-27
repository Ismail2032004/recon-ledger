from fastapi import Depends, FastAPI, Header, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import service
from app.db import get_db
from app.models import Account, Entry
from app.schemas import AccountCreate, AccountOut, EntryOut, TransferIn, TransferOut

app = FastAPI(title="Recon Ledger")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/accounts", response_model=AccountOut, status_code=201)
def create_account(body: AccountCreate, db: Session = Depends(get_db)):
    try:
        return service.create_account(db, body.name, body.allow_negative)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="An account with that name already exists")


@app.get("/accounts/{account_id}", response_model=AccountOut)
def get_account(account_id: int, db: Session = Depends(get_db)):
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="Account not found")
    return account


@app.get("/accounts/{account_id}/entries", response_model=list[EntryOut])
def list_entries(account_id: int, db: Session = Depends(get_db)):
    if db.get(Account, account_id) is None:
        raise HTTPException(status_code=404, detail="Account not found")
    rows = db.execute(
        select(Entry).where(Entry.account_id == account_id).order_by(Entry.id)
    ).scalars().all()
    return rows


@app.post("/transfers", response_model=TransferOut)
def create_transfer(
    body: TransferIn,
    response: Response,
    idempotency_key: str = Header(min_length=1, max_length=200),
    db: Session = Depends(get_db),
):
    try:
        result = service.transfer(
            db,
            idempotency_key,
            body.from_account_id,
            body.to_account_id,
            body.amount,
            body.reference,
        )
    except service.AccountNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except service.InsufficientFunds as e:
        raise HTTPException(status_code=422, detail=str(e))
    except service.InvalidTransfer as e:
        raise HTTPException(status_code=422, detail=str(e))
    except service.IdempotencyConflict as e:
        raise HTTPException(status_code=409, detail=str(e))

    # 201 = newly created, 200 = this was a retry and we're replaying the first answer
    response.status_code = 200 if result.replayed else 201
    response.headers["Idempotent-Replay"] = "true" if result.replayed else "false"
    tx = result.transaction
    return TransferOut(
        transaction_id=tx.id,
        idempotency_key=tx.idempotency_key,
        from_account_id=result.from_account_id,
        to_account_id=result.to_account_id,
        amount=result.amount,
        reference=tx.reference,
        created_at=tx.created_at,
    )


@app.get("/audit/integrity")
def audit_integrity(db: Session = Depends(get_db)):
    """Proof the books balance. Used in the load test."""
    return service.integrity_report(db)
