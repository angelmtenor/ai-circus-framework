# 💸 Follow the Money — transaction monitoring for a bank consortium - The Money Trail tab

Source: docs/scenarios/aml_money_trail.md (this repository)

<p align="center">
  <img src="../screenshots/aml_money_trail/money-trail-typologies.webp" alt="Nine cards, one per laundering typology — Stack, Bipartite, Gather-scatter, Fan-in, Fan-out, Random, Scatter-gather, Cycle and Unpatterned — each with a small node-link motif, its holder count and the share the model flags" width="900">
</p>
<p align="center"><sub>After the reveal: the schemes the generator planted and how many holders of each the model flags (Review tier or above).</sub></p>

### The Network graph view

The Enron network explorer, fed with the payment network: every holder a glowing node (colour = risk tier,
size = USD sent), the 13 member banks as violet hexagons joined to the holders that hold accounts there,
holders outside the consortium as small grey dots, and a line per pair of holders that paid each other
(the six payment formats merged into one edge, its label listing the biggest formats). It keeps everything
the explorer offers — search, tracing the shortest path between two holders, colouring by **country** as
communities, the case board of the highest scores, the SHAP dossier by pillar, the **hour-by-hour replay**
of the ten days and a **ground-truth reveal** (solid rings = flagged, dashed = missed).

<p align="center">
  <img src="../screenshots/aml_money_trail/money-trail-graph.webp" alt="The Network graph view: banks as violet hexagons at the centre of clusters of holders coloured by risk tier on a red ramp, joined by payment lines; tier counters above, a case board with the highest scores on the right and an hourly USD timeline below" width="900">
</p>
<p align="center"><sub>The same scored network as a graph: each bank is the hub of its account holders; red halos are the holders the model scores highest.</sub></p>

The graph is a **case slice** (1,000 holders built around laundering attempts), so its tier counters and
backtest describe the holders drawn — 17 % of them are positive against 3.7 % overall. The World map's
backtest covers all 12,253 holders.
