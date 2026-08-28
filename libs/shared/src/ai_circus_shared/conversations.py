"""
- Title:    Conversation history storage (Postgres)
- Author:   ai-circus-framework contributors

Shared by assistant/rag-agent/form-agent: each service owns its own Postgres
database (its own settings.yaml POSTGRES_* block, its own connection pool) and
calls init_engine()/Base.metadata.create_all() at startup exactly like
platform_registry.core.db — only the code is shared here, never the database
or the connection. This persists the chat transcript ui-react's ChatPanel
already resends in full on every turn, purely so a conversation survives a
page reload and can be listed/resumed/deleted from the UI's conversation
sidebar. It is deliberately unrelated to each service's LangGraph
`InMemorySaver()` (see e.g. rag_agent.core.agent's docstring) — that exists
only to satisfy `graph.aget_state()` within a single run, not for durable
history.

Every read/write below takes the caller's already-resolved `org_id`/`user_id`
(see each service's core/identity.py) and filters on them — never a
client-supplied id — so one tenant/user can never list, read, or delete
another's conversation.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import JSON, DateTime, Engine, ForeignKey, String, create_engine, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

# Re-exported so services never need to import sqlalchemy directly just to type
# a `Depends(get_session)` parameter (keeps sqlalchemy/psycopg as this shared
# library's own dependency, not each consuming service's).
DbSession = Session

# The placeholder title every service's POST /conversations/{scenario_slug} gives a
# conversation with no explicit title — must match that literal exactly, since
# append_messages only auto-renames a conversation still carrying this default.
DEFAULT_TITLE = "New conversation"


def _excerpt_title(text: str, max_length: int = 48) -> str:
    """A short, single-line excerpt of `text` to use as an auto-derived conversation
    title — the same "first message becomes the title" behavior ChatGPT/Claude use.
    """
    collapsed = " ".join(text.split())
    if len(collapsed) <= max_length:
        return collapsed
    return collapsed[:max_length].rstrip() + "…"


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


class PostgresConfig(Protocol):
    """Shape a service's own EnvConfig must satisfy — the same POSTGRES_* field
    names as platform_registry.core.db's config, so this module can build a
    connection string for any service without depending on its concrete
    EnvConfig class.
    """

    POSTGRES_HOST: str
    POSTGRES_PORT: str
    POSTGRES_DB: str
    POSTGRES_USER: str
    POSTGRES_PASSWORD: Any  # pydantic.SecretStr


class Base(DeclarativeBase):
    """Declarative base for the conversations/messages tables."""


class Conversation(Base):
    """One chat thread for one (org, user, scenario) — a row in the UI's conversation sidebar."""

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    org_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    scenario_slug: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC)
    )

    messages: Mapped[list[Message]] = relationship(back_populates="conversation", cascade="all, delete-orphan")


class Message(Base):
    """One AG-UI message (user/assistant/tool), persisted verbatim for history replay.

    `content` mirrors AG-UI's own message content, which can be a plain string or
    a list of content blocks (e.g. an attached image) — stored as JSON rather than
    a fixed schema so either shape round-trips unchanged.
    """

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[Any] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


def database_url(config: PostgresConfig) -> str:
    """Build this service's own Postgres connection string — same shape as
    platform_registry.core.db.database_url, one call per service's own database.
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


class ConversationStore:
    """CRUD for conversations/messages, always scoped by org_id + user_id."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def list_conversations(self, org_id: str, user_id: str, scenario_slug: str) -> list[Conversation]:
        """Most-recently-updated first — the order the sidebar renders them in."""
        stmt = (
            select(Conversation)
            .where(
                Conversation.org_id == org_id,
                Conversation.user_id == user_id,
                Conversation.scenario_slug == scenario_slug,
            )
            .order_by(Conversation.updated_at.desc())
        )
        return list(self._session.scalars(stmt))

    def create_conversation(self, org_id: str, user_id: str, scenario_slug: str, title: str) -> Conversation:
        conversation = Conversation(org_id=org_id, user_id=user_id, scenario_slug=scenario_slug, title=title)
        self._session.add(conversation)
        self._session.commit()
        self._session.refresh(conversation)
        return conversation

    def get_conversation(self, conversation_id: str, org_id: str, user_id: str) -> Conversation | None:
        """None both when the id doesn't exist and when it belongs to another org/user —
        the caller can't distinguish "not found" from "not yours", by design.
        """
        stmt = select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.org_id == org_id,
            Conversation.user_id == user_id,
        )
        return self._session.scalars(stmt).first()

    def delete_conversation(self, conversation_id: str, org_id: str, user_id: str) -> bool:
        """Returns False for an unknown id or one belonging to another org/user."""
        conversation = self.get_conversation(conversation_id, org_id, user_id)
        if conversation is None:
            return False
        self._session.delete(conversation)
        self._session.commit()
        return True

    def list_messages(self, conversation_id: str, org_id: str, user_id: str) -> list[Message]:
        """Oldest first — the order the chat transcript replays in. Empty (not an
        error) if the conversation doesn't exist or isn't this org/user's.
        """
        conversation = self.get_conversation(conversation_id, org_id, user_id)
        if conversation is None:
            return []
        stmt = select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at)
        return list(self._session.scalars(stmt))

    def append_messages(self, conversation_id: str, org_id: str, user_id: str, turns: list[tuple[str, Any]]) -> None:
        """Append (role, content) pairs to an existing, org/user-owned conversation and
        bump its updated_at (so it resurfaces at the top of the sidebar). A no-op if
        the conversation doesn't exist or isn't this org/user's — never creates one.

        The conversation's first-ever user turn also renames it from the generic
        "New conversation" placeholder to a short excerpt of that message — the
        sidebar's ChatGPT-style behavior of a conversation naming itself once there's
        something to name it after, rather than staying "New conversation" forever.
        """
        conversation = self.get_conversation(conversation_id, org_id, user_id)
        if conversation is None or not turns:
            return
        is_first_turn = (
            self._session.scalar(select(func.count()).where(Message.conversation_id == conversation_id)) == 0
        )
        for role, content in turns:
            self._session.add(Message(conversation_id=conversation_id, role=role, content=content))
        if is_first_turn and conversation.title == DEFAULT_TITLE:
            first_user_text = next(
                (content for role, content in turns if role == "user" and isinstance(content, str)), None
            )
            if first_user_text:
                conversation.title = _excerpt_title(first_user_text)
        conversation.updated_at = datetime.now(UTC)
        self._session.commit()
