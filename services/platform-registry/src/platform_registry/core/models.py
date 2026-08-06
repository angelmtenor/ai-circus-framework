"""
- Title:    ORM models for the `platform` Postgres schema
- Author:   ai-circus-framework contributors

Deliberately no separate `tenants` table: a Logto Organization *is* the tenant record
(id, name, branding all live in Logto) — duplicating that locally would just drift out
of sync. `Entitlement.org_id` references a Logto Organization id directly.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for every ORM model in this service."""


class Scenario(Base):
    """A scenario's metadata, seeded from `scenarios/<slug>/scenario.yaml`.

    One consolidated `prediction`/`assistant`/`rag-agent` instance serves every
    scenario of its kind (routed by `{scenario_slug}` in the request path), so there's
    no per-scenario service name to store here — both UIs call one fixed configured
    URL per kind. `feature_columns`/`feature_schema` drive both UIs' generic
    tabular_ml form renderer; `sample_questions` renders as chat suggestion chips for
    either kind.
    """

    __tablename__ = "scenarios"

    slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(String(2000))
    icon: Mapped[str] = mapped_column(String(8))
    role_required: Mapped[str] = mapped_column(String(64))

    feature_columns: Mapped[list[str] | None] = mapped_column(JSON, default=None)
    feature_schema: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    sample_questions: Mapped[list[str]] = mapped_column(JSON, default=list)
    # tabular_ml only — lets both UIs render a regression scenario's plain "value
    # units" prediction instead of the classification percentage/probability view.
    task_type: Mapped[str | None] = mapped_column(String(32), default=None)
    target_units: Mapped[str | None] = mapped_column(String(32), default=None)

    entitlements: Mapped[list[Entitlement]] = relationship(back_populates="scenario")


class Entitlement(Base):
    """Grants one tenant (Logto Organization) access to one scenario."""

    __tablename__ = "entitlements"
    __table_args__ = (UniqueConstraint("org_id", "scenario_slug", name="uq_entitlement_org_scenario"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    org_id: Mapped[str] = mapped_column(String(64), index=True)
    scenario_slug: Mapped[str] = mapped_column(ForeignKey("scenarios.slug"), index=True)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    scenario: Mapped[Scenario] = relationship(back_populates="entitlements")


class LlmSetting(Base):
    """Singleton row (id=1): which litellm_config.yaml `model_name` assistant/rag-agent
    should use for chat completions right now — the admin Settings page's live
    provider/model picker. Read by those services on every chat request (no restart
    needed to switch), so this is deliberately just one mutable row, not a history.
    """

    __tablename__ = "llm_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    model_name: Mapped[str] = mapped_column(String(64))
