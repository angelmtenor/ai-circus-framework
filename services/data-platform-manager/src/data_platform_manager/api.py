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

import uuid
from datetime import UTC, datetime

import redis
from ai_circus_shared.auth import ADMIN_ORG_ID, is_admin_bearer_token
from ai_circus_shared.cache import TenantCache
from ai_circus_shared.document_store import DbSession, DocumentStore
from ai_circus_shared.document_store import get_session as get_document_session
from fastapi import APIRouter, Depends, Header, HTTPException
from kubernetes.client.exceptions import ApiException
from pydantic import BaseModel

from data_platform_manager import get_env_config
from data_platform_manager.core import gateway, k8s_jobs
from data_platform_manager.core.cache_client import get_client
from data_platform_manager.core.roadmap import Capability, get_roadmap

router = APIRouter()

_JOB_STATUS_CACHE_TTL_SECONDS = 5
_TRIGGER_HISTORY_COLLECTION = "pipeline-triggers"


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
) -> dict[str, str]:
    """Delete-and-recreate one pipeline Job (etl-tabular/training/etl-vectorize).
    Every trigger is recorded to this service's own document store (an audit trail
    of who ran what, when — visible to an operator even after the Job itself is
    long gone) and immediately invalidates that job's cached status.
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
    store.put(
        ADMIN_ORG_ID,
        _TRIGGER_HISTORY_COLLECTION,
        str(uuid.uuid4()),
        {"job": name, "triggered_at": triggered_at},
    )
    return {"job": name, "triggered_at": triggered_at}


@router.get("/pipeline/triggers/history", dependencies=[Depends(require_admin)])
def trigger_history(session: DbSession = Depends(get_document_session)) -> list[dict[str, object]]:
    """Most-recently-triggered-first audit trail of every job this service has run."""
    store = DocumentStore(session)
    return [doc.content for doc in store.list(ADMIN_ORG_ID, _TRIGGER_HISTORY_COLLECTION)]
