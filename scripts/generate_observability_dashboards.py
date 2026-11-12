#!/usr/bin/env python3
"""Regenerate observability/dashboards/*.json — the framework's AI-specific Grafana dashboards (ADR 0015).

Pure stdlib and deterministic, like the diagram generators. Run from the repo root after changing a panel:

    python3 scripts/generate_observability_dashboards.py

Every dashboard picks its Prometheus-compatible datasource through a `datasource` variable, so it imports
unchanged into a platform's Grafana as well as the optional lab bundle's. Metric names are the ones
observability/collector/otel-collector.yaml produces (GenAI histograms without unit suffixes) and the
services' own /metrics (http_*, ai_circus_*, DCGM_FI_* — scraped, label `service` = the pod's `app`).
"""

from __future__ import annotations

import json
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "observability" / "dashboards"
DS = {"type": "prometheus", "uid": "${datasource}"}


def target(expr: str, legend: str = "", ref: str = "A", instant: bool = False) -> dict:
    t = {"datasource": DS, "expr": expr, "legendFormat": legend or "__auto", "refId": ref, "range": not instant}
    if instant:
        t["instant"] = True
        t["format"] = "table"
    return t


def panel(
    pid: int,
    title: str,
    kind: str,
    grid: tuple[int, int, int, int],
    targets: list[dict],
    *,
    unit: str = "short",
    description: str = "",
    thresholds: list[tuple[str, float | None]] | None = None,
    decimals: int | None = None,
    extra_options: dict | None = None,
    no_value: str | None = None,
) -> dict:
    x, y, w, h = grid
    steps = [{"color": color, "value": value} for color, value in (thresholds or [("green", None)])]
    defaults: dict = {"unit": unit, "thresholds": {"mode": "absolute", "steps": steps}}
    if no_value is not None:
        defaults["noValue"] = no_value
    if decimals is not None:
        defaults["decimals"] = decimals
    if kind == "timeseries":
        defaults["custom"] = {"lineWidth": 2, "fillOpacity": 10, "showPoints": "never", "spanNulls": True}
        if thresholds:
            defaults["custom"]["thresholdsStyle"] = {"mode": "dashed"}
    options: dict = {"legend": {"displayMode": "list", "placement": "bottom", "showLegend": True}}
    if kind in ("stat", "bargauge", "gauge"):
        options = {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "colorMode": "value"}
    options.update(extra_options or {})
    return {
        "id": pid,
        "type": kind,
        "title": title,
        "description": description,
        "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": options,
        "targets": targets,
    }


def dashboard(uid: str, title: str, description: str, panels: list[dict], variables: list[dict] | None = None) -> dict:
    datasource_var = {
        "name": "datasource",
        "label": "Metrics",
        "type": "datasource",
        "query": "prometheus",
        "current": {},
        "hide": 0,
    }
    return {
        "uid": uid,
        "title": title,
        "description": description,
        "tags": ["ai-circus"],
        "schemaVersion": 39,
        "version": 1,
        "editable": True,
        "graphTooltip": 1,
        "refresh": "30s",
        "time": {"from": "now-6h", "to": "now"},
        "timepicker": {},
        "timezone": "browser",
        "annotations": {"list": []},
        "links": [],
        "templating": {"list": [datasource_var, *(variables or [])]},
        "panels": panels,
    }


def query_var(name: str, label: str, query: str) -> dict:
    return {
        "name": name,
        "label": label,
        "type": "query",
        "datasource": DS,
        "query": {"query": query, "refId": "PrometheusVariableQueryEditor-VariableQuery"},
        "definition": query,
        "includeAll": True,
        "multi": True,
        "allValue": ".*",
        "current": {"selected": True, "text": "All", "value": "$__all"},
        "refresh": 2,
        "sort": 1,
        "hide": 0,
    }


