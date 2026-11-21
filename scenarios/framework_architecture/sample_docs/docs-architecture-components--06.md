# Components and request flows - Observability

Source: docs/architecture/components.md (this repository)

Metrics, logs and traces of every service, plus the AI-specific monitors: a trace per LLM call and a record per training run.

| Component | What it does | Technology | How to switch it on | ADR |
|---|---|---|---|---|
| **Langfuse** | A trace per LLM call by tenant, scenario and conversation, fed only by llm-gateway. | Langfuse v4 (web, worker) | always on | — |
| **MLflow** | Every training run's candidates, scores and the selected model; never allowed to fail a training job. | MLflow | always on | — |
| **OpenTelemetry collector** | Receives every service's traces and logs over OTLP, masks tokens and emails in log lines, forwards them. | OpenTelemetry collector | make k3s-obs-up | [0015](../adr/0015-observability-platform-contract.md), [0028](../adr/0028-masked-logs-and-one-trace-through-the-mcp-hop.md) |
| **VictoriaMetrics** | Scrapes every service's /metrics and evaluates the alert rules. | VictoriaMetrics · vmalert | make k3s-obs-up | [0015](../adr/0015-observability-platform-contract.md) |
| **Loki** | Stores the JSON log lines, each with its trace id. | Grafana Loki | make k3s-obs-up | — |
| **Tempo** | Stores the content-free traces, including one trace across the MCP hop. | Grafana Tempo | make k3s-obs-up | [0028](../adr/0028-masked-logs-and-one-trace-through-the-mcp-hop.md) |
| **Grafana** | Dashboards for the LLM gateway (calls, tokens, cost, latency), GPUs, model drift and the MCP path. | Grafana | make k3s-obs-up | — |
