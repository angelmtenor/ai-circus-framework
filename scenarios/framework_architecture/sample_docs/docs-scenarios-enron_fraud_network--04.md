# 🕸️ Enron: Follow the Network — link analysis for financial-crime investigation - The Network tab

Source: docs/scenarios/enron_fraud_network.md (this repository)

Finally, **reveal the public record**: rings pop onto the persons of interest, highest score
first — solid when the model flagged them at *Review* or above, dashed when it missed them —
with the counts, the precision and the lift over the base rate.

<p align="center">
  <img src="../screenshots/enron_fraud_network/network-reveal.webp" alt="The public record revealed: tier tiles with the number of persons of interest in each, a backtest summary (5 of 18 at Review or above, 13 missed, 8 false positives, precision 38% vs a 13% base rate, 3.1× lift), and rings on the board" width="900">
</p>

### Investigate a person

The **Investigate a person** switch above the board opens a person-centred workspace (*Investigate ›* in
any dossier jumps straight there). The chosen person sits at the centre of a radial **ego network**:
their strongest two-way contacts around them (stronger tie = closer), documented relations as dashed
hexagon links, and mail flowing along every tie as particles — cyan sent, white received. Beyond the
ring, the **routes to the nearest persons of interest** (or, with the toggle, to the model-flagged
tier) fan out; click a route to light it up. The side panel gives the out-of-fold score and SHAP
pillars, an *exposure* read-out (direct ties, share of their e-mail exchanged with persons of
interest and its peer percentile, hops to the nearest, how many within two hops), and per-tie
monthly bars. The timeline replays *this person's* mail month by month, the red part being the
share exchanged with persons of interest. Click a contact to inspect the tie, click again to
re-centre (*‹ Back* retraces).

**＋ New individual** invents someone who is not in the data: set their pay and e-mail profile,
pick who they e-mail (and how much), and the deployed model scores them live while the stage draws
their routes to the persons of interest. The features that *are* ties — contact count, e-mail volume,
clustering (exact, from the contacts' own ties) and a rough centrality (volume-weighted mean of the
contacts') — are derived for you and can be overridden; the scenario declares them with
`ui_extras.tie_features`. *What if… ›* clones a real person's profile and ties as the starting
point. Nothing is stored. As everywhere here, proximity to a person of interest is not evidence of
anything — colleagues e-mail each other.

Light theme:

<p align="center">
  <img src="../screenshots/enron_fraud_network/light-network-overview.webp" alt="The same board in the light theme" width="900">
</p>
