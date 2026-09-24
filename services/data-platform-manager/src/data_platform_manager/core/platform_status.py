"""
- Title:    Platform status — is every microservice/infra piece up and healthy?
- Author:   ai-circus-framework contributors

Backs the admin dashboard's "Platform" page (ui-react/src/PlatformStatus.tsx) through
GET /platform/status: one probe per component, all run concurrently, plus — when this
process is itself a pod — each component's ready/restart counts from the Kubernetes
API. The probe list is static on purpose: every hostname below is a Service DNS name
that is identical in docker-compose.yml and k8s/base (that parity is a design rule of
this repo), so there is nothing to configure per deployment, and the browser never
has to reach internal-only services (llm-gateway, Postgres, Valkey, ClickHouse, ...)
itself — it asks this admin-gated endpoint instead, which is also the only place that
can distinguish "not deployed" (an optional profile such as Kafka) from "down".

Probes are deliberately the cheapest signal each component offers (its own liveness
endpoint or a bare TCP connect) with a short timeout — this runs every few seconds
while the dashboard is open and must never load the platform it is watching.
"""

from __future__ import annotations

import asyncio
import dataclasses
import socket
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Literal, cast

import httpx
import redis

from data_platform_manager.core import k8s_jobs

PROBE_TIMEOUT_SECONDS = 2.0
Group = Literal["services", "infra", "observability"]
Status = Literal["up", "degraded", "down", "not_deployed"]


@dataclass(frozen=True)
class Target:
    """One component to probe. `probe` is "http" (2xx/3xx = up), "tcp" (connect = up) or
    "redis" (PING = up). `app_label` is the pod's `app`/`job-name` label for the k8s
    enrichment; `console_url` is the browser-facing admin UI, if the component has one.
    `optional` components (opt-in compose profiles / k8s overlays) report "not_deployed"
    rather than "down" when unreachable.
    """

    name: str
    group: Group
    url: str
    description: str
    probe: Literal["http", "tcp", "redis"] = "http"
    app_label: str | None = None
    console_url: str | None = None
    optional: bool = False


