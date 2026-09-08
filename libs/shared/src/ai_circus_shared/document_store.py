"""
- Title:    Tenant-scoped non-relational document store (Postgres JSONB)
- Author:   ai-circus-framework contributors

The unified data layer's flexible-schema store: services that need to persist
semi-structured, evolving-shape records — without standing up a dedicated
document database — get a `documents` table keyed by (org_id, collection,
doc_id) with a single JSON content column. Same "each service owns its own
Postgres database, only the code is shared here, never the database or the
connection" convention as ai_circus_shared.conversations — swap this module for
a dedicated document engine later only if a real collection outgrows what one
JSONB column comfortably holds; nothing above this module's API needs to change
if it does.

Every read/write below takes the caller's already-resolved org_id and filters on
it — never a client-supplied id — so one tenant can never read, list, or
overwrite another's documents.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import JSON, DateTime, Engine, String, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

# Re-exported so services never need to import sqlalchemy directly just to type
# a `Depends(get_session)` parameter — mirrors ai_circus_shared.conversations.
DbSession = Session

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


class PostgresConfig(Protocol):
    """Shape a service's own EnvConfig must satisfy — the same POSTGRES_* field
    names as ai_circus_shared.conversations.PostgresConfig, so this module can
    build a connection string for any service without depending on its concrete
    EnvConfig class.
    """

    POSTGRES_HOST: str
    POSTGRES_PORT: str
    POSTGRES_DB: str
    POSTGRES_USER: str
    POSTGRES_PASSWORD: Any  # pydantic.SecretStr


class Base(DeclarativeBase):
    """Declarative base for the documents table."""


class Document(Base):
    """One flexible-schema record, addressed by (org_id, collection, doc_id)."""

    __tablename__ = "documents"

    org_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    collection: Mapped[str] = mapped_column(String(128), primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    content: Mapped[Any] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )


def database_url(config: PostgresConfig) -> str:
    """Build this service's own Postgres connection string — same shape as
    ai_circus_shared.conversations.database_url, one call per service's own database.
    """
    password = config.POSTGRES_PASSWORD.get_secret_value()
    return (
        f"postgresql+psycopg://{config.POSTGRES_USER}:{password}"
        f"@{config.POSTGRES_HOST}:{config.POSTGRES_PORT}/{config.POSTGRES_DB}"
    )


def init_engine(config: PostgresConfig) -> Engine:
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


class DocumentStore:
    """CRUD for arbitrary JSON documents, always scoped by org_id."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def put(self, org_id: str, collection: str, doc_id: str, content: dict[str, Any]) -> Document:
        """Insert or fully replace one document's content (upsert)."""
        existing = self._row(org_id, collection, doc_id)
        if existing is not None:
            existing.content = content
            existing.updated_at = datetime.now(UTC)
            self._session.commit()
            self._session.refresh(existing)
            return existing
        document = Document(org_id=org_id, collection=collection, doc_id=doc_id, content=content)
        self._session.add(document)
        self._session.commit()
        self._session.refresh(document)
        return document

    def get(self, org_id: str, collection: str, doc_id: str) -> Document | None:
        """None both when the id doesn't exist and when it belongs to another org —
        the caller can't distinguish "not found" from "not yours", by design.
        """
        return self._row(org_id, collection, doc_id)

    def list(self, org_id: str, collection: str) -> list[Document]:
        """Most-recently-updated first, scoped to one org's one collection."""
        stmt = (
            select(Document)
            .where(Document.org_id == org_id, Document.collection == collection)
            .order_by(Document.updated_at.desc())
        )
        return list(self._session.scalars(stmt))

    def delete(self, org_id: str, collection: str, doc_id: str) -> bool:
        """Returns False for an unknown id or one belonging to another org."""
        document = self._row(org_id, collection, doc_id)
        if document is None:
            return False
        self._session.delete(document)
        self._session.commit()
        return True

    def _row(self, org_id: str, collection: str, doc_id: str) -> Document | None:
        stmt = select(Document).where(
            Document.org_id == org_id,
            Document.collection == collection,
            Document.doc_id == doc_id,
        )
        return self._session.scalars(stmt).first()