MODEL = '{gen_ai_request_model=~"$model"}'
GATEWAY = dashboard(
    "ai-circus-llm-gateway",
    "AI Circus · LLM gateway — tokens, cost, latency",
    "Every model call through llm-gateway (LiteLLM's OpenTelemetry GenAI metrics). Per-tenant cost lives in Langfuse.",
    [
        panel(1, "Calls / min", "stat", (0, 0, 6, 4), [target(f"sum(rate(gen_ai_client_operation_duration_count{MODEL}[5m])) * 60")], decimals=1),
        panel(2, "Spend in range (USD)", "stat", (6, 0, 6, 4), [target(f"sum(increase(gen_ai_usage_cost_sum{MODEL}[$__range]))")], unit="currencyUSD", decimals=4),
        panel(3, "Tokens in range", "stat", (12, 0, 6, 4), [target(f"sum(increase(gen_ai_client_token_usage_sum{MODEL}[$__range]))")], decimals=0),
        panel(
            4,
            "p95 latency",
            "stat",
            (18, 0, 6, 4),
            [target(f"histogram_quantile(0.95, sum by (le) (rate(gen_ai_client_operation_duration_bucket{MODEL}[5m])))")],
            unit="s",
            thresholds=[("green", None), ("orange", 10), ("red", 20)],
        ),
        panel(
            5,
            "Latency by model (p50 / p95)",
            "timeseries",
            (0, 4, 12, 8),
            [
                target(f"histogram_quantile(0.50, sum by (le, gen_ai_request_model) (rate(gen_ai_client_operation_duration_bucket{MODEL}[5m])))", "p50 {{gen_ai_request_model}}"),
                target(f"histogram_quantile(0.95, sum by (le, gen_ai_request_model) (rate(gen_ai_client_operation_duration_bucket{MODEL}[5m])))", "p95 {{gen_ai_request_model}}", "B"),
            ],
            unit="s",
        ),
        panel(
            6,
            "Time to first token (p95, streaming)",
            "timeseries",
            (12, 4, 12, 8),
            [target(f"histogram_quantile(0.95, sum by (le, gen_ai_request_model) (rate(gen_ai_server_time_to_first_token_bucket{MODEL}[5m])))", "{{gen_ai_request_model}}")],
            unit="s",
        ),
        panel(
            7,
            "Tokens / min by type",
            "timeseries",
            (0, 12, 12, 8),
            [target(f"sum by (gen_ai_token_type) (rate(gen_ai_client_token_usage_sum{MODEL}[5m])) * 60", "{{gen_ai_token_type}}")],
        ),
        panel(
            8,
            "Spend rate by model (USD / hour)",
            "timeseries",
            (12, 12, 12, 8),
            [target(f"sum by (gen_ai_request_model) (rate(gen_ai_usage_cost_sum{MODEL}[5m])) * 3600", "{{gen_ai_request_model}}")],
            unit="currencyUSD",
        ),
        panel(
            9,
            "Agent requests / min (AG-UI runs)",
            "timeseries",
            (0, 20, 24, 7),
            [target('sum by (service) (rate(http_requests_total{handler=~"/agui/.*"}[5m])) * 60', "{{service}}")],
            description="Chat runs reaching the agents — every one becomes one or more model calls above.",
        ),
    ],
    [query_var("model", "Model", "label_values(gen_ai_client_operation_duration_count, gen_ai_request_model)")],
)

GPU_SEL = 'gpu=~"$gpu"'
# NVML has no utilization reading on WSL2 and some consumer cards; memory, temperature and power still come.
NO_UTILIZATION = "Not reported by this GPU's driver (e.g. WSL2)"
GPU = dashboard(
    "ai-circus-gpu",
    "AI Circus · GPU",
    "DCGM exporter metrics where it runs; otherwise the same names from data-platform-manager's NVML fallback (source=nvml).",
    [
        panel(
            1,
            "GPU telemetry",
            "stat",
            (0, 0, 6, 4),
            [target('max(ai_circus_gpu_telemetry_available) or max(up{service="dcgm-exporter"})')],
            description="1 when a GPU is visible to the telemetry source; 0 on a CPU-only cluster.",
            thresholds=[("red", None), ("green", 1)],
            extra_options={"colorMode": "background"},
        ),
        panel(
            2,
            "Utilization",
            "gauge",
            (6, 0, 6, 4),
            [target(f"max by (gpu) (DCGM_FI_DEV_GPU_UTIL{{{GPU_SEL}}})", "GPU {{gpu}}")],
            unit="percent",
            thresholds=[("green", None), ("orange", 80), ("red", 95)],
            no_value=NO_UTILIZATION,
        ),
        panel(
            3,
            "Memory used",
            "gauge",
            (12, 0, 6, 4),
            [target(f"max by (gpu) (DCGM_FI_DEV_FB_USED{{{GPU_SEL}}} / (DCGM_FI_DEV_FB_USED{{{GPU_SEL}}} + DCGM_FI_DEV_FB_FREE{{{GPU_SEL}}})) * 100", "GPU {{gpu}}")],
            unit="percent",
            thresholds=[("green", None), ("orange", 85), ("red", 95)],
        ),
        panel(4, "Temperature", "gauge", (18, 0, 6, 4), [target(f"max by (gpu) (DCGM_FI_DEV_GPU_TEMP{{{GPU_SEL}}})", "GPU {{gpu}}")], unit="celsius", thresholds=[("green", None), ("orange", 75), ("red", 85)]),
        panel(
            5,
            "Utilization over time",
            "timeseries",
            (0, 4, 12, 8),
            [target(f"max by (gpu, modelName) (DCGM_FI_DEV_GPU_UTIL{{{GPU_SEL}}})", "GPU {{gpu}} {{modelName}}")],
            unit="percent",
            no_value=NO_UTILIZATION,
        ),
        panel(
            6,
            "Memory used (MiB)",
            "timeseries",
            (12, 4, 12, 8),
            [
                target(f"max by (gpu) (DCGM_FI_DEV_FB_USED{{{GPU_SEL}}})", "used GPU {{gpu}}"),
                target(f"max by (gpu) (DCGM_FI_DEV_FB_USED{{{GPU_SEL}}} + DCGM_FI_DEV_FB_FREE{{{GPU_SEL}}})", "total GPU {{gpu}}", "B"),
            ],
            unit="mbytes",
        ),
        panel(7, "Power draw", "timeseries", (0, 12, 12, 8), [target(f"max by (gpu) (DCGM_FI_DEV_POWER_USAGE{{{GPU_SEL}}})", "GPU {{gpu}}")], unit="watt"),
        panel(8, "Temperature over time", "timeseries", (12, 12, 12, 8), [target(f"max by (gpu) (DCGM_FI_DEV_GPU_TEMP{{{GPU_SEL}}})", "GPU {{gpu}}")], unit="celsius"),
    ],
    [query_var("gpu", "GPU", "label_values(DCGM_FI_DEV_GPU_TEMP, gpu)")],  # TEMP: present wherever a GPU is
)

