# 🕸️ Enron: Follow the Network — link analysis for financial-crime investigation - The data science

Source: docs/scenarios/enron_fraud_network.md (this repository)

- **Honest scores.** On 143 rows the deployed model is refit on everyone, so its own scores of
  those people are near-memorised (every person of interest near 1). The Network tab uses
  **out-of-fold** scores instead: training cross-fits the selected model (5 folds) and records
  every person's probability and SHAP contributions from the fold that never saw them —
  prediction serves them at `GET /model/{slug}/out-of-fold`.
- **De-leaked features.** The classic ud120 features `from_poi_to_this_person`,
  `from_this_person_to_poi` and `shared_receipt_with_poi` count e-mails with people *already
  known* to be persons of interest — they are computed from the label and were dropped. So was
  "is a corpus custodian" (FERC chose whose mailbox to collect).
- **Label-free network features** from networkx on the company-wide two-way e-mail graph (4,448
  people, 43,766 ties): contacts, PageRank and betweenness percentiles, clustering, share of
  e-mail in Aug–Dec 2001 and after hours.
- **Ablation** (5-fold CV repeated 10×, training's own pipeline):

| Inputs | Logistic regression | LightGBM (small data) |
| --- | --- | --- |
| Money only (pay + stock + ratios) | 0.739 ± 0.119 | 0.821 ± 0.091 |
| Network only (label-free) | 0.720 ± 0.087 | 0.732 ± 0.124 |
| **Money + network (deployed)** | 0.746 ± 0.118 | 0.818 ± 0.081 |
| Money + ud120's e-mail counts (leak the label) | 0.726 ± 0.120 | 0.803 ± 0.089 |
| Diagnostic: "has any e-mail trail" alone | 0.628 ± 0.031 | 0.628 ± 0.031 |
| Money + network, only the 111 with an e-mail trail | 0.673 ± 0.155 | 0.725 ± 0.116 |

Pay carries most of the signal; the network alone reaches ~0.73 without any label knowledge.
  **Sampling bias**: every person of interest has some e-mail trail while 32 others (mostly
  non-employee directors) have none — worth AUC 0.63 on its own — and the crisis-period share
  depends on whose mailbox was collected and kept.
- **Data quality**: the two rows ud120 shifted by one column (Belfer, Bhatnagar) are corrected
  from the court PDF, and every row's payment and stock components are checked to add up.
