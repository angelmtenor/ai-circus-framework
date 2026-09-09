"""
- Title:    Data platform admin API
- Author:   ai-circus-framework contributors

Admin-only control surface for the platform's data layer and AI Gateway
governance — every route below (except /healthz) requires the shared
ADMIN_API_KEY bearer token, never a Keycloak end-user token, per this service's
"admin tenant only" scope (see settings.yaml's header comment). It is not routed
through Traefik's public entrypoint by anything other than ui-react's admin-only
Settings page, which already gates its own "Data Platform" section on
`identity.orgId === ADMIN_ORG_ID` before ever calling here.

Pipeline job status/control (GET/POST /pipeline/jobs*) only works when this
process is itself running inside a real k3s cluster — see
core.k8s_jobs.in_cluster_config_available — since a docker-compose deployment
has no Kubernetes API to call. /roadmap and /gateway/rate-limits work in both.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

import redis
from ai_circus_shared.auth import ADMIN_ORG_ID, is_admin_bearer_token
from ai_circus_shared.cache import TenantCache
from ai_circus_shared.cdc import ensure_slot, poll_changes, slot_status
from ai_circus_shared.document_store import DbSession, DocumentStore
from ai_circus_shared.document_store import get_session as get_document_session
from ai_circus_shared.events import EventConsumer, EventProducer, connect_consumer
from confluent_kafka import Producer
from fastapi import APIRouter, Depends, Header, HTTPException
from kubernetes.client.exceptions import ApiException
from pydantic import BaseModel

from data_platform_manager import get_env_config
from data_platform_manager.core import gateway, k8s_jobs, lakehouse, semantic
from data_platform_manager.core.cache_client import get_client
from data_platform_manager.core.events_client import get_producer
from data_platform_manager.core.roadmap import Capability, get_roadmap

router = APIRouter()
logger = logging.getLogger(__name__)

_JOB_STATUS_CACHE_TTL_SECONDS = 5
_TRIGGER_HISTORY_COLLECTION = "pipeline-triggers"
_TRIGGER_EVENTS_TOPIC = "pipeline-triggers"
_EVENT_POLL_TIMEOUT_SECONDS = 1.0
# Empirically, a brand-new consumer group's first ~3 poll() calls return None
# while it joins the group — 6 gives comfortable margin over that before
# concluding the topic is genuinely empty (worst case ~6s, a manual admin
# "Refresh" click, not a hot path).
_EVENT_POLL_ATTEMPTS = 6
_EVENT_POLL_MAX_MESSAGES = 50
# The `documents` table (ai_circus_shared.document_store) is the CDC demo
# source — it's already populated by real usage (the pipeline-trigger audit
# trail above), so a change genuinely shows up here without needing a
# separate table just for this.
_CDC_SLOT_NAME = "data_platform_manager_documents"
_CDC_EVENTS_TOPIC = "cdc.documents"
# The lakehouse's demo table snapshots the SAME collection CDC watches — one
# periodic full-table snapshot (lakehouse) alongside one per-row change feed
# (CDC), over the same underlying data, showing why a platform wants both.
_LAKEHOUSE_TABLE_NAME = "pipeline_triggers"


def require_admin(authorization: str | None = Header(default=None)) -> None:
    """Gate every route in this router but /healthz on the shared admin bearer
    token — same check platform-registry's own require_admin performs.
    """
    config = get_env_config()
    if not is_admin_bearer_token(authorization, config.ADMIN_API_KEY.get_secret_value()):
        raise HTTPException(status_code=401, detail="Admin bearer token required.")


def get_cache(client: redis.Redis = Depends(get_client)) -> TenantCache:
    """FastAPI dependency: a TenantCache bound to this process's Valkey connection."""
    return TenantCache(_client=client)


def get_event_producer(producer: Producer = Depends(get_producer)) -> EventProducer:
    """FastAPI dependency: an EventProducer bound to this process's Kafka connection."""
    return EventProducer(_client=producer)


