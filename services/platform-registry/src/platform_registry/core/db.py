"""
- Title:    Database engine/session
- Author:   ai-circus-framework contributors
"""

from __future__ import annotations

from collections.abc import Iterator

from ai_circus_shared import db as shared_db
from sqlalchemy import URL, Engine, MetaData, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from platform_registry.data_model import EnvConfig

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def database_url(config: EnvConfig) -> URL:
    """The `platform` database URL — shared builder, so a password with `@`/`/`/`:` is escaped."""
    return shared_db.database_url(config)


def init_engine(config: EnvConfig) -> Engine:
    """Create the process-wide SQLAlchemy engine/session factory, waiting for Postgres to
    accept connections first (see ai_circus_shared.startup). Call once, at startup.
    """
    global _engine, _session_factory
    _engine = shared_db.connect_engine(config)
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def get_session() -> Iterator[Session]:
    """FastAPI dependency: yield a scoped SQLAlchemy session from the initialized engine."""
    if _session_factory is None:
        raise RuntimeError("Database engine not initialized — call init_engine() at startup first.")
    with _session_factory() as session:
        yield session


def ensure_added_columns(engine: Engine, metadata: MetaData) -> list[str]:
    """Add any column the ORM declares but an existing table lacks (nullable only).

    `Base.metadata.create_all()` creates missing *tables* but never alters an existing
    one, so a column added to a model after first deploy (e.g. `scenarios.deep_learning`)
    would otherwise fail every query against a pre-existing database with
    "column does not exist". Deliberately tiny — additive, nullable columns only, no
    type changes or drops; anything more needs a real migration tool.

    Returns:
        "<table>.<column>" for every column that was added.
    """
    inspector = inspect(engine)
    added: list[str] = []
    with engine.begin() as connection:
        for table in metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            existing = {column["name"] for column in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in existing:
                    continue
                if not column.nullable:
                    raise RuntimeError(f"Cannot auto-add non-nullable column {table.name}.{column.name}.")
                column_type = column.type.compile(dialect=engine.dialect)
                connection.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {column_type}'))
                added.append(f"{table.name}.{column.name}")
    return added
