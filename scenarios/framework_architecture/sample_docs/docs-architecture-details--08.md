# Architecture details - Business rules, decision policy, case desk

Source: docs/architecture-details.md (this repository)

A tabular scenario whose rows are *applications* (`prestaciones_sociales`) may gate each one through
**deterministic business rules before the model** (`dataset.business_rules`, `rule_columns`,
`ai_circus_shared.business_rules`: families of rules `missing/equals/in/lt/gt/contains_any…` with
outcomes `reject`/`request_info` (blocking) or `review`) and turn the binary model's probability into
a proposal with `model.decision_policy` (approve ≥ `approve_at`, deny ≤ `deny_at`, else manual review):
etl-tabular evaluates the rules before `dropna()` and keeps blocked rows for analytics, training never
fits on them (`rules_excluded_rows`), prediction's `/predict` returns `rules`/`gate`/`decision` (the
rules are server-side only — the thresholds are mirrored client-side because they are sliders).
`text_language: es` swaps TF-IDF's stop words (negations kept) and the challenger embeds every text
column. `ui_extras: case_desk` («Mesa de valoración», `CaseDeskView.tsx`): the rules → model → trays
circuit, an official paper sheet with per-box SHAP heat, and scanned-document intake — the browser OCRs
the file (platform-registry `/documents/extract`), assistant's `POST /extract-record/{slug}` maps the
text onto the boxes with strictly validated, quote-verified JSON, and `GET /intake-samples/{slug}/{file}`
serves the listed sample scans. Audit-only columns (`audit_columns`, e.g. sex, nationality) are kept out
of the model and only feed the equity panel.