class JobStatusOut(BaseModel):
    """One pipeline job's current state."""

    name: str
    state: str
    started_at: str | None
    completed_at: str | None


class PipelineJobsOut(BaseModel):
    """GET /pipeline/jobs response — either real statuses, or why they're unavailable."""

    available: bool
    reason: str | None = None
    jobs: list[JobStatusOut] = []


class RateLimitOut(BaseModel):
    """One routed model's static rpm/tpm ceiling."""

    model_name: str | None
    rpm: int | None
    tpm: int | None


class BudgetOut(BaseModel):
    """One org's configured monthly AI Gateway spend cap plus its current spend."""

    org_id: str
    monthly_cap_usd: float
    spend_usd: float


class BudgetSetIn(BaseModel):
    """Body for PUT /gateway/budgets/{org_id}."""

    monthly_cap_usd: float


# Collection this service's own document store keeps every configured budget
# cap under (see ai_circus_shared.document_store) — owned by ADMIN_ORG_ID with
# one doc per target org, not each org's own collection, so /gateway/budgets can
# list every configured org in a single query.
_BUDGETS_COLLECTION = "ai-gateway-budgets"
# Must match llm_gateway.budget_hook's _CAP_KEY/_spend_key exactly — that hook
# can't import ai_circus_shared.cache itself (fastapi version conflict with
# litellm[proxy], see its module docstring) and hand-matches this key format.
_BUDGET_CAP_KEY = "llm_budget_cap"
_BUDGET_SPEND_KEY_PREFIX = "llm_budget_spend"


def _budget_spend_key() -> str:
    return f"{_BUDGET_SPEND_KEY_PREFIX}:{datetime.now(UTC):%Y-%m}"


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness check — no auth, matches every other service's convention."""
    return {"status": "ok"}


@router.get("/roadmap", response_model=list[Capability], dependencies=[Depends(require_admin)])
def roadmap() -> list[Capability]:
    """The data platform's capability status matrix (live/partial/planned)."""
    return get_roadmap()


@router.get("/gateway/rate-limits", response_model=list[RateLimitOut], dependencies=[Depends(require_admin)])
def gateway_rate_limits() -> list[dict[str, object]]:
    """Read-only report of llm-gateway's static per-model rpm/tpm ceilings."""
    config = get_env_config()
    try:
        return gateway.get_rate_limits(config.LLM_GATEWAY_URL, config.LLM_GATEWAY_API_KEY.get_secret_value())
    except gateway.LlmGatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/gateway/budgets", response_model=list[BudgetOut], dependencies=[Depends(require_admin)])
def list_gateway_budgets(
    cache: TenantCache = Depends(get_cache),
    session: DbSession = Depends(get_document_session),
) -> list[BudgetOut]:
    """Every org with a configured monthly AI Gateway spend cap, plus its current
    month's spend — read straight from the same Valkey counter llm-gateway's
    budget_hook increments after every real completion. An org with no cap set
    here is unlimited and never appears in this list (budgets are opt-in).
    """
    store = DocumentStore(session)
    return [
        BudgetOut(
            org_id=doc.doc_id,
            monthly_cap_usd=doc.content["monthly_cap_usd"],
            spend_usd=float(cache.get(doc.doc_id, _budget_spend_key()) or 0.0),
        )
        for doc in store.list(ADMIN_ORG_ID, _BUDGETS_COLLECTION)
    ]


@router.put("/gateway/budgets/{org_id}", response_model=BudgetOut, dependencies=[Depends(require_admin)])
def set_gateway_budget(
    org_id: str,
    body: BudgetSetIn,
    cache: TenantCache = Depends(get_cache),
    session: DbSession = Depends(get_document_session),
) -> BudgetOut:
    """Set (or update) one org's monthly AI Gateway spend cap.

    Durable in this service's own document store (survives a Valkey flush/
    restart) and mirrored into Valkey so llm-gateway's budget_hook can enforce
    it on its hot path with zero calls back to this service.
    """
    store = DocumentStore(session)
    store.put(ADMIN_ORG_ID, _BUDGETS_COLLECTION, org_id, {"monthly_cap_usd": body.monthly_cap_usd})
    cache.set(org_id, _BUDGET_CAP_KEY, str(body.monthly_cap_usd))
    return BudgetOut(
        org_id=org_id,
        monthly_cap_usd=body.monthly_cap_usd,
        spend_usd=float(cache.get(org_id, _budget_spend_key()) or 0.0),
    )


