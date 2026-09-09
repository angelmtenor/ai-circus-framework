"""
- Title:    Unified AI platform capability roadmap (static)
- Author:   ai-circus-framework contributors

A machine-readable version of the "unified AI platform architecture" status
matrix: which capabilities are LIVE in this platform today, PARTIAL, or still
PLANNED. Deliberately pure/static data, not a live probe of each capability —
LIVE here means "this code has shipped", not "this container is currently
healthy" (the readiness/liveness probes already answer that). Update this
list by hand as capabilities move between states; there is no way to infer
"PLANNED" from running infrastructure, since by definition nothing is running
yet.

Organized into the three pillars the whole platform is built from — see
`Pillar` below and the architecture diagrams (README's "Architecture"
section):

- **data**: the source/generation layer — object/relational/non-relational/
  vector storage, the cache, and everything that produces or moves data
  (batch ETL, model training, event streaming, CDC, the lakehouse).
- **ai-bi-ml**: the consumption layer built on top of that data — the
  pre-existing scenario-serving stack (tabular ML inference, the
  conversational/RAG and assisted-form agents, voice), the AI Gateway that
  routes every LLM call, and the semantic/BI query layer.
- **governance**: transversal, not a third parallel layer — identity/tenancy,
  ingress, and AI Gateway usage controls (rate limits, budgets) apply across
  *both* of the above, not alongside them.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

Status = Literal["live", "partial", "planned"]
Pillar = Literal["data", "ai-bi-ml", "governance"]


class Capability(BaseModel):
    """One row of the roadmap — one capability box from the architecture diagram."""

    pillar: Pillar
    name: str
    status: Status
    note: str


ROADMAP: list[Capability] = [
    # --- Data: source, generation, and movement — always on unless noted. ---
    Capability(pillar="data", name="Object Storage", status="live", note="SeaweedFS, live today"),
    Capability(pillar="data", name="Relational Store", status="live", note="Postgres, live today"),
    Capability(
        pillar="data",
        name="Non-Relational Store",
        status="live",
        note="Postgres JSONB via ai_circus_shared.document_store — data-platform-manager is its first consumer",
    ),
    Capability(pillar="data", name="Vector Store", status="live", note="Qdrant, live today"),
    Capability(
        pillar="data",
        name="Cache / Key-Value",
        status="live",
        note="Valkey via ai_circus_shared.cache — data-platform-manager is its first consumer",
    ),
    Capability(
        pillar="data",
        name="Tabular ETL",
        status="live",
        note="Feature-engineering pipeline over the object store — see services/etl-tabular",
    ),
    Capability(
        pillar="data",
        name="Vector ETL",
        status="live",
        note="Chunks + embeds scenario documents into Qdrant — see services/etl-vectorize",
    ),
    Capability(
        pillar="data",
        name="Model Training",
        status="live",
        note="Produces the model artifact `prediction` later serves — see services/training",
    ),
    Capability(
        pillar="data",
        name="Pipeline job status/trigger",
        status="live",
        note="data-platform-manager, via the Kubernetes Jobs API (k3s only — see /pipeline/jobs)",
    ),
    Capability(
        pillar="data",
        name="Real-Time Event Streaming",
        status="live",
        note="Kafka (KRaft) + ai_circus_shared.events — data-platform-manager publishes/reads real events",
    ),
    Capability(
        pillar="data",
        name="Change-Data-Capture",
        status="live",
        note="Real Postgres logical replication -> Kafka (test_decoding, on-demand poll "
        "via /cdc/poll) — a background loop, not just an on-demand trigger, is a "
        "tracked follow-up",
    ),
    Capability(
        pillar="data",
        name="Lakehouse Table Format",
        status="live",
        note="Real Apache Iceberg tables (PyIceberg) over SeaweedFS, cataloged in Postgres — POST /lakehouse/ingest",
    ),
    # --- AI / BI / ML: consumption, built on top of the data pillar. ---
    Capability(
        pillar="ai-bi-ml",
        name="AI Gateway routing",
        status="live",
        note="LiteLLM proxy, live today",
    ),
    Capability(
        pillar="ai-bi-ml",
        name="Semantic Modeling & Query Federation",
        status="live",
        note="Embedded DuckDB engine federating the lakehouse's Iceberg table with "
        "platform-registry's real Postgres tables in one query — "
        "GET /semantic/views, POST /semantic/views/{name}/query",
    ),
    Capability(
        pillar="ai-bi-ml",
        name="Tabular ML Serving",
        status="live",
        note="Real-time inference plus SHAP explainability — see services/prediction",
    ),
    Capability(
        pillar="ai-bi-ml",
        name="Conversational Assistant",
        status="live",
        note="Tool-calling agent with live generative UI over tabular_ml scenarios — see services/assistant",
    ),
    Capability(
        pillar="ai-bi-ml",
        name="Conversational RAG",
        status="live",
        note="Retrieval-grounded chat over conversational_rag scenarios — see services/rag-agent",
    ),
    Capability(
        pillar="ai-bi-ml",
        name="Assisted Forms",
        status="live",
        note="Form-filling assistant over assisted_form scenarios — see services/form-agent",
    ),
    Capability(
        pillar="ai-bi-ml",
        name="Voice / Multimodal",
        status="live",
        note="Speech-to-text and text-to-speech over the chat agents — see services/agui-voice",
    ),
    # --- Governance: transversal usage/access controls, not a third layer. ---
    Capability(pillar="governance", name="Ingress / API Gateway", status="live", note="Traefik, live today"),
    Capability(
        pillar="governance", name="Identity & Tenancy", status="live", note="Keycloak Organizations, live today"
    ),
    Capability(
        pillar="governance",
        name="AI Gateway rate limits",
        status="live",
        note="Static per-model rpm/tpm in litellm_config.yaml",
    ),
    Capability(
        pillar="governance",
        name="AI Gateway per-tenant budgets",
        status="planned",
        note="Needs litellm's DB-backed proxy mode (Postgres + Prisma) — a new stateful "
        "dependency for llm-gateway's image, deliberately not added yet",
    ),
]


def get_roadmap() -> list[Capability]:
    """Return the full capability roadmap."""
    return ROADMAP
