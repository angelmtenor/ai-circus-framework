# 🚢 Titanic Survival — an explained ML tutorial - The Tutorial tab

Source: docs/scenarios/titanic.md (this repository)

A tutorial scenario opens on its tutorial: eleven chapters of plain-language explanation, each
backed by live charts and widgets computed from this platform's own dataset and deployed model.
None of it is a screenshot.

<p align="center"><img src="../screenshots/titanic/tutorial-intro.png" alt="Tutorial introduction with the chapter rail and live headline metrics" width="850"></p>

| # | Chapter | Live content |
|---|---|---|
| 1 | Frame the problem: probability vs yes/no; why ROC AUC over accuracy | Class balance, and the 61.6% "nobody survived" baseline |
| 2 | Meet the data: the raw columns | Preview of the model-ready rows |
| 3 | Clean the data: every imputation is a modelling decision | Table of missing values and what was done |
| 4 | Engineer features: Title, travelling party, fare per person | Survival rate by title and by party size |
| 5 | Explore: who survived? | Survival by sex, by class × sex, age distribution, fare by class |
| 6 | Choose a model: pipeline, candidates, the CV protocol, Green Code | The **real training leaderboard** |
| 7 | Evaluate honestly | Hold-out **ROC curve** and **confusion matrix** |
| 8 | Explain the model: what SHAP means | Global SHAP importance |
| 9 | Ask what-if questions | The original notebook's passengers, **scored live** |
| 10 | Responsible AI: why sex is a feature here and not in churn | – |
| 11 | Your turn | A link to the Voyage tab |

<p align="center">
  <img src="../screenshots/titanic/tutorial-explore.png" alt="Chapter 5: survival rate by sex, by class and sex, age distribution by fate, and fare per person by class" width="49%">
  <img src="../screenshots/titanic/tutorial-model.png" alt="Chapter 6: the model-selection leaderboard from training" width="49%">
</p>
<p align="center">
  <img src="../screenshots/titanic/tutorial-evaluate.png" alt="Chapter 7: ROC curve (AUC 0.873) and confusion matrix on the 179-passenger hold-out" width="49%">
  <img src="../screenshots/titanic/tutorial-examples.png" alt="Chapter 9: the notebook's example passengers scored live, with their top SHAP reasons" width="49%">
</p>

Chapter 9 re-runs the original notebook's what-if passengers through the deployed model. The
average 2nd-class man gets 6% and the average 2nd-class woman 84%, identical except for sex. A
7-year-old boy gets 96% / 92% / 86% in 1st / 2nd / 3rd class. Sex decided most fates, and
class decided who among the women and children got out.

---