@router.delete("/gateway/budgets/{org_id}", dependencies=[Depends(require_admin)])
def delete_gateway_budget(
    org_id: str,
    cache: TenantCache = Depends(get_cache),
    session: DbSession = Depends(get_document_session),
) -> dict[str, bool]:
    """Remove an org's cap — makes it unlimited again, not "zero budget"."""
    store = DocumentStore(session)
    deleted = store.delete(ADMIN_ORG_ID, _BUDGETS_COLLECTION, org_id)
    cache.delete(org_id, _BUDGET_CAP_KEY)
    return {"deleted": deleted}


@router.get("/pipeline/jobs", response_model=PipelineJobsOut, dependencies=[Depends(require_admin)])
def pipeline_jobs(cache: TenantCache = Depends(get_cache)) -> PipelineJobsOut:
    """Status of etl-tabular/training/etl-vectorize. Only meaningful in k3s — a
    docker-compose deployment has no Jobs API to query (run `make pipeline`
    directly there instead). Each job's status is cached briefly so a UI polling
    this endpoint doesn't hammer the Kubernetes API on every render.
    """
    if not k8s_jobs.in_cluster_config_available():
        return PipelineJobsOut(
            available=False,
            reason="Job status/control requires the k3s deployment. In docker-compose, run `make pipeline` directly.",
        )
    statuses: list[JobStatusOut] = []
    for name in k8s_jobs.PIPELINE_JOBS:
        cache_key = f"job-status:{name}"
        cached = cache.get(ADMIN_ORG_ID, cache_key)
        if cached is not None:
            state, started_at, completed_at = cached.split("|")
            statuses.append(
                JobStatusOut(name=name, state=state, started_at=started_at or None, completed_at=completed_at or None)
            )
            continue
        status = k8s_jobs.get_job_status(name)
        cache.set(
            ADMIN_ORG_ID,
            cache_key,
            f"{status.state}|{status.started_at or ''}|{status.completed_at or ''}",
            ttl_seconds=_JOB_STATUS_CACHE_TTL_SECONDS,
        )
        statuses.append(
            JobStatusOut(name=name, state=status.state, started_at=status.started_at, completed_at=status.completed_at)
        )
    return PipelineJobsOut(available=True, jobs=statuses)


@router.post("/pipeline/jobs/{name}/trigger", status_code=202, dependencies=[Depends(require_admin)])
def trigger_pipeline_job(
    name: str,
    cache: TenantCache = Depends(get_cache),
    session: DbSession = Depends(get_document_session),
    events: EventProducer = Depends(get_event_producer),
) -> dict[str, str]:
    """Delete-and-recreate one pipeline Job (etl-tabular/training/etl-vectorize).
    Every trigger is recorded to this service's own document store (the durable
    audit trail — visible to an operator even after the Job itself is long gone)
    and immediately invalidates that job's cached status. It's also published to
    the optional Data Platform profile's Kafka topic, best-effort: if that
    profile isn't running, the publish silently does nothing (see
    ai_circus_shared.events' module docstring) — it must never turn a real,
    successful trigger into a failed HTTP response.
    """
    if not k8s_jobs.in_cluster_config_available():
        raise HTTPException(
            status_code=501,
            detail="Job control requires the k3s deployment. In docker-compose, run `make pipeline` directly.",
        )
    if name not in k8s_jobs.PIPELINE_JOBS:
        raise HTTPException(status_code=404, detail=f"Unknown pipeline job {name!r}.")
    try:
        k8s_jobs.trigger_job(name)
    except ApiException as exc:
        raise HTTPException(status_code=502, detail=f"Kubernetes API error: {exc.reason}") from exc

    cache.delete(ADMIN_ORG_ID, f"job-status:{name}")
    store = DocumentStore(session)
    triggered_at = datetime.now(UTC).isoformat()
    event = {"job": name, "triggered_at": triggered_at}
    store.put(ADMIN_ORG_ID, _TRIGGER_HISTORY_COLLECTION, str(uuid.uuid4()), event)

    try:
        events.publish(ADMIN_ORG_ID, _TRIGGER_EVENTS_TOPIC, event)
    except Exception:
        # Best-effort by design (see docstring above) — the trigger itself
        # already succeeded and is durably recorded in the document store.
        logger.warning("Failed to publish pipeline-trigger event for job %r", name, exc_info=True)

    return event


