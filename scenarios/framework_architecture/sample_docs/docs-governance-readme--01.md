# Governance-ready by standards

Source: docs/governance/README.md (this repository)

The framework is **governable by an external AI-governance tool without depending on any product**. It
exposes what a governance programme needs through open contracts — JSON Schema, CloudEvents, OpenTelemetry,
CycloneDX, OPA-compatible JSON — and enforces the decisions a tool sends back. Design rules and the reasoning:
[ADR 0011](../adr/0011-governance-ready-by-standards.md).

<p align="center"><img src="../screenshots/platform-governance-tab.webp" alt="Platform → Governance: switches, the audit trail with its chain verified, the AI-BOM and the declared AI systems" width="850"></p>

**The admin dashboard.** *Platform → Governance* shows the switches below, the AI systems with what their owners declared (risk
category, human oversight), the audit trail with a chain-verified badge and the latest changes, and an AI-BOM download. It
answers while every feature is off. API: `GET /governance/overview` on platform-registry (admin only, read-only, content-free).
The policy decision point and the ML decision records are configured in other services and are not on the dashboard yet.
