# Observability — the AI-specific pieces

The metrics, logs and traces **stack** is your platform's (a collector, Prometheus or VictoriaMetrics, Loki,
Tempo, Grafana — shared by everything on the cluster). This framework ships only what is specific to it
([ADR 0015](../docs/adr/0015-observability-platform-contract.md)):

| Folder | What | Use it in your platform |
|---|---|---|
| [`collector/`](collector/otel-collector.yaml) | OpenTelemetry Collector (contrib) config: OTLP in, pod JSON logs tailed, redaction as defence in depth | merge into your collector; set `METRICS_REMOTE_WRITE_URL`, `TRACES_OTLP_ENDPOINT`, `LOGS_OTLP_ENDPOINT` |
| [`dashboards/`](dashboards/) | Grafana: **LLM gateway** (calls, tokens, cost, latency, time to first token), **GPU**, **Model drift**, **MCP servers** (calls, refusals, per-API tokens, audit lines) | import; each picks its datasource through a `datasource` variable |
| [`alerts/`](alerts/ai-circus-rules.yaml) | Prometheus rule groups: slow/expensive/silent LLM calls, 5xx rate, service down, model drift, GPU heat/memory, MCP refusals and refused token exchanges | `rule_files`, vmalert `-rule=`, or a PrometheusRule's `spec` |

Then point the framework at your collector — one key in `k8s/base/configmap.yaml` (`shared-config`):

```yaml
OTEL_EXPORTER_OTLP_ENDPOINT: "http://<your-collector>:4318"
```

and roll the deployments. `LOG_FORMAT: json` is already set there.

## What each service emits

| Signal | Source | Names |
|---|---|---|
| Traces | every service (FastAPI + httpx spans) and llm-gateway (one span per model call) | content-free; `aicf.tenant.id`, `aicf.scenario.slug`, `gen_ai.*` on gateway spans |
| GenAI metrics | llm-gateway over OTLP | `gen_ai_client_operation_duration_*`, `gen_ai_client_token_usage_*` (`gen_ai_token_type`), `gen_ai_usage_cost_*` (USD), `gen_ai_server_time_to_first_token_*` — dimensions: operation, provider (`gen_ai_system`), model only |
| HTTP metrics | every FastAPI service's `/metrics` (pods annotated `prometheus.io/scrape`) | `http_requests_total{handler,method,status}`, `http_request_duration_seconds_*` |
| Model drift | `prediction`'s `/metrics` | `ai_circus_model_feature_psi{scenario,model_version,feature}`, `ai_circus_model_score_psi`, `ai_circus_model_drift_window_rows`, `ai_circus_scored_rows_total{scenario,model_variant}` |
| GPU | NVIDIA's DCGM exporter where it runs; otherwise `data-platform-manager`'s `/metrics` (NVML) under the **same** names, `source="nvml"` | `DCGM_FI_DEV_GPU_UTIL`, `_FB_USED`, `_FB_FREE`, `_GPU_TEMP`, `_POWER_USAGE`; `ai_circus_gpu_telemetry_available` |
| Logs | every pod's stdout, one JSON object per line | `ts`, `level`, `service`, `logger`, `msg`, `trace_id`, `span_id`, `exception` |
| MCP servers (mcp-kit) | each server's `/metrics` and stdout ([ADR 0023](../docs/adr/0023-observability-of-the-mcp-path.md)) | `mcp_kit_tool_calls_total{server,tool,outcome}`, `mcp_kit_access_refused_total{server,reason}`, `mcp_kit_token_exchanges_total{server,outcome}`, `mcp_kit_api_responses_total{server,status_class}`, two latency histograms; audit lines (`logger` = `mcp_kit.audit`) with `trace_id` |

The dashboards and rules group by `service` = the pod's `app` label — relabel your scrape the same way
([`bundle/config/scrape.yaml`](bundle/config/scrape.yaml) shows how).

**Model drift** is the Population Stability Index of what `prediction` scores against the model's training
data, per feature (the 8 most important by SHAP) and for the served score, over a window of up to 2,000
rows (bulk requests sampled to 1,000), reported from 100 rows: < 0.1 stable, 0.1–0.25 moderate, > 0.25
significant. The reference is written by `training`, so a model trained before v0.38.0 reports none until
it is retrained (`make k3s-pipeline`). In a demo most scoring traffic *is* the dataset sample, so PSI stays
low — what-ifs with unusual values move it.

Drift is also in the app itself: **ML Insights → Data drift** (`GET /model/{slug}/drift`) shows each tenant its
*own* recent traffic — windows are kept per tenant, since tenants without a model of their own share the
fallback one. The `/metrics` gauges sum every tenant of a model and carry no tenant label.

**Content never leaves the pods**: spans are redacted at export (`ai_circus_shared.telemetry`,
`llm_gateway/tracing.py`), GenAI metrics carry no request metadata, labels carry no tenant data or values,
log messages never include prompts or personal data. Prompts stay in Langfuse, joined by trace id.

## The optional lab bundle (no platform at hand)

For a laptop or a DGX Spark with nothing else installed — **off by default**, never part of `k3s-all`:

```bash
make k3s-obs-up     # collector, VictoriaMetrics + vmalert, Loki, Tempo, Grafana; services start exporting
make k3s-obs-down   # services stop exporting; the bundle and its data are deleted
```

Grafana: <http://grafana.localhost> (anonymous visitors are Viewers; the admin password is in Secret
`observability/grafana-admin`). Everything is sized for a laptop (~1 GiB of limits), keeps 24 h, and lives
on `emptyDir` — a pod restart starts empty. The collector runs as uid 0 to read `/var/log/pods` (no
capabilities, read-only); that exception is the lab bundle's only. Multi-arch images throughout (amd64 and
arm64).

Regenerate the dashboards after changing a panel: `python3 scripts/generate_observability_dashboards.py`.
