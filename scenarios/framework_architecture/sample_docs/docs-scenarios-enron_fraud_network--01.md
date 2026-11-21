# 🕸️ Enron: Follow the Network — link analysis for financial-crime investigation

Source: docs/scenarios/enron_fraud_network.md (this repository)

> Domain: **Public sector** · Kind: `tabular_ml` (binary classification + a network) · Scenario: [`scenarios/enron_fraud_network/scenario.yaml`](../../scenarios/enron_fraud_network/scenario.yaml)

Fraud is rarely one person. Investigators at securities regulators, supervisors and prosecutors'
forensic teams work with *link analysis*: who is paid how much, who talks to whom, which
partnerships sit behind the numbers. This scenario puts that workflow on the real public record
of the Enron collapse (2001) — **143 insiders' pay** from the bankruptcy court's schedule,
**250,000 e-mails** from the FERC corpus turned into a company-wide network, and the
**off-balance-sheet partnerships** documented by the Board's own investigation (the Powers
Report) — and asks a model which profiles resemble those of the **18 persons of interest**, and
why.

<p align="center">
  <img src="../screenshots/enron_fraud_network/network-overview.webp" alt="The Network tab: a dark investigation board with a luminous web of e-mail ties between Enron insiders, nodes coloured by risk tier on a red ramp and sized by pay; violet hexagons for JEDI, Chewco, LJM1, LJM2, the four Raptors, Southampton Place, Arthur Andersen and the travel agency, with dashed relation lines; people with no two-way e-mail tie sit on an outer orbit; a case board lists the highest scores" width="900">
</p>
<p align="center"><sub>Every glowing node is a real insider, scored by a model that never saw their own label; hexagons are the partnerships and gatekeepers of the Powers Report.</sub></p>

| | |
|---|---|
| **Question** | From what an insider was paid and where they sat in the company's e-mail network, how closely does their profile resemble the persons of interest'? |
| **Data** | The ud120 insider-pay dataset (court record *In re Enron Corp.*), the FERC/CMU e-mail corpus (headers only) and the Powers Report (SEC EDGAR) — see [the data README](../../scenarios/enron_fraud_network/sample_data/README.md) |
| **Label** | 18 persons of interest: indicted, settled with the SEC or testified for immunity (USA Today, 2005) — **not** a list of convictions |
| **Inputs** | 14 pay and stock fields, 3 pay ratios, 8 label-free network features (contacts, PageRank and betweenness percentiles, clustering, crisis-period and after-hours shares) |
| **Model** | LightGBM tuned for small tables (`lightgbm_small_data`), selected over logistic regression |
| **5-fold CV ROC AUC** | **0.83 ± 0.06** on the 114-person training split (logistic regression: 0.65) |
| **Hold-out (29 people, ~4 positives)** | ROC AUC **0.77** — a single hold-out this small swings a lot |
| **Out of fold, all 143** | ROC AUC **0.81**: every person scored by a model that never saw their own outcome |
| **Tabs** | Scenario · Data & BI · ML Predictions · ML Insights · **Network** |
| **New platform capabilities** | A network per tabular scenario (`dataset.graph`, `GET /graph/{slug}`), the generic `network_explorer` tab, and out-of-fold scores (`model.out_of_fold_scores`, `GET /model/{slug}/out-of-fold`) |
