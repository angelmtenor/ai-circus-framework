# 🤝 Emergency Social Aid — rules, a model and a person - The data science

Source: docs/scenarios/prestaciones_sociales.md (this repository)

- **Why text and numbers together.** Structured data alone reaches 0.838, text alone 0.770, both 0.884:
  the text adds what no form box captures (urgency, a court date, an expense that isn't a basic need).
  The most important inputs: income per head in IPREM units, the applicant's account, the social report,
  prior aids and minors.
- **Why TF-IDF is deployed and not the transformer.** Every term maps back to a word, so the proposal can
  be motivated **box by box and word by word**; an embedding's 1,024 dimensions cannot. voyage-4-nano is
  clearly multilingual (it aligns Spanish with English sentences and groups Spanish paraphrases that share
  no words) but it blurs **negation** — "tenemos ingresos suficientes" and "no tenemos ingresos suficientes"
  sit at cosine 0.93 — which TF-IDF bigrams (`no tenemos`, `sin ingresos`) keep apart. It is trained next to
  the deployed model every run and compared on any application; promoting it would be a human decision.
- **Spanish text.** `dataset.text_language: es` swaps TF-IDF's stop-word list for a Spanish one that keeps
  *no, sin, ni, nunca, muy, poco*.
- **Honest scores.** The deployed model is refit on all rows, so on 524 applications its own scores of them
  are optimistic. The circuit, the KPIs and the equity panel use **out-of-fold** scores
  (`model.out_of_fold_scores`) — required by the `case_desk` extra.

<p align="center">
  <img src="../screenshots/prestaciones_sociales/equity.png" alt="Equity panel: for each sex and nationality group, the share of applications proposed for grant, review and denial and the real grant rate" width="900">
</p>