# Hostnames + ports mirror k8s/base/*.yaml's Services and docker-compose.yml's
# service names/container ports exactly — the same "documented hand-sync" convention
# core/k8s_jobs.py follows for the pipeline Jobs.
TARGETS: tuple[Target, ...] = (
    # ── Platform services ──────────────────────────────────────────────────────
    Target(
        "ui-react",
        "services",
        "http://ui-react:80/",
        "Single-page app (Traefik: aiopen.localhost)",
        app_label="ui-react",
    ),
    Target(
        "platform-registry",
        "services",
        "http://platform-registry:8000/healthz",
        "Scenario/entitlement registry — source of truth for tenants",
        app_label="platform-registry",
    ),
    Target(
        "prediction",
        "services",
        "http://prediction:8000/healthz",
        "tabular_ml inference + SHAP explanations",
        app_label="prediction",
    ),
    Target(
        "dl-inference",
        "services",
        "http://dl-inference:8000/healthz",
        "deep_learning inference (ONNX) + occlusion explanations — optional overlay (make k3s-dl-up)",
        app_label="dl-inference",
        optional=True,
    ),
    Target(
        "assistant", "services", "http://assistant:8000/healthz", "tabular_ml chat agent (AG-UI)", app_label="assistant"
    ),
    Target(
        "rag-agent", "services", "http://rag-agent:8000/healthz", "conversational_rag chat agent", app_label="rag-agent"
    ),
    Target(
        "form-agent", "services", "http://form-agent:8000/healthz", "assisted_form chat agent", app_label="form-agent"
    ),
    Target(
        "agui-voice",
        "services",
        "http://agui-voice:8000/healthz",
        "Voice mode (STT/TTS bridge to the chat agents)",
        app_label="agui-voice",
    ),
    Target(
        "llm-gateway",
        "services",
        "http://llm-gateway:4000/health/liveliness",
        "LiteLLM proxy — every LLM call routes through here",
        app_label="llm-gateway",
    ),
    Target(
        "data-platform-manager",
        "services",
        "http://data-platform-manager:8000/healthz",
        "This admin control plane",
        app_label="data-platform-manager",
    ),
    # ── Infrastructure ─────────────────────────────────────────────────────────
    Target(
        "postgres",
        "infra",
        "postgres:5432",
        "Relational store (registry, Keycloak, conversations, Langfuse, MLflow)",
        probe="tcp",
        app_label="postgres",
    ),
    Target(
        "keycloak",
        "infra",
        "http://keycloak:9000/health/ready",
        "Identity provider — Organizations are the tenants",
        app_label="keycloak",
        console_url="http://admin.keycloak.localhost",
    ),
    Target(
        "qdrant",
        "infra",
        "http://qdrant:6333/healthz",
        "Vector store for conversational_rag / assisted_form",
        app_label="qdrant",
    ),
    Target(
        "valkey",
        "infra",
        "valkey:6379",
        "Cache / rate-limit / budget counters / Langfuse queue",
        probe="redis",
        app_label="valkey",
    ),
    Target(
        "seaweedfs",
        "infra",
        "http://seaweedfs:8888/",
        "S3-compatible object storage (datasets, models, documents)",
        app_label="seaweedfs",
        console_url="http://console.objectstore.localhost",
    ),
    Target(
        "kafka",
        "infra",
        "kafka:9092",
        "Event streaming — optional Data Platform profile",
        probe="tcp",
        app_label="kafka",
        optional=True,
    ),
    # ── Observability ──────────────────────────────────────────────────────────
    Target(
        "langfuse",
        "observability",
        "http://langfuse-web:3000/api/public/health",
        "GenAI monitor — traces of every LLM call, per tenant/scenario",
        app_label="langfuse-web",
        console_url="http://langfuse.localhost",
    ),
    Target(
        "langfuse-worker",
        "observability",
        "http://langfuse-worker:3030/api/health",
        "Langfuse ingestion worker",
        app_label="langfuse-worker",
    ),
    Target(
        "clickhouse", "observability", "http://clickhouse:8123/ping", "Langfuse trace store", app_label="clickhouse"
    ),
    Target(
        "mlflow",
        "observability",
        "http://mlflow:5000/health",
        "MLOps monitor — every training run, per scenario/tenant",
        app_label="mlflow",
        console_url="http://mlflow.localhost",
    ),
)


@dataclass(frozen=True)
class PodInfo:
    """What the Kubernetes API says about a component's pod(s)."""

    ready: bool
    restarts: int
    phase: str
    age_seconds: int | None


@dataclass(frozen=True)
class ComponentStatus:
    """One probed component, as returned to the dashboard."""

    name: str
    group: Group
    status: Status
    latency_ms: int | None
    detail: str
    description: str
    console_url: str | None
    pod: PodInfo | None = None


@dataclass(frozen=True)
class PlatformStatus:
    """The whole dashboard payload."""

    checked_at: str
    in_cluster: bool
    components: list[ComponentStatus] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        """JSON-ready form (nested dataclasses included) for the API response."""
        return asdict(self)


def _host_port(url: str) -> tuple[str, int]:
    host, _, port = url.rpartition(":")
    return host, int(port)


async def _probe_http(client: httpx.AsyncClient, target: Target) -> tuple[bool, str]:
    response = await client.get(target.url)
    # 3xx counts: a SPA/console answering a redirect is alive; auth-gated consoles
    # (401/403) are alive too — the point is "is the process serving", not "am I allowed".
    ok = response.status_code < 500
    return ok, f"HTTP {response.status_code}"


async def _probe_tcp(target: Target) -> tuple[bool, str]:
    host, port = _host_port(target.url)
    _, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=PROBE_TIMEOUT_SECONDS)
    writer.close()
    await writer.wait_closed()
    return True, "TCP connect"


async def _probe_redis(target: Target) -> tuple[bool, str]:
    host, port = _host_port(target.url)

    def ping() -> bool:
        client = redis.Redis(
            host=host, port=port, socket_connect_timeout=PROBE_TIMEOUT_SECONDS, socket_timeout=PROBE_TIMEOUT_SECONDS
        )
        try:
            return bool(client.ping())
        finally:
            client.close()

    ok = await asyncio.to_thread(ping)
    return ok, "PING"


