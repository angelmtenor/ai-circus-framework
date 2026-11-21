# 🕵️ Payment Fraud & Financial Crime

Source: docs/scenarios/financial_crime_desk.md (this repository)

A bank's financial-crime desk with three models in one scenario (`financial_crime_desk`,
[ADR 0018](../adr/0018-several-models-per-scenario.md)):

| Model | Address | Question | Kind |
|---|---|---|---|
| Transfer screening (main) | `financial_crime_desk` | Should an analyst look at this outgoing cross-border transfer? | LightGBM on the message's fields and its free-text purpose (TF-IDF), SHAP, a sentence-embedding challenger, an LLM analyst |
| Payment fraud | `financial_crime_desk~payments` | Is this customer payment fraud? | Deterministic rules, then LightGBM on session and payment signals, SHAP |
| Mule accounts | `financial_crime_desk~mules` | Is this account a money mule? | Behaviour features computed offline from 60 days of transfers, logistic regression or LightGBM, SHAP, the account network |

The assistant also searches eight articles of the EU Anti-Money Laundering Regulation (EU) 2024/1624.

**The data is synthetic.** `scripts/generate_financial_crime.py` invents 3,000 credit-transfer messages and 5,000
customer payments of a fictional European bank, and `scripts/generate_financial_crime_mules.py` 60 days of
transfers between 800 of its accounts. No real customer, bank, payment or investigation is behind the
numbers, so the metrics illustrate the method, not a real bank's detection rates.
