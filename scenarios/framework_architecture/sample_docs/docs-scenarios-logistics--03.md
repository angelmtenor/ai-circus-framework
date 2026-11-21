# Logistics: Dispatch Tower & Lifeline Globe - Global Health Shipments — the Lifeline Globe

Source: docs/scenarios/logistics.md (this repository)

<p align="center">
  <img src="../screenshots/logistics/globe-overview.webp" alt="A glowing globe centred on Africa and the Indian Ocean: arcs from manufacturing sites in India (Nashik, Hyderabad, Goa), Europe and the US to national programmes across Africa and to Haiti and Vietnam, red for the highest-risk routes and grey for routes on track, with small planes and trucks moving along them; a monthly timeline of shipments stacked by risk tier below, and a dark split-flap departures board listing the riskiest shipments with their route and risk" width="900">
</p>

| | |
|---|---|
| **Question** | Which planned shipments of HIV/AIDS medicines and test kits will reach the country late — and what change would get them there on time? |
| **Model** | LightGBM classifier chosen over logistic regression on 5-fold CV ROC AUC (**0.904 ± 0.004**; logistic 0.80), hold-out 0.918 |
| **Data** | 9,949 real line items of USAID's PEPFAR Supply Chain Management System, 2006-2015, 42 countries — US public domain ([data README](../../scenarios/global_health_shipments/sample_data/README.md)) |
| **Label** | `delivered_late`: delivered to the client after the scheduled date (11.9%, median 12 days late) |

Every model input is known when the shipment is planned: destination, fulfilment route
(direct drop from the vendor vs. a regional distribution centre), shipment mode, the
goods (product group, adult/pediatric, dosage form, quantity, value, prices), how many
line items travel together, the **planned lead time** and the scheduled year and month.
Freight cost, insurance, weight and the delivery itself are not.

### What the tab does

- **Globe** — every shipment is an arc from its manufacturing site to the destination's
  capital (a map anchor, not the delivery address), lifted higher for longer routes and
  hidden correctly behind the globe; vehicles (plane, truck, ship) travel along them.
  *All years* shows one arc per route coloured by its mean risk; **Play** replays the
  programme month by month. Drag to turn it, click a destination to filter.
- **Departures board** — the window's shipments riskiest first on a split-flap board
  (the month header flips like an airport board).
- **Dossier** — pick a shipment: its SHAP drivers, then **Re-plan**: every one-change
  alternative (ship by air charter / truck / ocean, switch fulfilment route, plan 14 /
  30 / 60 days earlier) scored live, best move first.
- **Reveal what happened** — the backtest: which shipments really arrived late, and how
  many of them the flagged tiers caught.

<p align="center">
  <img src="../screenshots/logistics/globe-month.webp" alt="The replay stopped at November 2010: the globe turned to face the selected shipment's arc (cyan, Hyderabad to Côte d'Ivoire) with the month's other shipments in red and grey; the departures board header reads NOV 2010 and lists the month's shipments, riskiest first" width="900">
</p>

<p align="center">
  <img src="../screenshots/logistics/globe-dossier.webp" alt="A critical-tier shipment's dossier: its blind out-of-fold risk, the drivers pushing it up (planned lead time, fulfilment from a distribution centre, destination), and the re-plan panel where switching to a direct drop or planning 60 days earlier cuts the deployed model's risk the most" width="900">
</p>

### Honest scores

The globe reveals every shipment's real outcome next to its score, so its scores are
**cross-fitted** (`model.out_of_fold_scores`): each shipment is scored by a copy of the
model that never saw it (out-of-fold ROC AUC 0.919). The refit deployed model's own,
in-sample scores would flag 89% of the late shipments at the *Expedite review* tier;
the honest number is:
