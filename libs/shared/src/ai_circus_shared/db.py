"""Postgres engine construction shared by every service that owns a database.

platform-registry (`platform`), assistant/rag-agent/form-agent (conversation history,
see `conversations.py`) and data-platform-manager (`document_store.py`) each own their
own database but build the connection the same way — this is that one way.
"""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy import URL, Engine, create_engine
from sqlalchemy.exc import OperationalError

from ai_circus_shared.startup import wait_for


class PostgresConfig(Protocol):
    """Shape a service's own EnvConfig must satisfy (every service names these alike)."""

    POSTGRES_HOST: str
    POSTGRES_PORT: str
    POSTGRES_DB: str
    POSTGRES_USER: str
    POSTGRES_PASSWORD: Any  # pydantic.SecretStr


def database_url(config: PostgresConfig) -> URL:
    """This service's Postgres URL. Built with `URL.create`, not an f-string, so a
    password containing `@`, `/`, `:` or `%` is escaped rather than silently producing
    a different host/database (or an unparseable DSN).
    """
    return URL.create(
        "postgresql+psycopg",
        username=config.POSTGRES_USER,
        password=config.POSTGRES_PASSWORD.get_secret_value(),
        host=config.POSTGRES_HOST,
        port=int(config.POSTGRES_PORT),
        database=config.POSTGRES_DB,
    )


def connect_engine(config: PostgresConfig) -> Engine:
    """Create the process-wide engine and wait until Postgres actually accepts a
    connection (see `startup.wait_for`), so the caller's `create_all()` right after
    can't fail on a database that is still starting.
    """
    engine = create_engine(database_url(config), pool_pre_ping=True)

    def _ping() -> None:
        with engine.connect():
            pass

    wait_for(_ping, what="Postgres", retryable=lambda exc: isinstance(exc, OperationalError))
    return engine
