# Governance-ready by standards - Verifying it works

Source: docs/governance/README.md (this repository)

Each capability has unit tests and a documented live check; `make check-all` covers the lot. The contract
tests fail if a published schema drifts from its model, if a service bypasses llm-gateway, or if the telemetry
doc stops listing an attribute.
