# Logistics: Dispatch Tower & Lifeline Globe - Supply Chain Shipping ETA — the Dispatch Tower

Source: docs/scenarios/logistics.md (this repository)

<p align="center">
  <img src="../screenshots/logistics/dispatch-stage.webp" alt="Left: a dark US map with the eight origin hubs glowing in cyan, each surrounded by short streaks — real shipments in flight, coloured by how many days they actually took; Atlanta is focused, with a dashed route to a pinned destination. Right: Atlanta's ETA landscape — blue isochrone bands of predicted delivery days around the hub, labelled 18, 20 and 22 days, the hub's real shipments dotted on top, and a tooltip reading 16.6 days with a 90% interval of 14.5 to 21.5 days" width="900">
</p>

| | |
|---|---|
| **Question** | How long will this shipment take — and which carrier and service level should we book, and what delivery date can we promise? |
| **Model** | LightGBM regressor (hold-out R² ≈ 0.66) + two LightGBM quantile models for a 90% prediction interval |
| **Data** | 10,000 synthetic shipments (AWS SageMaker supply-chain workshop): origin hub, east-west / north-south offset to the destination, carrier, priority, order size |

The tab has three linked views, all scored live by the scenario's own `/predict`:

1. **Network** — the eight hubs on a US map with the dataset's real shipments streaking
   out of them, coloured by their *actual* duration. The data only knows a destination
   as a grid offset from its hub, so destinations are drawn at that offset — a
   schematic, and the tab says so. Click a hub to focus it.
2. **ETA landscape** — a 33 × 33 grid of destinations around the focused hub
   (≈1,100 records, one batched call, ~140 ms) for the chosen carrier, priority and order
   size, drawn as filled isochrones (d3-contour) on a *fixed* colour scale, so switching
   hub or option never silently re-scales it. Hover reads the prediction and its
   interval; click pins a destination.
3. **The race** — every carrier × priority combination (16 lanes) to the pinned
   destination. Vehicles run to their predicted day with the 90% interval shaded behind
   them; the verdict cards name the **fastest option**, the **safest promise** (the
   lowest upper bound of the interval: about 19 in 20 similar shipments arrive by then)
   and **what choosing well is worth** in days.

<p align="center">
  <img src="../screenshots/logistics/dispatch-race.webp" alt="The race panel: verdict cards (the fastest option, the safest promise date, and how many days choosing well is worth) above sixteen lanes grouped by carrier, each with a vehicle glyph (bolt for Express, van for Standard, truck for Ground, plane for Air) at its predicted day, a shaded 90% interval band and its finishing rank" width="900">
</p>

---