SCENARIO = 'scenario=~"$scenario"'
PSI_STEPS = [("green", None), ("orange", 0.1), ("red", 0.25)]
DRIFT = dashboard(
    "ai-circus-model-drift",
    "AI Circus · Model drift",
    "Population Stability Index of live scoring traffic against each model's training data "
    "(< 0.1 stable, 0.1–0.25 moderate, > 0.25 significant). Needs ≥ 100 scored rows per window.",
    [
        panel(
            1,
            "Score drift (PSI) by scenario",
            "bargauge",
            (0, 0, 12, 9),
            [target(f"max by (scenario) (ai_circus_model_score_psi{{{SCENARIO}}})", "{{scenario}}", instant=True)],
            thresholds=PSI_STEPS,
            decimals=3,
            extra_options={"displayMode": "lcd", "orientation": "horizontal", "colorMode": None},
        ),
        panel(
            2,
            "Most-drifted features",
            "bargauge",
            (12, 0, 12, 9),
            [target(f"topk(10, max by (scenario, feature) (ai_circus_model_feature_psi{{{SCENARIO}}}))", "{{scenario}} · {{feature}}", instant=True)],
            thresholds=PSI_STEPS,
            decimals=3,
            extra_options={"displayMode": "lcd", "orientation": "horizontal", "colorMode": None},
        ),
        panel(
            3,
            "Score PSI over time",
            "timeseries",
            (0, 9, 12, 8),
            [target(f"max by (scenario) (ai_circus_model_score_psi{{{SCENARIO}}})", "{{scenario}}")],
            thresholds=PSI_STEPS,
            decimals=3,
        ),
        panel(
            4,
            "Feature PSI over time",
            "timeseries",
            (12, 9, 12, 8),
            [target(f"max by (scenario, feature) (ai_circus_model_feature_psi{{{SCENARIO}}})", "{{scenario}} · {{feature}}")],
            thresholds=PSI_STEPS,
            decimals=3,
        ),
        panel(
            5,
            "Scored rows / min",
            "timeseries",
            (0, 17, 12, 7),
            [target(f"sum by (scenario, model_variant) (rate(ai_circus_scored_rows_total{{{SCENARIO}}}[5m])) * 60", "{{scenario}} ({{model_variant}})")],
        ),
        panel(
            6,
            "Rows in the current drift window",
            "timeseries",
            (12, 17, 12, 7),
            [target(f"max by (scenario) (ai_circus_model_drift_window_rows{{{SCENARIO}}})", "{{scenario}}")],
            description="Windows hold up to 2,000 rows and then start afresh; PSI is reported from 100.",
        ),
    ],
    [query_var("scenario", "Scenario", "label_values(ai_circus_model_drift_window_rows, scenario)")],
)

