# 💸 Follow the Money — transaction monitoring for a bank consortium

Source: docs/scenarios/aml_money_trail.md (this repository)

> Domain: **Banking & finance** · Kind: `tabular_ml` (binary classification + a network) · Scenario: [`scenarios/aml_money_trail/scenario.yaml`](../../scenarios/aml_money_trail/scenario.yaml)

Money laundering hides in the *shape* of payments: a burst of payees, a stack of pass-through
accounts, money that comes back where it started. A single bank sees only its own side of the
picture. This scenario plays the role of an **inter-bank monitoring utility** shared by 13 member
banks: from ten days of payments between **people and companies** it asks a model which account
holders behave like the ones a laundering scheme runs through — and why — and draws the money
hopping between banks on a world map.

It is deliberately **not** the [Enron case](enron_fraud_network.md): there, a few real insiders
and a corporate scandal; here, thousands of simulated holders, payment flows across 33 countries
and a labelled world in which the laundering schemes are known.

<p align="center">
  <img src="../screenshots/aml_money_trail/money-trail-overview.webp" alt="The Money Trail tab: a dark world map with each country's account holders clustered around a violet member bank, coloured on a red ramp by risk tier, joined by luminous arcs for the ten days' biggest payments; a ledger and a watchlist on the right and an hourly timeline under the map" width="900">
</p>
<p align="center"><sub>Every glowing mark is an account holder — a circle for a person, a diamond for a company — scored by a model that never saw its own label; arcs are the ten days' biggest payments.</sub></p>

| | |
|---|---|
| **Question** | From how an account holder moved money over ten days — how much, how fast, to and from how many parties, through which formats and countries — how closely does it resemble the holders laundering ran through? |
| **Data** | IBM **AMLworld HI-Small** — a *synthetic* world of individuals, companies and banks with every transfer labelled (Altman et al., NeurIPS 2023, CDLA-Sharing-1.0). See [the data README](../../scenarios/aml_money_trail/sample_data/README.md) |
| **Rows** | 12,253 holders with an account at one of 13 member banks (4,101 sole proprietorships, 4,189 partnerships, 3,941 corporations, 22 individuals) |
| **Label** | Involved in ≥ 1 laundering transfer the consortium can see — **448 holders (3.7 %)**; involvement, not guilt (receivers can be unwitting mules) |
| **Inputs** | 28 behaviour features: holder type, accounts/banks/countries, transfers and USD in/out, pass-through ratio, dwell time, payees/payers, peak daily fan-in/fan-out, reciprocity, payment-format mix, cross-border share, night share, PageRank, 3-hop return cycles |
| **Model** | LightGBM, selected over logistic regression by cross-validated ROC AUC |
| **5-fold CV ROC AUC** | **0.83** on the training split (hold-out 0.82); **0.84 ± 0.015** over repeated CV of all rows, ≈ 0.81 without the payment-format mix; PR AUC ≈ 0.31 (≈ 8× the 3.7 % base rate) |
| **Out of fold, all 12,253** | ROC AUC **0.841**: every holder scored by a model that never saw its own outcome. *Review* tier and above (≥ 10 %): 462 holders, 34 % of them involved, 35 % of all positives; *Priority* (≥ 30 %): 167 holders, 54 % involved |
| **Tabs** | Scenario · Data & BI · ML Predictions · ML Insights · **Money Trail** (a **World map** and a **Network graph** view of the same scored network) |
| **New platform capabilities** | The generic `money_trail` tab, **hourly periods** (`YYYY-MM-DDTHH`) in a scenario's network, and the Enron network explorer reused as its second view |
