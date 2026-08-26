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

    # Attribution for a ported public dataset (see ai_circus_shared.scenario_schema.
    # DatasetCredits) — None for scenarios whose content is original.
    credits: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)

    feature_columns: Mapped[list[str] | None] = mapped_column(JSON, default=None)
    feature_schema: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)
    # tabular_ml only — seeds both UIs' Data/dataset dashboard (see
    # ai_circus_shared.scenario_schema.ChartSpec).
    default_charts: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, default=None)
    sample_questions: Mapped[list[str]] = mapped_column(JSON, default=list)
    # tabular_ml only — lets both UIs render a regression scenario's plain "value
    # units" prediction instead of the classification percentage/probability view.
    task_type: Mapped[str | None] = mapped_column(String(32), default=None)
    target_units: Mapped[str | None] = mapped_column(String(32), default=None)
    # tabular_ml only — human-friendly name/explanation for the target (see
    # ai_circus_shared.scenario_schema.TabularModel.target_label/target_description).
    target_label: Mapped[str | None] = mapped_column(String(200), default=None)
    target_description: Mapped[str | None] = mapped_column(String(500), default=None)
    # Classification only — maps each raw class value to a friendly label (see
    # ai_circus_shared.scenario_schema.TabularModel.target_value_labels).
    target_value_labels: Mapped[dict[str, str] | None] = mapped_column(JSON, default=None)
    # tabular_ml only — the dataset column being predicted (not a feature itself), so
    # UIs can offer it in dataset-exploration views without treating it as a model input.
    target: Mapped[str | None] = mapped_column(String(64), default=None)
    # assisted_form only — drives both UIs' generic form renderer (see
    # ai_circus_shared.scenario_schema.FormConfig).
    form: Mapped[dict[str, Any] | None] = mapped_column(JSON, default=None)

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


class VoiceSetting(Base):
    """Singleton row (id=1): which agui-voice STT/TTS provider ("whisper"/"deepgram"/
    "groq", "piper"/"elevenlabs"/"cartesia") the live voice pipeline and the /tts
    speaker-icon endpoint should use right now — the admin Settings page's voice-mode
    picker, mirroring `LlmSetting` above. Read by agui-voice on every new WS
    connection/`/tts` call (no restart needed to switch), so this is deliberately just
    one mutable row, not a history. Only stores the *choice*; whether that provider is
    actually usable (self-hosted always is, a cloud one needs its API key configured
    in agui-voice's own `.env`) is agui-voice's own call, not tracked here.
    """

    __tablename__ = "voice_settings"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    stt_provider: Mapped[str] = mapped_column(String(32))
    tts_provider: Mapped[str] = mapped_column(String(32))
