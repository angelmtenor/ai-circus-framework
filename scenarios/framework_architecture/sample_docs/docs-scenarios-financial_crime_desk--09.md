# 🕵️ Payment Fraud & Financial Crime - Responsible AI

Source: docs/scenarios/financial_crime_desk.md (this repository)

- **Pseudonymous ids only.** No name in either dataset, and the wording never names a religion, nationality or real
  organisation.
- **Age is audit-only.** The customer's age is kept out of the fraud model (`protected_features_excluded`) and used
  only to audit outcome rates.
- **Jurisdiction is never enough.** The data mixes ordinary and suspicious traffic to every jurisdiction group, and
  the LLM's guidance says so.
- **People decide.** The models rank and explain; the governance declaration (`human_oversight: review`) says that
  an analyst escalates, reports or releases every alert.
- **Honest scores.** Every view that shows a real outcome next to a score uses out-of-fold scores.
