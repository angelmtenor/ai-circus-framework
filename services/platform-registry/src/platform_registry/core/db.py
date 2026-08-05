"""
- Title:    Database engine/session
- Author:   ai-circus-framework contributors
"""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from platform_registry.data_model import EnvConfig

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def database_url(config: EnvConfig) -> str:
    """Build the Postgres connection string for the `platform` schema from config."""
    password = config.POSTGRES_PASSWORD.get_secret_value()
    return (
        f"postgresql+psycopg://{config.POSTGRES_USER}:{password}"
        f"@{config.POSTGRES_HOST}:{config.POSTGRES_PORT}/{config.POSTGRES_DB}"
    )


def init_engine(config: EnvConfig) -> Engine:
    """Create the process-wide SQLAlchemy engine/session factory. Call once, at startup."""
    global _engine, _session_factory
    _engine = create_engine(database_url(config), pool_pre_ping=True)
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_session() -> Iterator[Session]:
    """FastAPI dependency: yield a scoped SQLAlchemy session from the initialized engine."""
    if _session_factory is None:
        raise RuntimeError("Database engine not initialized — call init_engine() at startup first.")
    with _session_factory() as session:
        yield session
