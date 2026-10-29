# 0009 — Apache Superset as BI, dashboards as code

**Context.** BI options: Superset (Apache-2.0), Metabase (AGPL), Grafana (AGPL), Evidence,
Lightdash. Dashboards must be versioned with the scenario.

**Decision.** Superset in the bi tier, metadata in Postgres (`superset` database), cache in
Valkey. Dashboards are declared in `scenarios/<slug>/bi/dashboards.yaml` (datasets, charts,
layout) and created through Superset's REST API by an idempotent bootstrap Job, rather than
hand-exported ZIPs. The ui-react view also has native live tiles, so the core tier demos without
Superset.

**Consequences.** Apache-2.0 end to end. The YAML is small and reviewable; Superset's own export
remains available for tweaks made in its UI.
