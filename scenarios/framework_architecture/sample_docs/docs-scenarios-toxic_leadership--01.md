# 🚩 Toxic Management & Work Environment — an NLP + ML tutorial

Source: docs/scenarios/toxic_leadership.md (this repository)

> Domain: **Tutorials** · Kind: `tabular_ml` (binary classification, free text + structured data) · Scenario: [`scenarios/toxic_leadership/scenario.yaml`](../../scenarios/toxic_leadership/scenario.yaml)

"People don't leave companies, they leave managers." Engineers write it in reviews every day:
directors who can't read code, salespeople who sell what doesn't exist, visions made of
buzzwords, work offloaded, credit taken and blame handed down.

In most companies it is a **very small number of people** — managers, salespeople, self-styled
"AI experts" with an inflated CV and no idea of machine learning — who, as they say in Spain,
*tienen mucha cara*: they delegate their own job to the technical staff, overload them, and
spread falsehoods about them inside the company. A few such people can embitter a whole
engineering team and do outsized damage to the business. Recognising the pattern, and
reporting it with evidence, is a responsibility of every senior engineer and tech lead.

This scenario turns **4,000 real Glassdoor reviews by software, data and AI engineers** into a
model that reads them. A gradient-boosting model combines what each engineer *wrote* with the
facts of their job, learns the vocabulary of toxic management, and races a modern
sentence-transformer. An LLM coach reads any description of how someone behaves — a manager,
a salesperson, a self-styled expert — against a research-based rubric.

<p align="center">
  <img src="../screenshots/toxic_leadership/office-model.png" alt="The Office tab: a glass office tower at night, one floor per job family, every review a person at a desk coloured from blue (leadership OK) to red (bad leadership); an AI-FIRST neon on the roof and a C-suite penthouse with its lights on and nobody at the desks" width="900">
</p>
<p align="center"><sub>Every desk is a real review, seated by job family and coloured by the model's probability that the reviewer's leadership is failing them.</sub></p>

| | |
|---|---|
| **Question** | From what an engineer wrote, plus a few facts about their job, did they rate their senior management 1–2★? We predict a probability. |
| **Data** | 4,000 of the 838,566 reviews in Kaggle's *Glassdoor Job Reviews* (UK, 2008–2021): engineering roles only, employers anonymised. CC BY-SA 4.0 |
| **Inputs** | The review text (headline + cons), job family, employer sector, current/former, tenure, year, and the pay and work-life-balance ratings |
| **Model** | LightGBM tuned for small tables (`lightgbm_small_data`) on TF-IDF + the job context, selected over default LightGBM |
| **5-fold CV ROC AUC** | **0.869 ± 0.006** on the 3,200-review training split (default LightGBM: 0.857) |
| **Hold-out (800 reviews)** | ROC AUC **0.886** · accuracy 84.4% |
| **Transformer challenger** | voyage-4-nano embeddings + the same LightGBM: CV 0.881 ± 0.011 · hold-out 0.885 |
| **Tabs** | Scenario · **Tutorial** · Data & BI · ML Predictions · ML Insights · **The Office** (with the **Leadership check**) |
| **New platform capability** | `type: text` features: TF-IDF inside the model pipeline, SHAP explained word by word |
