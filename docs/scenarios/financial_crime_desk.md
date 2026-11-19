# 🕵️ Payment Fraud & Financial Crime

A bank's financial-crime desk with two models in one scenario (`financial_crime_desk`,
[ADR 0018](../adr/0018-several-models-per-scenario.md)):

| Model | Address | Question | Kind |
|---|---|---|---|
| Transfer screening (main) | `financial_crime_desk` | Should an analyst look at this outgoing cross-border transfer? | LightGBM on the message's fields and its free-text purpose (TF-IDF), SHAP, a sentence-embedding challenger, an LLM analyst |
| Payment fraud | `financial_crime_desk~payments` | Is this customer payment fraud? | Deterministic rules, then LightGBM on session and payment signals, SHAP |

The assistant also searches eight articles of the EU Anti-Money Laundering Regulation (EU) 2024/1624.

**The data is synthetic.** `scripts/generate_financial_crime.py` invents 3,000 credit-transfer messages and 5,000
customer payments of a fictional European bank. No real customer, bank, payment or investigation is behind the
numbers, so the metrics illustrate the method, not a real bank's detection rates.

## Contents

- [The Message screening tab](#the-message-screening-tab)
- [The LLM analyst](#the-llm-analyst)
- [The Payment stream tab](#the-payment-stream-tab)
- [The data](#the-data)
- [The models](#the-models)
- [Responsible AI](#responsible-ai)
- [Run it yourself](#run-it-yourself)

## The Message screening tab

Every transfer is scored by a model that never saw it (out-of-fold) and listed in an inbox, highest score first,
with a threshold that splits it into *Escalate* and *Release*. Opening a transfer draws it as the message the bank
sends, MT103 / pacs.008 style:

| Tag | Field | Columns |
|---|---|---|
| `:20:` | Reference | the message reference |
| `:23B:` | Message | MT103 or pacs.008 |
| `:32A:` | Value date · currency · amount | date, currency, amount in EUR, round amount |
| `:50K:` | Ordering customer | pseudonymous id, individual or company, account age, transfers in the last 7 days |
| `:56A:` | Intermediary banks | how many correspondents are in the chain |
| `:59:` | Beneficiary | pseudonymous id, type, jurisdiction group, new beneficiary, prior payments |
| `:71A:` | Charges | SHA, OUR or BEN |
| `:70:` | Remittance information | the free-text purpose, at most 140 characters |

Each field is tinted by how much it pushed the score: red towards *Escalated*, blue away. The out-of-fold SHAP values
are summed over the field's columns. The free text is shaded word by word by the deployed model. Next to the message:

- **What pushed the score**: the top drivers, as probability points.
- **Two trained models**: the deployed TF-IDF + LightGBM model and the challenger, which reads the text as
  `voyage-4-nano` sentence embeddings. The challenger is never promoted on its own.
- **The LLM analyst** (below), on request.

*Reveal the investigation* adds the real outcome, the typology the investigation found, the misses at the
threshold, and a table of every typology with the share the model sends to an analyst. The typology explains the
outcomes and is never a model input.

The tab draws only DOM: no canvas, no animation, and an inbox capped at 60 rows.

## The LLM analyst

The scenario's `rubric_check` gives the active LLM (through llm-gateway) the transfer *as text*: its fields and
remittance information, never its score, outcome or typology. The LLM reads it against a rubric and must quote the
message as evidence for every signal; a quote not found in the message is marked as unverified.

- **Red flags:** a vague purpose, a payment for a third party, transit or re-export, sensitive or dual-use goods,
  support for an armed or extremist group, scam hallmarks, possible structuring, a profile mismatch.
- **Legitimate indicators:** a specific, verifiable reference, a recognisable everyday purpose, a registered
  charity, an established relationship.

`rubric_check.reviewer_role` sets the prompt's opening (here, a financial-crime analyst). The guidance tells the
model that a jurisdiction, currency or customer type is a reason to look closer and never a red flag by itself. It
must never judge a nationality, a religion or a person, and never state that a crime happened.

## The Payment stream tab

The `payments` model's `row_triage` tab replays the payments one by one. Each goes to *Analyst review* or
*Straight through* by a threshold you move, with the real workload and misses. Before the model, deterministic
business rules run on every payment:

| Rule | Outcome |
|---|---|
| Payee on the shared mule-account list | Blocked |
| Sanctions screening match | Blocked |
| Verification of payee: no match | Confirm with the customer |
| Over 5,000 EUR from a new device | Confirm with the customer (step-up authentication) |
| Remote-access software during the session | Analyst review |

Blocked payments and those awaiting confirmation are kept for analytics, but the model is never trained on them.
The Predictions tab shows which rules fired for any payment you enter.

## The data

**Transfers** (`payment_messages.csv`, target `Escalated`, 15% positive). Each message comes from a typology:

- *Ordinary:* payroll, supplier invoice, legitimate trade, rent, tuition, family support, property purchase,
  humanitarian donation, crypto purchase.
- *Suspicious:* trade-based laundering, sanctions evasion, terrorist-financing indicators, structuring, scam proceeds.

They overlap on purpose:

- 15% of suspicious messages are written like ordinary ones, and 7% of ordinary ones are vague.
- Most transfers to a higher-risk jurisdiction are ordinary (family support, humanitarian aid, trade).
- An investigation confirms 90% of the suspicious typologies and 1% of the ordinary ones.

**Payments** (`payments.csv`, target `Fraud`, 6% positive). The typologies are genuine, account takeover,
impersonation scam, investment scam and card-not-present. The signals a fraud engine sees: new device, new payee,
payee account age, time from login to payment (very fast for a script, very slow for a coached victim), failed
logins, changed credentials, an active phone call or remote-access software during the session, amount against the
customer's usual, hour, cross-border.

## The models

Both are LightGBM with SHAP explanations and out-of-fold scores (each row scored by a model that never saw it),
which the two tabs use.

| Model | Rows | ROC AUC, hold-out | ROC AUC, out of fold |
|---|---|---|---|
| Transfer screening (TF-IDF + LightGBM, deployed) | 3,000 | 0.925 | 0.952 |
| Transfer screening challenger (voyage-4-nano embeddings + LightGBM) | 3,000 | 0.934 | — |
| Payment fraud | 4,868 (132 stopped by a blocking rule) | 0.945 | 0.952 |

The transfer model reads both the structured fields and the words: on this data neither alone reaches the
combined score. The challenger scores slightly higher on the hold-out and the model card says so, but it is not
promoted on its own: a person decides, after weighing its cost (an embedding call per message).

## Responsible AI

- **Pseudonymous ids only.** No name in either dataset, and the wording never names a religion, nationality or real
  organisation.
- **Age is audit-only.** The customer's age is kept out of the fraud model (`protected_features_excluded`) and used
  only to audit outcome rates.
- **Jurisdiction is never enough.** The data mixes ordinary and suspicious traffic to every jurisdiction group, and
  the LLM's guidance says so.
- **People decide.** The models rank and explain; the governance declaration (`human_oversight: review`) says that
  an analyst escalates, reports or releases every alert.
- **Honest scores.** Every view that shows a real outcome next to a score uses out-of-fold scores.

## Run it yourself

```bash
python scripts/generate_financial_crime.py            # regenerate the two CSVs (seeded)
make k3s-pipeline                                     # ETL + training of every tabular scenario
make k3s-vectorize                                    # index the AMLR articles for the assistant's document tool
make k3s-text-embeddings SCENARIOS=financial_crime_desk   # optional: the challenger (host GPU), then retrain
```