async def probe(client: httpx.AsyncClient, target: Target) -> ComponentStatus:
    """Probe one target; never raises — every failure becomes a "down"/"not_deployed" row."""
    started = time.perf_counter()
    try:
        if target.probe == "http":
            ok, detail = await _probe_http(client, target)
        elif target.probe == "tcp":
            ok, detail = await _probe_tcp(target)
        else:
            ok, detail = await _probe_redis(target)
        latency_ms: int | None = int((time.perf_counter() - started) * 1000)
        status: Status = "up" if ok else "down"
    except (httpx.HTTPError, OSError, redis.RedisError, TimeoutError, socket.gaierror) as exc:
        latency_ms = None
        # A hostname that doesn't resolve is the signature of "not deployed at all"
        # (no Service / no compose container), as opposed to a crashed pod whose
        # Service DNS still exists — for optional profiles that's the expected state.
        unresolved = (
            isinstance(exc, socket.gaierror) or "getaddrinfo" in str(exc) or "Name or service not known" in str(exc)
        )
        status = "not_deployed" if target.optional and unresolved else "down"
        detail = "not deployed" if status == "not_deployed" else f"{type(exc).__name__}: {exc}"[:200]
    return ComponentStatus(
        name=target.name,
        group=target.group,
        status=status,
        latency_ms=latency_ms,
        detail=detail,
        description=target.description,
        console_url=target.console_url,
    )


def list_pods_by_app() -> dict[str, PodInfo]:
    """Ready/restart counts per `app` (or `job-name`) label, from the Kubernetes API.

    Only meaningful in-cluster (needs the pods-list RBAC in k8s/base/data-platform-
    manager.yaml). Returns the *newest* pod per label so a rolling restart shows the
    replacement, not the terminating one.
    """
    from kubernetes import client, config

    config.load_incluster_config()
    core = client.CoreV1Api()
    pods = cast("client.V1PodList", core.list_namespaced_pod(k8s_jobs.NAMESPACE))
    now = datetime.now(UTC)
    result: dict[str, tuple[datetime, PodInfo]] = {}
    for pod in pods.items or []:
        meta = pod.metadata
        labels = (meta.labels or {}) if meta else {}
        label = labels.get("app") or labels.get("job-name")
        if not label or pod.status is None:
            continue
        container_statuses = pod.status.container_statuses or []
        ready = bool(container_statuses) and all(cs.ready for cs in container_statuses)
        restarts = sum(cs.restart_count or 0 for cs in container_statuses)
        created = meta.creation_timestamp if meta else None
        age = int((now - created).total_seconds()) if created else None
        info = PodInfo(ready=ready, restarts=restarts, phase=pod.status.phase or "Unknown", age_seconds=age)
        stamp = created or datetime.min.replace(tzinfo=UTC)
        if label not in result or stamp > result[label][0]:
            result[label] = (stamp, info)
    return {label: info for label, (_, info) in result.items()}


def _merge_pod(component: ComponentStatus, target: Target, pods: dict[str, PodInfo]) -> ComponentStatus:
    info = pods.get(target.app_label or "")
    if info is None:
        return component
    status = component.status
    if status == "up" and not info.ready:
        # Answering its probe but not Ready for k8s (readiness gate failing, or a
        # replacement pod still starting): serving, but not healthy.
        status = "degraded"
    return dataclasses.replace(component, status=status, pod=info)


async def collect(targets: tuple[Target, ...] = TARGETS) -> PlatformStatus:
    """Probe every target concurrently and (in-cluster) enrich with pod state."""
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS, follow_redirects=False) as client:
        components = list(await asyncio.gather(*(probe(client, t) for t in targets)))

    in_cluster = k8s_jobs.in_cluster_config_available()
    if in_cluster:
        try:
            pods = await asyncio.to_thread(list_pods_by_app)
        except Exception:
            pods = {}
        components = [_merge_pod(c, t, pods) for c, t in zip(components, targets, strict=True)]

    return PlatformStatus(checked_at=datetime.now(UTC).isoformat(), in_cluster=in_cluster, components=components)
