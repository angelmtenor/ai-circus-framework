"""
- Title:    ORM models for the `platform` Postgres schema
- Author:   ai-circus-framework contributors

Deliberately no separate `tenants` table: a Logto Organization *is* the tenant record
(id, name, branding all live in Logto) — duplicating that locally would just drift out
of sync. `Entitlement.org_id` references a Logto Organization id directly.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Declarative base for every ORM model in this service."""


class Scenario(Base):
    """A scenario's metadata, seeded from `scenarios/<slug>/scenario.yaml`."""

    __tablename__ = "scenarios"

    slug: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(String(2000))
    icon: Mapped[str] = mapped_column(String(8))
    role_required: Mapped[str] = mapped_column(String(64))

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
