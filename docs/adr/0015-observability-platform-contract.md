# 0015 — Observability is a platform prerequisite; services honour a contract

**Context.** Every service exposes Prometheus `/metrics`; llm-gateway sends GenAI traces to Langfuse;
GPU numbers come from NVML inside data-platform-manager. There is no shared trace or log pipeline,
and the logs are human-formatted text. Customers run Kubernetes platforms that already have one —
Prometheus or VictoriaMetrics, Grafana, Loki, Tempo behind an OpenTelemetry collector — and a demo
laptop has ~12 GiB for everything (`make k3s-data-demo` already uses ~8.8).

**Decision.**
- **The metrics/logs/traces stack is the platform's, not this framework's.** No Prometheus,
  Grafana, Loki or Tempo in `k8s/base`. The framework ships only its side of the contract and,
  later, the AI-specific pieces (dashboards, alert rules, a collector config snippet) and an
  optional, off-by-default bundle for labs with no platform.
- **The contract, per service:**
  1. `/metrics` in Prometheus format, as today (`observability.configure_metrics`).
  2. Traces over **OTLP/HTTP**, only when the standard `OTEL_EXPORTER_OTLP_ENDPOINT` (or
     `…_TRACES_ENDPOINT`) is set — `telemetry.configure_tracing`: FastAPI server spans (minus
     `/healthz`, `/readyz`, `/metrics`) and httpx client spans, so `traceparent` reaches
     llm-gateway. Unset, the framework imports nothing OpenTelemetry itself.
  3. **JSON logs** on stdout with `LOG_FORMAT=json` (set in k8s): one object per line with `ts`,
     `level`, `service`, `logger`, `msg`, `trace_id`, `span_id`, and a plain traceback.
  4. **No content, no personal data** in spans or logs: every exported span passes
     `RedactingSpanExporter` (prompts, completions, messages, SQL text, bodies, `enduser.*` dropped;
     URLs lose their query string — a test pins the list); log messages never carry them (a coding
     rule, as for governance events). That pipeline must be the **only** exporter: recent FastAPI
     releases read `OTEL_EXPORTER_OTLP_*` themselves at lifespan start-up and attach plain OTLP
     exporters for traces, metrics and logs — found on k3s, every span went out twice, once with its
     query string, and FastAPI's log export carries exception messages and rejected input.
     `configure_tracing` therefore always sets FastAPI's `auto_configure` off (a regression test drives
     a real lifespan).
- **Langfuse stays** the LLM-specific trace store, with content as today (`TRACE_CAPTURE_CONTENT`).
  llm-gateway adds LiteLLM's generic `otel` callback when the endpoint is set, with
  `message_logging: false`; Langfuse's callback keeps a private tracer provider, so its
  content-bearing spans never reach the platform. Both continue the agent's `traceparent`, so one
  trace id links a Tempo trace to its Langfuse trace.

**What the framework ships on top** ([`observability/`](../../observability/README.md)): the collector config,
three Grafana dashboards (LLM gateway, GPU, model drift), alert rules, and the signals behind them —
llm-gateway's OpenTelemetry GenAI metrics (dimensions limited to operation, provider and model), `prediction`'s
model-drift PSI against a reference `training` writes into the model metadata, and the DCGM exporter's GPU
metric names from `data-platform-manager`'s NVML reading where DCGM cannot run (WSL2, consumer cards). Pods
carry `prometheus.io/*` annotations. llm-gateway's platform spans drop LiteLLM's request metadata
(`metadata.*`, `hidden_params`: requester IP and headers, user e-mail, key hash) and gain the same
`gen_ai.*`/`aicf.*` attributes as Langfuse's. An optional lab bundle (`make k3s-obs-up`, off by default)
runs the stack itself for clusters without one.

**Consequences.** Turning observability on is configuration only (`shared-config`), with no
redeploy of anything but the configmap and a rollout. The OpenTelemetry SDK is now a dependency of
every service image (~10 MB of disk each). The framework loads it only when enabled, but redis-py's
optional metrics module imports it whenever it is installed: measured on `prediction`, ~4 MiB of RSS
per Python process with tracing off (~40 MiB across the cluster) — accepted, so that switching
tracing on never needs an image rebuild. uvicorn's own access
log lines stay plain text (they bypass loguru); the collector should parse JSON where it can and
keep other lines as-is. The redaction list follows OpenTelemetry GenAI semconv v1.37 and
OpenInference names; a new instrumentation that invents another content attribute needs adding
there.
