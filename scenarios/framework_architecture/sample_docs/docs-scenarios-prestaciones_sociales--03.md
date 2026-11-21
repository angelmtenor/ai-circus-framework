# 🤝 Emergency Social Aid — rules, a model and a person - Design: why rules, a binary model and a policy

Source: docs/scenarios/prestaciones_sociales.md (this repository)

- **Rules first.** A missing ID or a value out of range is not a prediction problem — it is a *requirement*
  (art. 68 of Ley 39/2015: the applicant gets 10 days to fix it). Putting those cases through a model
  would teach it the rules' own outcome. So rules run first, the model never trains on what they decide,
  and a record they stop is still scored but shown as *"modelo no aplicado"*.
- **Four rule families**, all declared in YAML (`dataset.business_rules`): **completitud** (a document or
  box is missing → *requerimiento de subsanación*), **validez y coherencia** (an amount over 1,500 €, more
  minors than household members → subsanación), **requisitos** (under 6 months in the census, income over
  1.5 × IPREM, more than two aids in 12 months → *inadmisión*) and **protección** (gender violence, a court
  eviction date → forced *manual review* whatever the score).
- **Three outcomes without a multiclass model.** The corpus is tagged *granted / denied*; the third outcome
  is **selective prediction**: below 30% → deny, above 70% → grant, in between a person decides.
  Everything is a **proposal** — access to essential public benefits is a high-risk use under the EU AI
  Act (Annex III) and an administrative act must state its reasons (art. 35 of Ley 39/2015).
- **Fairness by exclusion, and by audit.** Sex and nationality are never model inputs
  (`protected_features_excluded`); they are kept (`audit_columns`) only to report outcome rates per group.
- **A model learns the past, including its favours.** Trained on real resolutions, a model would
  reproduce any favouritism hidden in them. That is why the requirements live in explicit, auditable
  rules rather than in the model, why the decision policy keeps the uncertain middle for a person, and
  why the *real resolution* overlay and the equity panel exist: to look for exactly that drift.
