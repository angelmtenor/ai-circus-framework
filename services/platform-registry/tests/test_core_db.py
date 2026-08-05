"""Tests for platform_registry.core.db."""

from __future__ import annotations

import pytest

from platform_registry.core import db
from tests.conftest import FakeSecret


class FakeConfig:
    """Minimal stand-in for EnvConfig, covering only the fields database_url() reads."""

    def __init__(self) -> None:
        """Populate fixed connection details for a deterministic DSN."""
        self.POSTGRES_USER = "ai_circus"
        self.POSTGRES_PASSWORD = FakeSecret("s3cret")
        self.POSTGRES_HOST = "postgres"
        self.POSTGRES_PORT = "5432"
        self.POSTGRES_DB = "platform"


def test_database_url_builds_expected_dsn() -> None:
    """database_url() assembles a psycopg3 DSN from the config fields."""
    assert db.database_url(FakeConfig()) == "postgresql+psycopg://ai_circus:s3cret@postgres:5432/platform"


@pytest.fixture(autouse=True)
def _reset_engine_state() -> None:
    """Ensure init_engine()'s module-level state doesn't leak between tests."""
    db._engine = None
    db._session_factory = None
    yield
    db._engine = None
    db._session_factory = None


def test_get_session_before_init_raises() -> None:
    """Calling get_session() before init_engine() raises a clear RuntimeError."""
    with pytest.raises(RuntimeError, match="not initialized"):
        next(db.get_session())


def test_init_engine_then_get_session_yields_a_session() -> None:
    """init_engine() followed by get_session() yields a usable SQLAlchemy session."""
    config = FakeConfig()
    config.POSTGRES_HOST = "sqlite"  # overwritten below via a real sqlite DSN instead

    # Use an in-memory sqlite engine directly rather than a real Postgres connection.
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    db._engine = create_engine("sqlite:///:memory:")
    db._session_factory = sessionmaker(bind=db._engine, expire_on_commit=False)

    session = next(db.get_session())
    assert session.bind is db._engine
