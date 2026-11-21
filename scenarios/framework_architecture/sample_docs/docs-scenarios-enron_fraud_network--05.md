# 🕸️ Enron: Follow the Network — link analysis for financial-crime investigation - What a score is — and is not

Source: docs/scenarios/enron_fraud_network.md (this repository)

- A score is **resemblance** to the pay-and-e-mail profile of 18 persons of interest. It is not
  evidence of anything, and the tab and the assistant say so.
- "Person of interest" means indicted, settled with the SEC (usually without admitting
  wrongdoing) or testified in exchange for immunity. Some charges were dismissed; one conviction
  was vacated on the defendant's death. **A high score for anyone else is a model false positive.**
- Names are real because the record is public and the dataset is the canonical teaching set —
  but colleagues outside the pay table are anonymised, and no e-mail content or address is used.
- The honest result is a *triage* tool, not a detector: the top of the board concentrates the
  executives with extraordinary pay (precision 38% at *Review* or above, 3× the base rate), while
  many defendants — Broadband executives, traders, the finance staff behind the partnerships —
  look like everyone else on pay and e-mail. That is exactly where the documented relations,
  the paths and an investigator's judgement come in.
