"""OpenTelemetry traces and JSON logs — the services' half of the platform observability contract.

The metrics/logs/traces *stack* (a collector, VictoriaMetrics, Loki, Tempo, Grafana) is a platform
prerequisite, never part of this framework (ADR 0015). What every service owes that platform:

- **Traces over OTLP**, only when the platform says where: `configure_tracing(app, service)` does
  nothing — and imports nothing itself — unless the standard
  `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` / `OTEL_EXPORTER_OTLP_ENDPOINT` is set. Then it instruments
  the FastAPI app (minus `/healthz`, `/readyz`, `/metrics`) and outgoing httpx calls, so a request's
  `traceparent` reaches llm-gateway and its Langfuse trace shares the same trace id. Every other
  standard `OTEL_*` variable (sampler, resource attributes, headers) keeps its usual meaning.
- **Content-free spans**: every span goes through `RedactingSpanExporter` before it leaves the pod —
  prompts, completions, message bodies, SQL text and request/response bodies are dropped and URLs
  lose their query string (`redact_attributes`). Prompts belong to Langfuse alone.
- **JSON logs**: with `LOG_FORMAT=json` a service's loguru logger writes one JSON object per line
  (`install_json_sink`) carrying the current `trace_id`/`span_id`, so the platform's log store can
  jump from a log line to its trace. Log *messages* must never contain prompts or personal data;
  that is a coding rule (governance events follow the same one), not something a sink can detect.

`/metrics` stays as it is (`observability.configure_metrics`).
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from collections.abc import Mapping, Sequence
from typing import Any, TextIO

TRACES_ENDPOINT_VARS = ("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "OTEL_EXPORTER_OTLP_ENDPOINT")
UNTRACED_ROUTES = "healthz,readyz,metrics"

# Attribute keys (exact, or as a dotted prefix) that may carry prompts, completions, message bodies
# or personal data — dropped from every exported span.
DROPPED_ATTRIBUTE_PREFIXES = (
    "gen_ai.prompt",
    "gen_ai.completion",
    "gen_ai.input.messages",
    "gen_ai.output.messages",
    "gen_ai.system_instructions",
    "gen_ai.tool.call.arguments",
    "gen_ai.tool.call.result",
    "llm.input_messages",
    "llm.output_messages",
    "llm.prompts",
    "input.value",
    "output.value",
    "http.request.body",
    "http.response.body",
    "db.statement",
    "db.query.text",
    "enduser",
    "user.email",
    "user.name",
)
# Attributes holding a URL or a request target: kept, minus the query string.
URL_ATTRIBUTES = ("url.full", "http.url", "http.target", "url.query")

_provider: Any = None


def tracing_endpoint(env: Mapping[str, str] | None = None) -> str | None:
    """The OTLP endpoint traces go to, or None when tracing is off (no endpoint, or disabled)."""
    env = os.environ if env is None else env
    if env.get("OTEL_SDK_DISABLED", "").strip().lower() == "true":
        return None
    if env.get("OTEL_TRACES_EXPORTER", "").strip().lower() == "none":
        return None
    return next((env[var].strip() for var in TRACES_ENDPOINT_VARS if env.get(var, "").strip()), None)


def _strip_query(value: object) -> object:
    return value.split("?", 1)[0] if isinstance(value, str) else value


def redact_attributes(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
    """`attributes` without anything that may carry content or personal data (see module docstring)."""
    kept: dict[str, Any] = {}
    for key, value in (attributes or {}).items():
        if any(key == prefix or key.startswith(f"{prefix}.") for prefix in DROPPED_ATTRIBUTE_PREFIXES):
            continue
        if key == "url.query":
            continue
        kept[key] = _strip_query(value) if key in URL_ATTRIBUTES else value
    return kept


def _redacted_span(span: Any) -> Any:
    from opentelemetry.sdk.trace import Event, ReadableSpan

    events = tuple(Event(e.name, redact_attributes(e.attributes), e.timestamp) for e in span.events)
    return ReadableSpan(
        name=span.name,
        context=span.context,
        parent=span.parent,
        resource=span.resource,
        attributes=redact_attributes(span.attributes),
        events=events,
        links=span.links,
        kind=span.kind,
        status=span.status,
        start_time=span.start_time,
        end_time=span.end_time,
        instrumentation_scope=span.instrumentation_scope,
    )


def redacting_exporter(inner: Any) -> Any:
    """Wrap an OpenTelemetry `SpanExporter` so every span it exports is redacted first."""
    from opentelemetry.sdk.trace.export import SpanExporter

    class RedactingSpanExporter(SpanExporter):
        def export(self, spans: Sequence[Any]) -> Any:
            return inner.export([_redacted_span(span) for span in spans])

        def shutdown(self) -> None:
            inner.shutdown()

        def force_flush(self, timeout_millis: int = 30_000) -> bool:
            return inner.force_flush(timeout_millis)

    return RedactingSpanExporter()


def disable_fastapi_auto_export(app: Any) -> None:
    """Stop FastAPI's built-in telemetry from adding its own OTLP exporters from the environment.

    Recent FastAPI releases read `OTEL_EXPORTER_OTLP_*` at lifespan start-up and attach plain OTLP
    exporters for traces, metrics and logs to the global providers — a second, *unredacted* copy of
    every span (query strings included) and log records with exception messages and rejected input.
    Its documented switch is `FastAPI(telemetry={"auto_configure": False})`; `configure_tracing`
    receives an app that already exists, so it flips the same setting on it. A FastAPI without
    built-in telemetry has no `_telemetry` and is left alone.
    """
    config = getattr(app, "_telemetry", None)
    if isinstance(config, dict):
        config["auto_configure"] = False


def configure_tracing(app: Any, service: str, env: Mapping[str, str] | None = None) -> bool:
    """Export this service's traces over OTLP when the platform gives an endpoint; else a no-op.

    Call once, right after `configure_metrics(app)`. Returns whether tracing is on. The tracer
    provider and the httpx instrumentation are process-wide and set up once; `app` may be None for
    a process with no FastAPI app (a Job). FastAPI's own environment auto-export is switched off
    either way, so this redacting pipeline is the only thing that ever exports.
    """
    global _provider
    env = os.environ if env is None else env
    if app is not None:
        disable_fastapi_auto_export(app)
    if tracing_endpoint(env) is None:
        return False

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    if _provider is None:
        resource = Resource.create(
            {SERVICE_NAME: env.get("OTEL_SERVICE_NAME") or service, "service.namespace": "ai-circus"}
        )
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(redacting_exporter(OTLPSpanExporter())))
        trace.set_tracer_provider(provider)
        HTTPXClientInstrumentor().instrument()
        _provider = provider
    if app is not None:
        # No per-message ASGI `receive`/`send` spans: a streamed chat answer made ~50 of them per request.
        FastAPIInstrumentor.instrument_app(
            app, excluded_urls=UNTRACED_ROUTES, tracer_provider=_provider, exclude_spans=["receive", "send"]
        )
    return True


# ── JSON logs ─────────────────────────────────────────────────────────────────


def log_format_is_json(env: Mapping[str, str] | None = None) -> bool:
    """Whether `LOG_FORMAT=json` asks for one JSON object per log line (set in k8s, not for `make run`)."""
    env = os.environ if env is None else env
    return env.get("LOG_FORMAT", "").strip().lower() == "json"


def current_trace_ids() -> tuple[str, str] | None:
    """(trace_id, span_id) of the active span as hex, or None — never imports OpenTelemetry itself."""
    trace = sys.modules.get("opentelemetry.trace")
    if trace is None:
        return None
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None
    return format(context.trace_id, "032x"), format(context.span_id, "016x")


def json_log_line(record: Mapping[str, Any], service: str) -> str:
    """One loguru record as a JSON line: ts, level, service, logger, msg, trace ids, traceback."""
    extra = record.get("extra") or {}
    line: dict[str, Any] = {
        "ts": record["time"].isoformat(),
        "level": record["level"].name,
        "service": service,
        "logger": extra.get("name") or record.get("name"),
        "msg": record["message"],
    }
    ids = current_trace_ids()
    if ids:
        line["trace_id"], line["span_id"] = ids
    exception = record.get("exception")
    if exception is not None and exception.type is not None:
        # The plain traceback — never loguru's `diagnose` view, which prints local variable values.
        line["exception"] = "".join(traceback.format_exception(exception.type, exception.value, exception.traceback))
    return json.dumps(line, ensure_ascii=False, default=str)


def install_json_sink(logger: Any, *, level: str, service: str, stream: TextIO | None = None) -> int:
    """Add a loguru sink writing `json_log_line` records to stdout (one object per line); returns its id."""
    out = stream or sys.stdout

    def sink(message: Any) -> None:
        out.write(json_log_line(message.record, service) + "\n")
        out.flush()

    return logger.add(sink, level=level, format="{message}")
