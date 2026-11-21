# 🚩 Toxic Management & Work Environment — an NLP + ML tutorial - The data science

Source: docs/scenarios/toxic_leadership.md (this repository)

### Preparation

[`scripts/prepare_toxic_leadership_dataset.py`](../../scripts/prepare_toxic_leadership_dataset.py)
downloads a pinned mirror of the Kaggle file, verifies its SHA-256 and:

- keeps **engineering roles only** — job titles matched into five job families (first match
  wins, so a *Lead Data Scientist* is Data & AI);
- sets the target: **BadLeadership** = the Senior Management rating is 1–2★ (26% of reviews);
- **anonymises**: the employer becomes a coarse sector; the employer's own name, well-known
  companies, public executives and HQ towns become `[company]` / `[name]` inside the text and
  job title; e-mails and URLs are removed;
- **de-leaks**: overall rating, CEO approval, "would recommend" and outlook are dropped, and so
  are the culture and career ratings (the **halo effect** — see below);
- builds **Review** = headline + cons, cut at 1,000 characters, and drops duplicates;
- draws a stratified, seeded sample of 4,000 (Data & AI deliberately over-sampled: 1,000).

`--ablation` prints the comparison the tutorial quotes (5-fold CV on all 4,000 reviews):

| Inputs (LightGBM, small-data settings) | ROC AUC |
|---|---|
| Job context + pay + hours, no text | 0.825 |
| Review text only | 0.808 |
| **Everything we use** | **0.874** |
| + culture & career ratings (halo) | 0.923 |
| Everything, default LightGBM settings | 0.866 |

The halo ratings would make the model *look* better while making it useless for the question
at hand: someone who rates leadership 1★ rates culture 1★ too, so the model would stop reading.

### Text as features

The review is a `type: text` feature, vectorised **inside** the model pipeline, so the deployed
model reads raw text: unigrams + bigrams (`upper management`, `no direction`), terms in at least
5 reviews, at most 3,000, log-scaled TF, and sklearn's English stop words **minus the negations**
(`no`, `not`, `never`, `without`, …). SHAP runs on the TF-IDF columns; each term's contribution
is summed back into one `Review` bar for the waterfall and split over the words of the text for
the highlights (a bigram's share goes half to each word). What the words *absent* from a
review contribute (vs. an average review) is shown as the remainder.

### Model selection and honest numbers

Gradient boosting only, simplest settings first (Green Code: a heavier candidate must win by
more than 0.01 ROC AUC). The small-data settings win (0.869 vs 0.857). On the untouched 800-review
hold-out the deployed model reaches ROC AUC 0.886.

Things to keep in mind: reviews are **self-selected** and former employees over-represented;
bad-leadership ratings drift from about a third of reviews before 2017 to under a fifth in
2020–21; the data is UK English, 2008–2021.

### Responsible AI

The model reads **anonymous reviews about organisations**. It must never be used to score a
named manager, rank employees or monitor what staff write. No text model can see whether a CV
or LinkedIn profile is true, or how good someone is technically — that takes evidence:
technical conversations, work samples, references.

**Speaking up is part of the job.** When someone offloads their work onto your team, overloads
it or spreads falsehoods about it, a senior engineer or tech lead should report it — the people
below often can't. Report it the way you'd defend a model: evidence (who did the work, what was
promised and claimed, dates, hours), the right channel (your management chain, HR, the
company's internal reporting channel), behaviours rather than labels, and never rumours. The
model shows the aggregate pattern; the report is a human act, with facts.

---
