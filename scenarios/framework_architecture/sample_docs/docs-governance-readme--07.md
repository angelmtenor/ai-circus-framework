# Governance-ready by standards - Known limits

Source: docs/governance/README.md (this repository)

- **Admin identity.** The admin API is a shared bearer key, so the audit trail records the credential class
  (`admin-api-key`), not a named person.
- **Audit anchoring.** The hash chain proves rows were not altered by someone who cannot recompute it; keep the
  `audit_hash` carried in events outside the system to detect a rewritten chain or a truncated tail.
- **Ungoverned calls.** A model call with no tenant or AI system attached (internal admin tests) is not
  subject to policy. Speech vendors used by agui-voice are outside the model chokepoint.
- **Trace retention** is Langfuse's to configure; **event retention** is the sink's.
- **Not covered yet:** per-AI-system tool scoping and agent step limits (the `obligations` object can grow
  `allowed_tools` / `max_steps`), and the SPDX 3.0 AI profile as a second inventory format.
