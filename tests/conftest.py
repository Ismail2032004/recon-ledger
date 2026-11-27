"""Test setup: each test run gets its own throwaway database, built with the real
migrations. Your dev data is never touched."""
import os
import uuid
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parent.parent


def _plain_url(url: str) -> str:
    return url.replace("postgresql://", "postgresql+psycopg://", 1)


@pytest.fixture(scope="session")
def database_url():
    base = os.environ.get("DATABASE_URL")
    if not base:
        pytest.skip("DATABASE_URL not set (run tests via docker compose, see README)")
    base_url = make_url(_plain_url(base))
    test_name = f"recon_test_{uuid.uuid4().hex[:8]}"

    admin = create_engine(base_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{test_name}"'))

    test_url = base_url.set(database=test_name).render_as_string(hide_password=False)
    os.environ["DATABASE_URL"] = test_url

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(cfg, "head")

    yield test_url

    from app import db as app_db

    if app_db._engine is not None:
        app_db._engine.dispose()
    with admin.connect() as conn:
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :n"
            ),
            {"n": test_name},
        )
        conn.execute(text(f'DROP DATABASE IF EXISTS "{test_name}"'))
    os.environ["DATABASE_URL"] = base


@pytest.fixture()
def session_factory(database_url):
    from app.db import get_session_factory

    return get_session_factory()


@pytest.fixture()
def db(session_factory):
    session = session_factory()
    yield session
    session.close()


@pytest.fixture()
def client(database_url):
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


@pytest.fixture()
def make_account(db):
    from app import service

    def _make(balance=0, allow_negative=False, name=None):
        name = name or f"acct-{uuid.uuid4().hex[:10]}"
        account = service.create_account(db, name, allow_negative)
        if balance:
            funder = service.create_account(db, f"fund-{uuid.uuid4().hex[:10]}", allow_negative=True)
            service.transfer(db, f"fund-{uuid.uuid4().hex}", funder.id, account.id, balance)
            db.refresh(account)
        return account

    return _make
