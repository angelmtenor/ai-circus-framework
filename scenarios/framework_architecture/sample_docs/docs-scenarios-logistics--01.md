# Logistics: Dispatch Tower & Lifeline Globe

Source: docs/scenarios/logistics.md (this repository)

> Domain: **Logistics** · two `tabular_ml` scenarios, each with a showpiece 5th tab:
> [`supply_chain`](../../scenarios/supply_chain/scenario.yaml) (regression → **Dispatch Tower**) and
> [`global_health_shipments`](../../scenarios/global_health_shipments/scenario.yaml) (classification → **Lifeline Globe**).

Both tabs are generic renderers driven by a `ui_extras` block in `scenario.yaml` — any
future ETA or shipment-risk scenario gets them with YAML only
(`ai_circus_shared.scenario_schema.DispatchTowerExtra` / `ShipmentGlobeExtra`).

---