@router.get("/pipeline/triggers/history", dependencies=[Depends(require_admin)])
def trigger_history(session: DbSession = Depends(get_document_session)) -> list[dict[str, object]]:
    """Most-recently-triggered-first audit trail of every job this service has run —
    the durable record (Postgres), unlike GET /events/pipeline-triggers below.
    """
    store = DocumentStore(session)
    return [doc.content for doc in store.list(ADMIN_ORG_ID, _TRIGGER_HISTORY_COLLECTION)]


@router.get("/events/pipeline-triggers", dependencies=[Depends(require_admin)])
def recent_pipeline_trigger_events() -> list[dict[str, object]]:
    """Recent pipeline-trigger events read directly off the Kafka topic — proof
    the optional Data Platform profile's event stream is actually working, not
    the durable source of truth (see /pipeline/triggers/history for that).
    Returns an empty list, not an error, if that profile isn't running: this
    uses a fresh, uniquely-named consumer group per call (auto.offset.reset=
    earliest) specifically so every call re-reads from the beginning of the
    topic rather than draining it across calls — the right trade-off for a
    low-volume admin visibility endpoint, not a real streaming consumer.

    Deliberately polls a *fixed* number of times rather than stopping at the
    first empty one: confirmed live against a real broker that a brand-new
    consumer group's first few poll() calls return None while it joins the
    group / gets its partition assignment, even when messages already exist —
    stopping early would almost always (wrongly) report "no events".
    """
    config = get_env_config()
    consumer = EventConsumer(_client=connect_consumer(config, group_id=f"data-platform-manager-viewer-{uuid.uuid4()}"))
    consumer.subscribe(ADMIN_ORG_ID, _TRIGGER_EVENTS_TOPIC)
    events: list[dict[str, object]] = []
    try:
        for _ in range(_EVENT_POLL_ATTEMPTS):
            if len(events) >= _EVENT_POLL_MAX_MESSAGES:
                break
            event = consumer.poll(timeout_seconds=_EVENT_POLL_TIMEOUT_SECONDS)
            if event is not None:
                events.append(event)
    except Exception:
        logger.warning("Failed to read pipeline-trigger events from Kafka", exc_info=True)
        return []
    finally:
        consumer.close()
    return events


class CdcStatusOut(BaseModel):
    """Whether the CDC replication slot exists yet, and its current position."""

    slot_exists: bool
    active: bool | None = None
    confirmed_flush_lsn: str | None = None


@router.get("/cdc/status", response_model=CdcStatusOut, dependencies=[Depends(require_admin)])
def cdc_status(session: DbSession = Depends(get_document_session)) -> CdcStatusOut:
    """Whether the `documents` table's logical replication slot has been
    created yet (see POST /cdc/poll) and its current WAL position — proof the
    slot is real and advancing, not just "created once and forgotten".
    """
    status = slot_status(session, _CDC_SLOT_NAME)
    if status is None:
        return CdcStatusOut(slot_exists=False)
    return CdcStatusOut(slot_exists=True, active=status["active"], confirmed_flush_lsn=status["confirmed_flush_lsn"])


