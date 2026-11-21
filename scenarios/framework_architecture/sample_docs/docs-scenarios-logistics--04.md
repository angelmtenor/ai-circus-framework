# Logistics: Dispatch Tower & Lifeline Globe - Global Health Shipments — the Lifeline Globe

Source: docs/scenarios/logistics.md (this repository)

| Flagged (*Expedite review* or above, P ≥ 0.35) | Late shipments caught | Precision | Lift over the 11.9% base rate |
|---|---|---|---|
| 1,119 of 9,949 (11.2%) | 694 of 1,182 (**59%**) | **62%** | 5.2× |

At this size the out-of-fold artifact carries probabilities only (per-row SHAP stops at
5,000 rows — `tabular_ml.MAX_OUT_OF_FOLD_EXPLAINED_ROWS`); the dossier explains and
re-plans with the deployed model, and says which number is which.

### Caveats

- The model describes this programme's history: performance shifted strongly by year
  (2010-2011 peak), so the scheduled year is an input.
- The scheduled date is the one on record at delivery — if some were revised after a
  delay, a long planned lead time partly reflects that revision.
- A re-plan changes one input and keeps the rest: the model's what-if, not a guarantee
  (e.g. a faster mode rarely helps when the delay is upstream at the vendor).

<p align="center">
  <img src="../screenshots/logistics/light-globe-overview.webp" alt="The same globe tab in the light white-green theme: a pale ocean with white land, red risk arcs and grey routes on track; the departures board stays a dark physical-looking object" width="900">
</p>
