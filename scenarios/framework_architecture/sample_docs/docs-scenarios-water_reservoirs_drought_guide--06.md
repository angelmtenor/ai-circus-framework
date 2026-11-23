# Reservoirs & Drought Watch: what is built, how it works, what is not - Simplifications, and why

Source: docs/scenarios/water_reservoirs_drought_guide.md (this repository)

- **Scenarios use the model's own quantiles.** A direct model cannot take future rain as a feature, so "dry/normal/wet"
  moves the forecast distribution by the p10 or p90 distance from the median. The dry stress is conservative. A milder
  definition is a one-function change.
- **Savings come from the rulebook's illustrative demand split**, not estimated from historical dry-period rates.
- **Rulebook search is lexical and in-process**, not a vector search: the plan has about 40 paragraphs.
- **The fictional tenant is content, not a Keycloak organisation:** the scenario is seeded for the `admin` tenant like
  every scenario; a Riverbend organisation with its own entitlement is a deployment step, not code.
- **Other languages** are answered by the assistant (it translates); the plan and bulletin exist in English and Spanish.
- **The map is schematic:** a plain x/y grid of a fictional basin, drawn as SVG, not a projected map of a real place.