@router.post("/cdc/poll", dependencies=[Depends(require_admin)])
def cdc_poll(
    session: DbSession = Depends(get_document_session),
    events: EventProducer = Depends(get_event_producer),
) -> dict[str, object]:
    """Change-data-capture, on demand: create the `documents` table's logical
    replication slot if it doesn't exist yet, fetch every row-level change
    since the last poll (a real Postgres WAL read — see
    ai_circus_shared.cdc's module docstring — not the application re-publishing
    its own writes, unlike POST /pipeline/jobs/{name}/trigger's best-effort
    publish above), and forward each one to Kafka as its own event.

    On-demand rather than a background loop: this is a reference/demo
    framework, not a production CDC pipeline — an admin clicking "poll now"
    (or a cron hitting this endpoint) is a proportionate way to prove the
    change feed is real without adding a long-running task's lifecycle
    (start/stop/crash-recovery) to this service.
    """
    just_created = ensure_slot(session, _CDC_SLOT_NAME)
    changes = poll_changes(session, _CDC_SLOT_NAME)
    for change in changes:
        try:
            events.publish(ADMIN_ORG_ID, _CDC_EVENTS_TOPIC, change.to_dict())
        except Exception:
            logger.warning("Failed to publish CDC event for %s.%s", change.schema, change.table, exc_info=True)
    return {"slot_created": just_created, "changes_captured": len(changes), "changes": [c.to_dict() for c in changes]}


@router.post("/lakehouse/ingest", dependencies=[Depends(require_admin)])
def lakehouse_ingest(session: DbSession = Depends(get_document_session)) -> dict[str, object]:
    """Snapshot the pipeline-trigger audit trail's current rows into a
    versioned Iceberg table (see core/lakehouse.py) — a real Parquet write to
    the same SeaweedFS every other service already uses, cataloged in this
    service's own Postgres database. Every call adds a new snapshot; nothing
    is overwritten, so the table's row/snapshot counts both grow each time
    (unlike the CDC endpoints above, which only forward what *changed*).
    """
    config = get_env_config()
    catalog = lakehouse.get_catalog(config)
    store = DocumentStore(session)
    return lakehouse.ingest(catalog, store, ADMIN_ORG_ID, _TRIGGER_HISTORY_COLLECTION, _LAKEHOUSE_TABLE_NAME)


@router.get("/lakehouse/tables", dependencies=[Depends(require_admin)])
def lakehouse_tables() -> list[str]:
    """Every Iceberg table under the lakehouse namespace — [] before the first ingest."""
    config = get_env_config()
    return lakehouse.list_tables(lakehouse.get_catalog(config))


@router.get("/lakehouse/tables/{table_name}", dependencies=[Depends(require_admin)])
def lakehouse_table(table_name: str) -> dict[str, object]:
    """Row/snapshot counts for one Iceberg table."""
    config = get_env_config()
    info = lakehouse.table_info(lakehouse.get_catalog(config), table_name)
    if info is None:
        raise HTTPException(status_code=404, detail=f"Unknown lakehouse table {table_name!r}.")
    return info


class SemanticViewOut(BaseModel):
    """One entry of the semantic model — the named query itself, not its result."""

    name: str
    description: str
    sql: str


@router.get("/semantic/views", response_model=list[SemanticViewOut], dependencies=[Depends(require_admin)])
def semantic_views() -> list[semantic.SemanticView]:
    """The semantic model: every named, federated query this service can run —
    see core/semantic.py's module docstring for what "federated" means here.
    """
    return semantic.SEMANTIC_VIEWS


@router.post("/semantic/views/{name}/query", dependencies=[Depends(require_admin)])
def semantic_query(name: str) -> dict[str, object]:
    """Run one semantic view: federates the lakehouse's Iceberg table with
    platform-registry's real entitlements/scenarios tables through an embedded
    DuckDB engine (see core/semantic.py) and returns the result rows.
    """
    view = semantic.get_view(name)
    if view is None:
        raise HTTPException(status_code=404, detail=f"Unknown semantic view {name!r}.")
    config = get_env_config()
    return semantic.run_query(view, config, _LAKEHOUSE_TABLE_NAME)
