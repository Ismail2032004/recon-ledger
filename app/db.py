import os
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def get_database_url() -> str:
    url = os.environ["DATABASE_URL"]
    # Our .env uses the plain postgresql:// form; SQLAlchemy needs the driver name too.
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def make_engine(url: str | None = None):
    return create_engine(
        url or get_database_url(),
        pool_size=int(os.getenv("DB_POOL_SIZE", "40")),
        max_overflow=0,
        pool_timeout=int(os.getenv("DB_POOL_TIMEOUT", "30")),
    )


_engine = None
_SessionLocal = None


def get_session_factory():
    """Create the engine lazily so importing the app never needs a database."""
    global _engine, _SessionLocal
    if _SessionLocal is None:
        _engine = make_engine()
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _SessionLocal


@contextmanager
def session_scope():
    """One database session per request, opened AND closed inside the request's own thread.

    (We deliberately do not use a FastAPI "yield" dependency for this: its cleanup needs a
    free worker thread, and under heavy load that can deadlock against the connection pool.
    Rule: connections in use can never outnumber worker threads. See DB_POOL_SIZE / API_THREADS.)
    """
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()
