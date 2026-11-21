# 🤝 Emergency Social Aid — rules, a model and a person - How it is built

Source: docs/scenarios/prestaciones_sociales.md (this repository)

| Piece | Where |
|---|---|
| Rules + policy, pure and tested | [`libs/shared/src/ai_circus_shared/business_rules.py`](../../libs/shared/src/ai_circus_shared/business_rules.py); schema in `scenario_schema.py` (`BusinessRules`, `DecisionPolicy`, `CaseDeskExtra`) |
| Rules before `dropna()`, blocked rows kept | `etl-tabular` `core/etl.py` · training drops them (`rules_excluded_rows`) |
| `rules` / `gate` / `decision` on every prediction, per-row gates in the dataset sample | `prediction` `api.py` |
| Scanned intake | `assistant` `core/extraction.py` (`POST /extract-record/{slug}`, `GET /intake-samples/{slug}/{file}`) |
| The tab | `ui-react` `CaseDeskView.tsx`, `CaseDeskCircuit.tsx`, `CaseSheet.tsx`, `caseDeskLogic.ts` |
| Corpus + scans | `scripts/generate_prestaciones_sociales_dataset.py`, `scripts/generate_prestaciones_sample_documents.py` |
| Regulations | `scenarios/prestaciones_sociales/sample_docs/` — real texts by `scripts/prepare_prestaciones_normativa.py` (BOE open-data API + Cellar, SHA-pinned), fictional ones hand-written |
| Document tool, scope, reading list | `assistant` `core/tools.py` (`build_document_tool`), `core/chat.py`, `api.py` (`_chat_scope`, `GET /documents/{slug}`); shared retrieval in `ai_circus_shared/retrieval.py` |
| Provenance chips, reading room | `ui-react` `chatProvenance.ts`, `ChatPanel.tsx`, `NormativaDesk.tsx` |
