# 🚢 Titanic Survival — an explained ML tutorial

Source: docs/scenarios/titanic.md (this repository)

> Domain: **Tutorials** · Kind: `tabular_ml` (binary classification) · Scenario: [`scenarios/titanic/scenario.yaml`](../../scenarios/titanic/scenario.yaml)

On 15 April 1912 the RMS Titanic sank on her maiden voyage and more than 1,500 of the 2,224
people aboard died. This scenario turns the real passenger list into a complete, **explained**
machine-learning tutorial. It walks every step from the raw CSV to an explained survival
probability, then puts all 891 passengers back aboard an illustrated liner so you can explore
who survived, what the model predicted for each of them, and why.

<p align="center">
  <img src="../screenshots/titanic/voyage-model.png" alt="The Voyage tab: all 891 passengers berthed by class aboard an illustrated cutaway of the RMS Titanic at night, coloured from red (likely perished) through grey to blue (likely survived)" width="900">
</p>
<p align="center"><sub>Every dot is a real passenger, berthed by ticket class and coloured by the model's survival probability.</sub></p>

It is ported from the 2018 Keras notebook in
[angelmtenor/data-science-keras](https://github.com/angelmtenor/data-science-keras/blob/master/notebooks/titanic.ipynb)
and modernised: engineered features, a small-data gradient-boosting model chosen by
cross-validation, SHAP explanations, and honest hold-out metrics.

| | |
|---|---|
| **Question** | Did a passenger survive the sinking? We predict a probability, not a yes/no. |
| **Data** | Kaggle *Titanic — Machine Learning from Disaster*, labelled file: 891 passengers (the crew are not included) |
| **Model** | LightGBM tuned for small tables (`lightgbm_small_data`), selected over logistic regression |
| **5-fold CV ROC AUC** | **0.885 ± 0.015** (logistic regression: 0.872 ± 0.016) |
| **Hold-out (179 passengers)** | ROC AUC **0.873** · accuracy 82.1% · precision 81.4% · recall 69.6% |
| **The 2018 Keras DNN** | ROC AUC 0.86 · accuracy 82.7% |
| **Tabs** | Scenario · **Tutorial** · Data & BI · ML Predictions · ML Insights · **Voyage** |
| **Extra infrastructure** | None. It uses the existing `prediction`/`training`/`etl-tabular` services; model artifacts are ~350 KB |
