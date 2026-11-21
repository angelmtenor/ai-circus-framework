# Architecture guide - How a scenario becomes an app

Source: docs/architecture/README.md (this repository)

A scenario is one `scenarios/<slug>/scenario.yaml`, validated by `ai_circus_shared.scenario_schema`:

1. `platform-registry` seeds it at start-up and grants it to the admin tenant; other tenants get it through a
   Keycloak role.
2. The batch jobs read the same file as build-time config: `etl-tabular` the dataset, `training` the candidates.
3. The service of its kind serves it under its slug (`/predict/{slug}`, `/agui/{slug}`): no new code.
4. `ui-react` draws generic tabs from it (the feature schema becomes the inputs, `default_charts` the dashboard,
   `ui_extras` a showpiece tab, `story` the Overview and its guided demo).
5. `ai_circus_shared.architecture` derives from it which components the scenario uses and why, and the registry
   serves that with the scenario: its Overview shows the same map with its part lit.
