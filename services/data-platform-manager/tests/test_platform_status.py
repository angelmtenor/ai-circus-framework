"""Tests for the admin platform-status feed (core/platform_status.py + GET /platform/status).

No real network: every probe is exercised against an httpx MockTransport / a
monkeypatched TCP-redis probe, and the k8s pod enrichment against a stubbed
`list_pods_by_app` — the module's contract is "never raises, every failure is a row".
"""

from __future__ import annotations

import asyncio
import socket

import httpx
import pytest

from data_platform_manager.core import k8s_jobs, platform_status
from data_platform_manager.core.platform_status import PodInfo, Target


def _run(coro):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function] — tiny asyncio helper for sync tests
    return asyncio.run(coro)


def _http_target(name: str, url: str, **kwargs: object) -> Target:
    return Target(name, "services", url, f"{name} description", **kwargs)  # type: ignore[arg-type]


def _client_with(handler) -> httpx.AsyncClient:  # ruff: ignore[missing-type-function-argument]
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=1.0)


def test_http_probe_reports_up_with_latency() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/healthz"
        return httpx.Response(200, json={"status": "ok"})

    async def go() -> platform_status.ComponentStatus:
        async with _client_with(handler) as client:
            return await platform_status.probe(client, _http_target("prediction", "http://prediction:8000/healthz"))

    result = _run(go())
    assert result.status == "up"
    assert result.detail == "HTTP 200"
    assert result.latency_ms is not None and result.latency_ms >= 0
    assert result.pod is None


def test_http_probe_treats_auth_gated_console_as_up_and_5xx_as_down() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401 if request.url.host == "console" else 503)

    async def go() -> tuple[platform_status.ComponentStatus, platform_status.ComponentStatus]:
        async with _client_with(handler) as client:
            return (
                await platform_status.probe(client, _http_target("console", "http://console:80/")),
                await platform_status.probe(client, _http_target("broken", "http://broken:80/healthz")),
            )

    console, broken = _run(go())
    assert console.status == "up"
    assert broken.status == "down"
    assert broken.detail == "HTTP 503"


def test_connection_error_is_down_but_unresolved_optional_target_is_not_deployed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "kafka":
            raise httpx.ConnectError("[Errno -2] Name or service not known")
        raise httpx.ConnectError("connection refused")

    async def go() -> tuple[platform_status.ComponentStatus, platform_status.ComponentStatus]:
        async with _client_with(handler) as client:
            return (
                await platform_status.probe(client, _http_target("kafka", "http://kafka:9092/", optional=True)),
                await platform_status.probe(client, _http_target("assistant", "http://assistant:8000/healthz")),
            )

    kafka, assistant = _run(go())
    assert kafka.status == "not_deployed"
    assert kafka.detail == "not deployed"
    assert assistant.status == "down"
    assert "ConnectError" in assistant.detail
    assert assistant.latency_ms is None


def test_tcp_probe_unresolved_hostname_is_down_for_required_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    async def failing_open_connection(*_args: object, **_kwargs: object) -> None:
        await asyncio.sleep(0)
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(platform_status.asyncio, "open_connection", failing_open_connection)

    async def go() -> platform_status.ComponentStatus:
        async with httpx.AsyncClient() as client:
            return await platform_status.probe(client, Target("postgres", "infra", "postgres:5432", "db", probe="tcp"))

    result = _run(go())
    assert result.status == "down"


def test_collect_enriches_with_pod_state_only_in_cluster(monkeypatch: pytest.MonkeyPatch) -> None:
    targets = (
        _http_target("prediction", "http://prediction:8000/healthz", app_label="prediction"),
        _http_target("assistant", "http://assistant:8000/healthz", app_label="assistant"),
        _http_target("rag-agent", "http://rag-agent:8000/healthz", app_label="rag-agent"),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200)

    real_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        platform_status.httpx,
        "AsyncClient",
        lambda **kwargs: real_async_client(transport=httpx.MockTransport(handler), timeout=kwargs.get("timeout")),
    )

    # Outside a cluster: probes only, no pod block, in_cluster False.
    monkeypatch.setattr(k8s_jobs, "in_cluster_config_available", lambda: False)
    outside = _run(platform_status.collect(targets))
    assert outside.in_cluster is False
    assert [c.status for c in outside.components] == ["up", "up", "up"]
    assert all(c.pod is None for c in outside.components)

    # In-cluster: a serving-but-not-Ready pod is "degraded"; restarts are surfaced;
    # a label the API doesn't know stays probe-only.
    monkeypatch.setattr(k8s_jobs, "in_cluster_config_available", lambda: True)
    monkeypatch.setattr(
        platform_status,
        "list_pods_by_app",
        lambda: {
            "prediction": PodInfo(ready=True, restarts=0, phase="Running", age_seconds=100),
            "assistant": PodInfo(ready=False, restarts=3, phase="Running", age_seconds=5),
        },
    )
    inside = _run(platform_status.collect(targets))
    assert inside.in_cluster is True
    by_name = {c.name: c for c in inside.components}
    assert by_name["prediction"].status == "up" and by_name["prediction"].pod is not None
    assert by_name["assistant"].status == "degraded" and by_name["assistant"].pod is not None
    assert by_name["assistant"].pod.restarts == 3
    assert by_name["rag-agent"].status == "up" and by_name["rag-agent"].pod is None

    # The k8s API failing must not take the probes down with it.
    def boom() -> dict[str, PodInfo]:
        raise RuntimeError("rbac denied")

    monkeypatch.setattr(platform_status, "list_pods_by_app", boom)
    resilient = _run(platform_status.collect(targets))
    assert [c.status for c in resilient.components] == ["up", "up", "up"]


def test_targets_cover_every_compose_and_k8s_service() -> None:
    """The static target list is hand-synced with the deployment manifests — pin the
    names so adding a service to k8s/base without a dashboard row fails loudly here.
    """
    names = {t.name for t in platform_status.TARGETS}
    assert names == {
        "ui-react",
        "platform-registry",
        "prediction",
        "assistant",
        "rag-agent",
        "form-agent",
        "agui-voice",
        "llm-gateway",
        "data-platform-manager",
        "postgres",
        "keycloak",
        "qdrant",
        "valkey",
        "seaweedfs",
        "kafka",
        "langfuse",
        "langfuse-worker",
        "clickhouse",
        "mlflow",
    }
    assert {t.name for t in platform_status.TARGETS if t.optional} == {"kafka"}
    assert {t.name for t in platform_status.TARGETS if t.console_url} == {"keycloak", "seaweedfs", "langfuse", "mlflow"}
