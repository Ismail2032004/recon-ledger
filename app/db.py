import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def get_database_url() -> str:
    url = os.environ["DATABASE_URL"]
    # Our .env uses the plain postgresql:// form; SQLAlchemy needs the driver name too.
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def make_engine(url: str | None = None):
    # pool_size is a bit above the default so the concurrent tests have room
    return create_engine(url or get_database_url(), pool_size=20, max_overflow=20)


_engine = None
_SessionLocal = None


def get_session_factory():
    """Create the engine lazily so importing the app never needs a database."""
    global _engine, _SessionLocal
    if _SessionLocal is None:
        _engine = make_engine()
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False)
    return _SessionLocal


def get_db():
    """FastAPI dependency: one database session per request."""
    db = get_session_factory()()
    try:
        yield db
    finally:
        db.close()
