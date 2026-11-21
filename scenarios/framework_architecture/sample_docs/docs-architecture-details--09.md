# Architecture details - A document search as an assistant tool

Source: docs/architecture-details.md (this repository)

A `tabular_ml` scenario may also give its assistant a **document search as one more tool**
(`documents` + `documents.tool` {name, label, description, sample_questions} + `vector_store`, e.g.
`prestaciones_sociales`' `consultar_normativa` over real BOE/EUR-Lex articles + fictional local rules,
fetched by `scripts/prepare_prestaciones_normativa.py`): etl-vectorize indexes it like a RAG scenario,
assistant builds the tool per request (`ai_circus_shared.retrieval` — the one `retrieve`/`format_retrieved`
rag-agent and form-agent share), serves `GET /documents/{slug}`, and honours AG-UI `forwardedProps.scope:
"documents"` by building **that tool alone**. ChatPanel labels every reply with the sources its tool
calls really used (`chatProvenance.ts`); case_desk adds a reading room (`NormativaDesk.tsx`, «📖 Ver norma»
on a fired rule's `legal_basis` — a libs/shared test checks each one resolves to a shipped document).
