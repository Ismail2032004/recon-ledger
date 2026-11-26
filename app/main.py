from fastapi import FastAPI

app = FastAPI(title="Recon Ledger")


@app.get("/health")
def health():
    return {"status": "ok"}
