"""Tests for ai_circus_shared.telemetry: off by default, content-free spans, JSON logs with trace ids."""

from __future__ import annotations

import io
import json
import subprocess
import sys
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from ai_circus_shared import telemetry
from ai_circus_shared.telemetry import (
    configure_tracing,
    install_json_sink,
    json_log_line,
    log_format_is_json,
    redact_attributes,
    redacting_exporter,
    tracing_endpoint,
)


def test_tracing_is_off_without_an_endpoint_and_imports_nothing() -> None:
    # A fresh interpreter: with no OTLP endpoint, configuring tracing must not even import OpenTelemetry.
    code = (
        "import sys; from ai_circus_shared.telemetry import configure_tracing;"
        "assert configure_tracing(None, 'svc', env={}) is False;"
        "assert not any(m.startswith('opentelemetry.sdk') for m in sys.modules), 'sdk imported'"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, None),
        ({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318"}, "http://collector:4318"),
        (
            {"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "http://c:4318/v1/traces", "OTEL_EXPORTER_OTLP_ENDPOINT": "x"},
            "http://c:4318/v1/traces",
        ),
        ({"OTEL_EXPORTER_OTLP_ENDPOINT": "  "}, None),
        ({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://c:4318", "OTEL_SDK_DISABLED": "true"}, None),
        ({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://c:4318", "OTEL_TRACES_EXPORTER": "none"}, None),
    ],
)
def test_tracing_endpoint(env: dict[str, str], expected: str | None) -> None:
    assert tracing_endpoint(env) == expected


def test_redact_attributes_drops_content_and_query_strings() -> None:
    attributes = {
        "gen_ai.request.model": "groq-llama",
        "gen_ai.usage.input_tokens": 12,
        "gen_ai.prompt.0.content": "my salary is 40k",
        "gen_ai.input.messages": "[...]",
        "gen_ai.completion.0.content": "...",
        "llm.input_messages.0.message.content": "...",
        "input.value": "...",
        "db.statement": "SELECT * FROM people WHERE email='a@b.c'",
        "http.request.body": "{...}",
        "enduser.id": "alice",
        "url.full": "http://prediction/predict/churn?token=secret&email=a@b.c",
        "http.target": "/predict/churn?x=1",
        "url.query": "token=secret",
        "http.route": "/predict/{scenario_slug}",
        "aicf.tenant.id": "acme",
    }
    assert redact_attributes(attributes) == {
        "gen_ai.request.model": "groq-llama",
        "gen_ai.usage.input_tokens": 12,
        "url.full": "http://prediction/predict/churn",
        "http.target": "/predict/churn",
        "http.route": "/predict/{scenario_slug}",
        "aicf.tenant.id": "acme",
    }


def test_a_dropped_prefix_never_swallows_a_lookalike_key() -> None:
    # A prefix drops itself and its dotted children ("input.value.x"), never a lookalike ("input.values_count").
    assert redact_attributes({"input.values_count": 3, "user.id_hash": "h"}) == {
        "input.values_count": 3,
        "user.id_hash": "h",
    }


def test_exported_spans_are_redacted() -> None:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    sink = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(redacting_exporter(sink)))
    with provider.get_tracer("t").start_as_current_span("chat") as span:
        span.set_attribute("gen_ai.request.model", "m")
        span.set_attribute("gen_ai.input.messages", "secret prompt")
        span.add_event("gen_ai.content.prompt", {"gen_ai.prompt": "secret prompt", "kept": 1})
    (exported,) = sink.get_finished_spans()
    assert dict(exported.attributes) == {"gen_ai.request.model": "m"}
    assert dict(exported.events[0].attributes) == {"kept": 1}
    assert exported.context.trace_id == span.get_span_context().trace_id


def test_configure_tracing_instruments_once_and_reports_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI

    monkeypatch.setattr(telemetry, "_provider", None)
    env = {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:9"}  # nothing listens; export is batched/async
    monkeypatch.setattr("opentelemetry.trace.set_tracer_provider", lambda provider: None)
    first, second = FastAPI(), FastAPI()
    assert configure_tracing(first, "prediction", env=env) is True
    provider = telemetry._provider
    assert configure_tracing(second, "prediction", env=env) is True
    assert telemetry._provider is provider
    assert provider.resource.attributes["service.name"] == "prediction"
    provider.shutdown()


def test_fastapi_never_adds_its_own_unredacted_exporters(monkeypatch: pytest.MonkeyPatch) -> None:
    # Regression: FastAPI's built-in telemetry read OTEL_EXPORTER_OTLP_ENDPOINT at lifespan start-up and
    # attached a plain BatchSpanProcessor next to ours — every span went out twice, once unredacted.
    runtime = pytest.importorskip("fastapi.telemetry._runtime")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")
    monkeypatch.setattr(telemetry, "_provider", None)
    monkeypatch.setattr("opentelemetry.trace.set_tracer_provider", lambda provider: None)
    monkeypatch.setattr(runtime, "_configured", [])
    monkeypatch.setattr(runtime, "_owned", [])
    app = FastAPI()
    assert configure_tracing(app, "svc") is True
    # As in a pod: ours is the global provider FastAPI would attach to (set_tracer_provider is stubbed
    # above because OpenTelemetry allows setting it only once per process).
    monkeypatch.setattr("opentelemetry.trace.get_tracer_provider", lambda: telemetry._provider)
    with TestClient(app):  # runs the lifespan, where FastAPI would auto-configure
        pass
    assert runtime._configured == []
    assert [type(p).__name__ for p in telemetry._provider._active_span_processor._span_processors] == [
        "BatchSpanProcessor"
    ]
    telemetry._provider.shutdown()


def test_a_request_is_one_server_span_without_asgi_message_spans(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    monkeypatch.setattr(telemetry, "_provider", None)
    monkeypatch.setattr("opentelemetry.trace.set_tracer_provider", lambda provider: None)
    app = FastAPI()

    @app.get("/items/{item}")
    def item(item: str) -> dict:
        return {"item": item}

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    assert configure_tracing(app, "svc", env={"OTEL_EXPORTER_OTLP_ENDPOINT": "http://127.0.0.1:9"}) is True
    sink = InMemorySpanExporter()
    telemetry._provider.add_span_processor(SimpleSpanProcessor(redacting_exporter(sink)))
    client = TestClient(app)
    client.get("/items/a?email=a@b.c")
    client.get("/healthz")  # untraced
    (span,) = sink.get_finished_spans()
    assert span.name == "GET /items/{item}"
    assert "email" not in str(dict(span.attributes))
    telemetry._provider.shutdown()


def test_fastapi_auto_export_is_off_even_with_tracing_off() -> None:
    # A metrics- or logs-only endpoint must not let FastAPI export log records with exception text either.
    from fastapi import FastAPI

    app = FastAPI()
    assert configure_tracing(app, "svc", env={}) is False
    assert getattr(app, "_telemetry", {"auto_configure": False})["auto_configure"] is False


def _record(message: str, *, exception: object = None) -> dict:
    return {
        "time": datetime(2026, 11, 3, 12, 0, tzinfo=UTC),
        "level": SimpleNamespace(name="INFO"),
        "message": message,
        "name": "prediction.api",
        "extra": {"name": "prediction.api"},
        "exception": exception,
    }


def test_json_log_line_carries_the_active_trace_ids() -> None:
    from opentelemetry.sdk.trace import TracerProvider

    tracer = TracerProvider().get_tracer("t")
    line = json.loads(json_log_line(_record("outside a span"), "prediction"))
    assert line == {
        "ts": "2026-11-03T12:00:00+00:00",
        "level": "INFO",
        "service": "prediction",
        "logger": "prediction.api",
        "msg": "outside a span",
    }
    with tracer.start_as_current_span("request") as span:
        line = json.loads(json_log_line(_record("inside"), "prediction"))
    assert line["trace_id"] == format(span.get_span_context().trace_id, "032x")
    assert line["span_id"] == format(span.get_span_context().span_id, "016x")


def test_json_log_line_includes_a_plain_traceback() -> None:
    try:
        raise ValueError("boom")
    except ValueError as e:
        info = SimpleNamespace(type=type(e), value=e, traceback=e.__traceback__)
    line = json.loads(json_log_line(_record("failed", exception=info), "svc"))
    assert line["exception"].startswith("Traceback") and "ValueError: boom" in line["exception"]


def test_install_json_sink_writes_one_object_per_line() -> None:
    from loguru import logger

    stream = io.StringIO()
    handler = install_json_sink(logger, level="INFO", service="svc", stream=stream)
    try:
        logger.bind(name="x").info("hello {}", "world")
    finally:
        logger.remove(handler)
    (line,) = stream.getvalue().splitlines()
    assert json.loads(line)["msg"] == "hello world"


def test_log_format_is_json() -> None:
    assert log_format_is_json({"LOG_FORMAT": " JSON "}) is True
    assert log_format_is_json({}) is False
    assert log_format_is_json({"LOG_FORMAT": "text"}) is False
