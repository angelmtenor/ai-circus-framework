# 0015 — Observability is a platform prerequisite; services honour a contract

Source: docs/adr/0015-observability-platform-contract.md (this repository)

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
