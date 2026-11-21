# 💸 Follow the Money — transaction monitoring for a bank consortium - The Money Trail tab

Source: docs/scenarios/aml_money_trail.md (this repository)

Two views of the same scored network, switched at the top of the tab: the **World map** (below) and the
[**Network graph**](#the-network-graph-view) — the same force-directed link-analysis board as the
[Enron case](enron_fraud_network.md).

### World map

A world map with each country's holders clustered around its member bank (a schematic — the clusters
are nudged apart so Europe stays legible).

| On the map | Meaning |
|---|---|
| **Circles / diamonds** | Holders: a circle is a *person* (individual or sole proprietorship), a diamond a *company* (corporation or partnership). Colour = risk tier on a validated one-hue red ramp (Background → Elevated → Review → Priority), scored by a model that never saw the holder's own label; size = USD sent |
| **Violet squares** | The 13 member banks |
| **Hollow grey rings** | Holders *outside* the consortium — counterparties the utility sees only because a payment touches a member bank |
| **Particles** | While playing, one particle per payment hopping from payer to payee, hour by hour over 1–10 September 2022. **Its shape is the payment format** — ● ACH, ◆ wire, ■ cheque, ▲ card, ✚ cash, ⬢ Bitcoin — so nothing rests on colour alone |
| **Arcs** | Paused, the hour's payments as static arcs; on "All ten days", the biggest flows overall |

<p align="center">
  <img src="../screenshots/aml_money_trail/money-trail-replay.webp" alt="Replay in progress: dozens of small white marks — circles, diamonds, squares, triangles, crosses — hopping along arcs between banks on the world map" width="900">
</p>
<p align="center"><sub>Replaying hour by hour: every payment is a mark hopping payer → payee; its shape is the payment format.</sub></p>

- **Ledger** — the hour's biggest payments (payer → payee, format, USD, currency); click a name to follow its money.
- **Watchlist** — the riskiest holders on the map; click one to open its dossier.
- **Dossier** — every payment it sent (orange, arrowed) and received (blue) is drawn on the map; SHAP
  rolled into six pillars (profile, volume and value, layering, fan-in and fan-out, channels and
  reach, network position), the biggest single features, its percentile on 11 features among all
  12,253 holders, its counterparties, and an hour-by-hour strip of dollars sent and received.
<p align="center">
  <img src="../screenshots/aml_money_trail/money-trail-follow.webp" alt="One holder selected: orange arrowed arcs for the payments it sent and blue ones for those it received, fanning out from Switzerland to counterparties around the world" width="900">
</p>
<p align="center"><sub>Following one holder: orange arcs are payments it sent, blue ones payments it received.</sub></p>

<p align="center">
  <img src="../screenshots/aml_money_trail/money-trail-dossier.webp" alt="The dossier of a Priority holder: pillar bars for the SHAP explanation, the biggest single features, percentile bars against all holders, counterparties with payment-format marks and an hour-by-hour strip" width="900">
</p>

- **Reveal what happened** — outlines every holder that was really involved (solid = the model flagged
  it at *Review* or above, dashed = missed), and opens the **typology gallery**: the eight schemes the
  generator planted (fan-out, fan-in, cycle, bipartite, scatter-gather, gather-scatter, stack,
  random) with how many holders each has and how many the model flags. Click a scheme to light it
  up on the map; select a holder and its whole scheme is outlined in gold.
