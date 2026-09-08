"""
- Title:    Data platform capability roadmap (static)
- Author:   ai-circus-framework contributors

A machine-readable version of the "unified AI platform architecture" status
matrix: which data-layer/gateway capabilities are LIVE in AI Liquid Core today,
PARTIAL, or still PLANNED. Deliberately pure/static data, not a live probe of
each capability — LIVE here means "this code has shipped", not "this container
is currently healthy" (the readiness/liveness probes already answer that). Update
this list by hand as capabilities move between states; there is no way to infer
"PLANNED" from running infrastructure, since by definition nothing is running yet.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Status = Literal["live", "partial", "planned"]


class Capability(BaseModel):
    """One row of the roadmap — one capability box from the architecture diagram."""

    layer: str
    name: str
    status: Status
    note: str


ROADMAP: list[Capability] = [
    # Unified data layer — default profile, always on.
    Capability(layer="data-layer", name="Object Storage", status="live", note="SeaweedFS, live today"),
    Capability(layer="data-layer", name="Relational Store", status="live", note="Postgres, live today"),
    Capability(
        layer="data-layer",
        name="Non-Relational Store",
        status="live",
        note="Postgres JSONB via ai_circus_shared.document_store — this service is its first consumer",
    ),
    Capability(layer="data-layer", name="Vector Store", status="live", note="Qdrant, live today"),
    Capability(
        layer="data-layer",
        name="Cache / Key-Value",
        status="live",
        note="Valkey via ai_circus_shared.cache — this service is its first consumer",
    ),
    # Cross-cutting platform.
    Capability(layer="gateway", name="Ingress / API Gateway", status="live", note="Traefik, live today"),
    Capability(layer="gateway", name="Identity & Tenancy", status="live", note="Keycloak Organizations, live today"),
    Capability(
        layer="gateway",
        name="AI Gateway routing",
        status="live",
        note="LiteLLM proxy, live today",
    ),
    Capability(
        layer="gateway",
        name="AI Gateway rate limits",
        status="live",
        note="Static per-model rpm/tpm in litellm_config.yaml",
    ),
    Capability(
        layer="gateway",
        name="AI Gateway per-tenant budgets",
        status="planned",
        note="Needs litellm's DB-backed proxy mode (Postgres + Prisma) — a new stateful "
        "dependency for llm-gateway's image, deliberately not added yet",
    ),
    # Data platform — optional profile, off by default.
    Capability(
        layer="data-platform",
        name="Pipeline job status/trigger",
        status="live",
        note="This service, via the Kubernetes Jobs API (k3s only — see /pipeline/jobs)",
    ),
    Capability(
        layer="data-platform",
        name="Real-Time Event Streaming",
        status="live",
        note="Kafka (KRaft) + ai_circus_shared.events — this service publishes/reads real events",
    ),
    Capability(
        layer="data-platform",
        name="Change-Data-Capture",
        status="live",
        note="Real Postgres logical replication -> Kafka (test_decoding, on-demand poll "
        "via /cdc/poll) — a background loop, not just an on-demand trigger, is a "
        "tracked follow-up",
    ),
    Capability(
        layer="data-platform",
        name="Lakehouse Table Format",
        status="live",
        note="Real Apache Iceberg tables (PyIceberg) over SeaweedFS, cataloged in Postgres — POST /lakehouse/ingest",
    ),
    Capability(
        layer="data-platform",
        name="Semantic Modeling & Query Federation",
        status="planned",
        note="Phase 3 of the optional profile",
    ),
]


def get_roadmap() -> list[Capability]:
    """Return the full capability roadmap."""
    return ROADMAP
