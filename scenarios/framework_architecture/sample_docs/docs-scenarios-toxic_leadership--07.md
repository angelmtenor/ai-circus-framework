# 🚩 Toxic Management & Work Environment — an NLP + ML tutorial - Bag of words vs transformers

Source: docs/scenarios/toxic_leadership.md (this repository)

The same LightGBM, the same folds, four ways to read the text (5-fold CV on all 4,000 reviews):

| Text encoder | Text only | Text + job context |
|---|---|---|
| TF-IDF (bag of words, 1972) | 0.808 | 0.874 |
| all-MiniLM-L6-v2 (22M parameters) | 0.816 | 0.876 |
| all-mpnet-base-v2 (110M) | 0.831 | 0.881 |
| **voyage-4-nano** (the platform's `local-embed`) | **0.837** | **0.881** |

Transformers read text alone clearly better (+0.03); combined with the job context the gap
shrinks to about +0.01: +0.007 above, +0.012 in the live leaderboard's cross-validation, and a
tie on the hold-out (0.886 vs 0.885). The costs are real: voyage-4-nano embeds all 4,000
reviews in seconds on a laptop GPU (RTX 4070) but would take about 40 minutes on the cluster's
CPU; every live prediction needs a round-trip to the embedding model (~0.6 s vs ~0.1 s); and
its 1,024 dimensions don't map to words.

So the platform deploys the TF-IDF model and trains a **voyage-4-nano + LightGBM challenger** on
the same split every run. The model card flags when the challenger clears the Green Code bar;
promoting it is a human decision.

---
