# Governance-ready by standards - Declaring an AI system

Source: docs/governance/README.md (this repository)

Add an optional `governance:` block to a `scenario.yaml` ([schema](schemas/ai-system-governance.json); three
scenarios carry illustrative examples, marked "owner to confirm"). Without it a scenario still gets its slug as
`ai_system_id`. Risk stays `unassessed` until a person sets it.