# MCP servers built with mcp-kit (docs/adr/0022, 0023): their own /metrics (mcp_kit_*), the API behind them, and the
# kit's audit lines in Loki (each linked to its trace). Labels are servers, tools, outcomes and reasons only.
MCP = 'server=~"$server"'
LOG_QUERY = '{service_name=~"$server"} | logger="mcp_kit.audit"'
LOGS = {"type": "loki", "uid": "${logs}"}
MCP_BOARD = dashboard(
    "ai-circus-mcp",
    "AI Circus · MCP servers — calls, refusals, per-API tokens",
    "MCP servers built with mcp-kit (docs/adr/0022, 0023): tool calls, policy refusals, per-API token exchanges, "
    "the API behind each server, and the audit lines.",
    [
        panel(1, "Tool calls in range", "stat", (0, 0, 6, 4),
              [target(f"sum(increase(mcp_kit_tool_calls_total{{{MCP}}}[$__range]))")], decimals=0, no_value="0"),
        panel(2, "Calls outside the grant", "stat", (6, 0, 6, 4),
              [target(f'sum(increase(mcp_kit_tool_calls_total{{{MCP},outcome="denied"}}[$__range]))')],
              decimals=0, no_value="0", thresholds=[("green", None), ("orange", 1)],
              description="Tools a caller asked for but the policy does not give it."),
        panel(3, "Refused before MCP (401 + 403)", "stat", (12, 0, 6, 4),
              [target(f"sum(increase(mcp_kit_access_refused_total{{{MCP}}}[$__range]))")],
              decimals=0, no_value="0", thresholds=[("green", None), ("orange", 1)],
              description="Missing, forged, expired or foreign tokens (401) and callers with no granted tool (403)."),
        panel(4, "Per-API tokens refused", "stat", (18, 0, 6, 4),
              [target(f'sum(increase(mcp_kit_token_exchanges_total{{{MCP},outcome="refused"}}[$__range]))')],
              decimals=0, no_value="0", thresholds=[("green", None), ("red", 1)],
              description="The identity provider refused a per-API token: misconfiguration, or a server asking beyond its API."),
        panel(5, "Tool calls per second by tool and outcome", "timeseries", (0, 4, 12, 8),
              [target(f"sum by (server, tool, outcome) (rate(mcp_kit_tool_calls_total{{{MCP}}}[5m]))",
                      "{{server}} {{tool}} {{outcome}}")], unit="reqps"),
        panel(6, "Refused before MCP, by reason", "timeseries", (12, 4, 12, 8),
              [target(f"sum by (server, reason) (rate(mcp_kit_access_refused_total{{{MCP}}}[5m]))", "{{server}} {{reason}}")],
              unit="reqps", description="A sudden rise means a misconfigured client or someone probing."),
        panel(7, "Tool call latency (p50 / p95), API call included", "timeseries", (0, 12, 12, 8),
              [target(f"histogram_quantile(0.50, sum by (le, tool) (rate(mcp_kit_tool_call_duration_seconds_bucket{{{MCP}}}[5m])))", "p50 {{tool}}"),
               target(f"histogram_quantile(0.95, sum by (le, tool) (rate(mcp_kit_tool_call_duration_seconds_bucket{{{MCP}}}[5m])))", "p95 {{tool}}", "B")],
              unit="s"),
        panel(8, "Per-API tokens: issued, cached, refused", "timeseries", (12, 12, 12, 8),
              [target(f"sum by (outcome) (rate(mcp_kit_token_exchanges_total{{{MCP}}}[5m]))", "{{outcome}}")],
              unit="reqps", description="Cached: the server reused the per-API token it already held for that user token."),
        panel(9, "The API behind each server, by status class", "timeseries", (0, 20, 12, 8),
              [target(f"sum by (server, status_class) (rate(mcp_kit_api_responses_total{{{MCP}}}[5m]))", "{{server}} {{status_class}}")],
              unit="reqps", description="4xx is the API's own refusals: another store, another user named in the request."),
        panel(10, "Token exchange round trip (p95)", "timeseries", (12, 20, 12, 8),
              [target(f"histogram_quantile(0.95, sum by (le, server) (rate(mcp_kit_token_exchange_duration_seconds_bucket{{{MCP}}}[5m])))", "{{server}}")],
              unit="s"),
        {
            "id": 11,
            "type": "logs",
            "title": "Audit lines: who, which tool, which outcome (never arguments, results or tokens)",
            "description": "One line per decision of the kit. Open a line to jump to its trace.",
            "datasource": LOGS,
            "gridPos": {"x": 0, "y": 28, "w": 24, "h": 10},
            "options": {"showTime": True, "wrapLogMessage": True, "enableLogDetails": True, "sortOrder": "Descending"},
            "targets": [{"datasource": LOGS, "expr": LOG_QUERY, "refId": "A", "queryType": "range"}],
        },
    ],
    [
        # ".+" for All: Loki refuses a selector that can match an empty value.
        {**query_var("server", "MCP server", "label_values(mcp_kit_tool_calls_total, server)"), "allValue": ".+"},
        {"name": "logs", "label": "Logs", "type": "datasource", "query": "loki", "current": {}, "hide": 0},
    ],
)

if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    for name, board in (("llm-gateway", GATEWAY), ("gpu", GPU), ("model-drift", DRIFT), ("mcp", MCP_BOARD)):
        path = OUT / f"{name}.json"
        path.write_text(json.dumps(board, indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {path}")
