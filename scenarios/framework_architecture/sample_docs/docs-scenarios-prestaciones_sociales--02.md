# 🤝 Emergency Social Aid — rules, a model and a person

Source: docs/scenarios/prestaciones_sociales.md (this repository)

| | |
|---|---|
| **Question** | Does an application that has passed the rules deserve a grant? The model returns P(granted); two thresholds turn it into *propuesta de concesión*, *revisión manual* or *propuesta de denegación*. |
| **Data** | **600 synthetic applications** (no public row-level corpus of benefit applications exists — benefits open data is aggregate only). Facts and resolutions are generated with a seed; the three text boxes were written **once, offline, by an LLM from those facts** (never from the resolution), checked by a leak guard. |
| **Rules** | 16 deterministic rules in 4 families, evaluated **before** the model: they stop 76 of 600 applications (13%), which are kept for analytics but never trained on. |
| **Model** | One LightGBM (`lightgbm_small_data`) over numeric + categorical + three TF-IDF text boxes (Spanish stop-words, negations *kept*). |
| **5-fold CV ROC AUC** | **0.856 ± 0.028** on the 419-application training split (default LightGBM: 0.856 — no gain, so the small-data settings stay) |
| **Hold-out (105)** | ROC AUC **0.887** · accuracy 82.9% · out-of-fold AUC over all 524 trainable rows **0.879** |
| **Ablation** | structured only 0.838 · text only 0.770 · **both 0.884** (5-fold CV, the same rows) |
| **Transformer challenger** | voyage-4-nano embeddings of the three boxes + the same LightGBM: CV 0.863 ± 0.034 · hold-out 0.885 — **+0.008, below the 0.01 bar**, so TF-IDF stays deployed |
| **Tabs** | Scenario · Data & BI · ML Predictions · ML Insights · **Mesa de valoración** |
| **Regulations (RAG)** | 24 short documents the assistant searches as **one more tool** (`consultar_normativa`): 15 real articles quoted verbatim (Leyes 39/2015, 40/2015, 38/2003, 7/1985; EU AI Act) + Villaclara's fictional bases, internal instruction and citizen-service protocol |
| **New platform capabilities** | `business_rules` + `decision_policy`, `text_language`, multi-box challenger, `ui_extras: case_desk`, scanned intake (`/extract-record`), **`documents.tool`** (a tabular assistant's document search, with a documents-only chat scope and per-reply provenance) |
