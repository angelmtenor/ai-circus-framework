# 🚩 Toxic Management & Work Environment — an NLP + ML tutorial - The Tutorial tab

Source: docs/scenarios/toxic_leadership.md (this repository)

Thirteen chapters, every chart, metric and prediction live:

| # | Chapter | Live widgets |
|---|---|---|
| 1 | Frame the problem — why ROC AUC, not accuracy | class balance |
| 2 | Meet the data | dataset preview |
| 3 | Clean, anonymise, de-leak — the **halo effect** | ablation table |
| 4 | Text is data: tokens → n-grams → TF-IDF (why `not` stays) | |
| 5 | Explore: is it the money, the hours — or the manager? | 4 charts |
| 6 | Combine text and structure: gradient boosting | model leaderboard |
| 7 | Evaluate honestly | ROC curve, confusion matrix |
| 8 | Bag of words vs transformers | benchmark table |
| 9 | The vocabulary of toxic leadership | global importance, red/green-flag terms |
| 10 | Read a review like the model | 6 examples, highlighted, with the challenger's score |
| 11 | NLP pitfalls — sarcasm, negation, proxies, shortcuts | |
| 12 | Responsible AI: reviews, not people | |
| 13 | Your turn | |

<p align="center">
  <img src="../screenshots/toxic_leadership/tutorial-vocabulary.png" alt="Chapter 9: the review text is the most important input; red flags (management, leadership, managers, away, worst, politics, terrible, no growth…) and green flags (great, nice, hikes, great company…)" width="49%">
  <img src="../screenshots/toxic_leadership/tutorial-examples.png" alt="Chapter 10: six archetype reviews scored live, each word coloured by its SHAP contribution" width="49%">
</p>
<p align="center">
  <img src="../screenshots/toxic_leadership/tutorial-model.png" alt="Chapter 6: the live leaderboard — small-data LightGBM deployed, default LightGBM, and the voyage-4-nano challenger" width="49%">
  <img src="../screenshots/toxic_leadership/tutorial-deleak.png" alt="Chapter 3: anonymisation, de-leaking and the halo effect, with the ablation table" width="49%">
</p>

---
