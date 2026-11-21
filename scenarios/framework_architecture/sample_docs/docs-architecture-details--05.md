# Architecture details - Fleet health, row triage, case tracker, source library

Source: docs/architecture-details.md (this repository)

`ui_extras: fleet_health` (`FleetHealthView.tsx`, DOM and CSS only, no frame loop) replays real dataset rows unit by unit,
scoring each unit's current row at every tick; health is the model's score, or actual minus predicted when the target is
not itself a health measure (`health.value: residual`). Its thresholds should come from the real distribution.
`ui_extras: row_triage` (`RowTriageView.tsx`) routes rows by a threshold over the precomputed out-of-fold scores, so a tick
calls nothing. `ui_extras: message_screening` (`MessageScreeningView.tsx`, first user `financial_crime_desk`) is its reading-room twin
for rows that are *messages* with a free-text field: an inbox by out-of-fold score, the open message drawn field by field (each tinted by
its out-of-fold SHAP push), the words shaded live by the deployed model, the text challenger and, with a scenario `rubric_check`,
the LLM reading the message as text (never the score or outcome; `rubric_check.reviewer_role` replaces the leadership coach's
prompt opening). A scenario with `documents` and `extra_models` keeps the documents (and `vector_store`) on the main model. **A new tab must leave the page quiet**: prefer DOM/SVG over canvas, cap what it draws, and measure it idle.
An `assisted_form` may add `ui_extras: case_tracker` (`CaseTrackerView.tsx`; form-agent `GET /submissions/{slug}`, scoped by
tenant *and* user, reading each object because the owner lives inside it, hence the scan cap). A `conversational_rag` may add
`ui_extras: source_library` (`SourceLibraryView.tsx`; rag-agent `GET /documents/{slug}` and `/passages`, reading the tenant's
Qdrant collection, since rag-agent has no object store; etl-vectorize stores each chunk's `chunk` position, so a collection
indexed earlier shows passages unordered until re-indexed).
