# 🕵️ Payment Fraud & Financial Crime - The Payment stream tab

Source: docs/scenarios/financial_crime_desk.md (this repository)

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
