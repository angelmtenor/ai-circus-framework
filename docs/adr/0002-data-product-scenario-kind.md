# 0002 — `data_product` is a fifth scenario kind

**Context.** The proposal left open whether a data product is a kind or a `data:` block any
scenario may attach.

**Decision.** A kind, `data_product`, validated by `ai_circus_shared.scenario_schema`
(`DataProductConfig`) and seeded by platform-registry like every other kind. It is served by
generic services that resolve every `data_product` scenario (`resolve_scenarios(kind=…)`):
`data-ingest`, `data-transform`, `data-api`; its chat is the existing `assistant` with data tools.
ui-react renders it with one generic `DataProductView`. Other kinds consume a data product's
gold marts through `data-api` or as a dataset source, never by reading its tables directly.

**Consequences.** Matches the repo's "one service instance per kind, routed by
`{scenario_slug}`" rule and its "adding a scenario is YAML + files" promise. A data product is
listed in the picker even when the data tier is down; `data-api` being unreachable surfaces as a
clear "data tier not running" state in the view, not a blank page.
