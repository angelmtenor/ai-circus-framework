# 🤝 Emergency Social Aid — rules, a model and a person

> Domain: **Public sector** · Kind: `tabular_ml` (binary classification, free text + structured data, **business rules before the model**) · Scenario: [`scenarios/prestaciones_sociales/scenario.yaml`](../../scenarios/prestaciones_sociales/scenario.yaml) · UI in **Spanish**

**Why.** Emergency social aid is public money for people who cannot cover the basics — and in Spain
its manual assessment has two well-known failure modes. **Fraud**: undeclared income, the same aid
claimed again for the same expense, money requested for something that isn't a basic need or that
the household can perfectly well afford. **Favouritism**: staff approving aid by hand for people they
know. This scenario puts every application through **the same rules and the same model**, and gives
every proposal its reasons box by box — so a grant or a denial has to be justified by the file, not
by who the applicant is, and departing from the proposal has to be argued in writing. The aid reaches
the people who actually need it; nobody is accused of anything by an algorithm (the rules and the
model only say what to check — a social worker checks it and decides).

The (fictional) Ayuntamiento de Villaclara gives *Ayudas Económicas de Emergencia Social* — up to
1,500 € for rent, utility bills, food, school supplies or medical expenses. Every application is a
basic form: **numbers** (income, household, months in the census), **categories** (employment, housing,
concept of the aid) and **three free-text boxes** (the applicant's own account, what the money is for,
the social worker's report). The scenario answers what an intelligent-document-processing desk would be
asked: *which applications can wait for a person, which can't, and which boxes explain the proposal?*

<p align="center">
  <img src="../screenshots/prestaciones_sociales/circuit.png" alt="The circuit: 600 applications flow from the registry through the business rules (76 stopped: 57 need more documents, 19 are inadmissible) and the model into three trays — grant, manual review, deny — with live threshold sliders and KPIs" width="900">
</p>
<p align="center"><sub>Every dot is an application. The rules stop what is incomplete or doesn't meet the requirements; the model sorts the rest. Drag the thresholds and the trays re-sort live.</sub></p>

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

## Design: why rules, a binary model and a policy

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

## The Mesa de valoración tab

1. **The circuit** (canvas). The registry's 600 dots ride through the rules and the model into five trays.
   Colours — grant green, review blue, deny orange — are the first three slots of a palette validated for
   colour-vision deficiency; the two rule-stopped trays are recessive grey, and every tray also carries a
   glyph and a label. Scores are **out-of-fold**: from a model that never saw that application.
2. **The thresholds.** Two sliders re-sort the trays live. KPIs show the trade-off a manager actually
   tunes: share resolved without review, **error in the automated trays** against the real resolution,
   and the manual-review workload. *Show the real resolution* recolours dots by what really happened
   and rings the proposals it contradicts.
3. **The application as an official sheet.**

<p align="center">
  <img src="../screenshots/prestaciones_sociales/case-review.png" alt="A manual-review application opened as a paper form: numbered boxes lit green or orange by how much they pushed the proposal, free-text boxes with highlighted words, the rule checklist grouped by family, and the boxes to review" width="900">
</p>

   Each numbered box is tinted by its SHAP contribution (green pushes towards granting, orange towards
   denying); the text boxes highlight the words that moved the score; the side panel stamps the rules
   one by one, shows the probability against both thresholds and lists the boxes that weigh most — for a
   manual-review case, *"casillas a revisar"*: the strongest pushes in opposite directions. The TF-IDF model
   and its challenger are compared on the same application.
4. **Registrar documento** — scanned intake.

<p align="center">
  <img src="../screenshots/prestaciones_sociales/scan-reading.png" alt="A scanned application being read: boxes fill one by one with a confidence chip each while the rest wait" width="520">
</p>

   Four fictional **scans** (image-only PDFs: skewed, noisy, stamped) go through the platform's Spanish OCR
   (tesseract `spa`), then an LLM maps the text onto the boxes. It must **quote** where each value comes
   from; categorical values must be an option, numbers must parse and sit in range, and a quote the OCR
   text does not contain is flagged *unverified* with a lower confidence. Boxes appear one by one with
   their confidence; what could not be read stays marked *"no leído"*. Then rules → model → stamp — the
   scan without an ID is stopped by rule C1 before the model. Every box stays editable and re-scores.
5. **Nueva solicitud** — a blank form or six personas (a mother of two facing a power cut, an eviction with
   a court date, undeclared income, a car down-payment…), and **Equidad** — outcome rates by sex and nationality.

The dock **assistant** reads what is on screen (the open application's boxes, fired rules, probability,
decision and thresholds) and answers in Spanish — *"¿por qué va a revisión manual?"*, *"¿qué falta aquí?"*.

## The regulations: RAG as one more tool

BI (the data), ML (the rules and the model) and GenAI (the assistant) answer *what happened* and *what
the model proposes*; a case worker also needs *what the rules are* — and the anti-fraud and
anti-favouritism checks only work if everybody applies the same ones. So the scenario ships the
regulations its applications are judged by, and the assistant searches them **as one more tool**, next
to its dataset and prediction tools. Nothing about it is specific to this scenario: any `tabular_ml`
scenario gets one by adding a `documents` block with a `tool` (name, label, description the model reads,
documents-only suggestions) and a `vector_store`; etl-vectorize indexes it like any RAG scenario.

| Kind | Documents | Why they are here |
|---|---|---|
| **Real, verbatim** (BOE consolidated text / EUR-Lex) | Ley 39/2015 arts. 21, 28, 35, 53, 66, 68, 69 · Ley 40/2015 arts. 23–24 · Ley 38/2003 arts. 14, 37 · Ley 7/1985 art. 25 · AI Act arts. 14, 86 and Annex III(5) | Deadlines, motivation, *subsanación*; **checking data by interoperability instead of asking for paper** (art. 28) and the consequences of a false *declaración responsable* (art. 69); **refunds** when aid was obtained by hiding income (LGS art. 37); **abstention** when you know the applicant (Ley 40/2015 art. 23); human oversight and the right to an explanation (AI Act) |
| **Fictional** (Villaclara) | *Bases reguladoras* (arts. 1–16) · *Instrucción 1/2026* · *Protocolo de atención a la ciudadanía* | The requirements the rules engine enforces (every `legal_basis` resolves to one of them — a test checks it), the **red flags** that call for a reinforced check (undeclared income, repeated aid, several applicants at one address, non-basic expenses, inconsistent receipts), departing from the proposal in writing, and how to ask for a check or communicate a denial **without accusing anyone** |

- **Where an answer comes from.** Every reply carries chips computed from the tools the agent really
  called — 📚 Normativa (with the articles it returned), ⚖️ Reglas + 🧮 Modelo (`predict_records`),
  🎯 Evaluación, 📊 Datos, 📈 Gráfico — and the prompt asks the model to tag each paragraph
  **[Normativa] / [Reglas] / [Modelo] / [Datos]** and never blend them: a rule is not a probability.
  *"88 years old, lives alone, 1,100 €/month pension, asks for 900 € of electricity debt: does she
  qualify?"* → `predict_records` (rule R2: 1,100 / 1 / 600 = 1.83 × IPREM > 1.5 → *inadmisión*) +
  `consultar_normativa` (Bases art. 4.2.b) → an answer that says which part is the rule, which the norm,
  and what else the person may need.
- **Documents-only mode.** The chat's *Todas las fuentes · Solo normativa* switch is sent as AG-UI
  `forwardedProps.scope`; in documents mode the assistant is **built with that one tool** — the data and
  the model are not hidden by the prompt, they are absent from the run.
- **The reading room.** The Mesa de valoración gains a **📚 Normativa** mode: the documents grouped by law
  and marked *Texto oficial* / *Ficticio*, a paper-like reading pane, and a documents-only chat whose cited
  articles light up in the index. Any fired rule's legal basis has a **📖 Ver norma** button that opens
  its article and asks the chat about it for that application.

<p align="center">
  <img src="../screenshots/prestaciones_sociales/chat-provenance.png" alt="The dock assistant answering whether an 88-year-old living alone on a 1,100 € pension qualifies: tagged [Reglas], [Modelo] and [Normativa] paragraphs with short citations, then chips naming the sources it really used — Normativa, Reglas, Modelo, Datos — and the articles retrieved" width="420">
  <img src="../screenshots/prestaciones_sociales/reading-room.png" alt="The reading room: the documents grouped by law and marked Ficticio or Texto oficial, article 68 of Ley 39/2015 on a paper-like pane, and the documents-only chat explaining the legal basis of a missing-ID rule, with the cited articles lit in the index" width="900">
</p>
<p align="center"><sub>Left: the 88-year-old case, answered from the rules, the model and the norms — each one tagged. Right: «📖 Ver norma» on a missing-ID rule opens Ley 39/2015 art. 68 and asks the documents-only chat about it.</sub></p>

## The data science

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

## How it is built

| Piece | Where |
|---|---|
| Rules + policy, pure and tested | [`libs/shared/src/ai_circus_shared/business_rules.py`](../../libs/shared/src/ai_circus_shared/business_rules.py); schema in `scenario_schema.py` (`BusinessRules`, `DecisionPolicy`, `CaseDeskExtra`) |
| Rules before `dropna()`, blocked rows kept | `etl-tabular` `core/etl.py` · training drops them (`rules_excluded_rows`) |
| `rules` / `gate` / `decision` on every prediction, per-row gates in the dataset sample | `prediction` `api.py` |
| Scanned intake | `assistant` `core/extraction.py` (`POST /extract-record/{slug}`, `GET /intake-samples/{slug}/{file}`) |
| The tab | `ui-react` `CaseDeskView.tsx`, `CaseDeskCircuit.tsx`, `CaseSheet.tsx`, `caseDeskLogic.ts` |
| Corpus + scans | `scripts/generate_prestaciones_sociales_dataset.py`, `scripts/generate_prestaciones_sample_documents.py` |
| Regulations | `scenarios/prestaciones_sociales/sample_docs/` — real texts by `scripts/prepare_prestaciones_normativa.py` (BOE open-data API + Cellar, SHA-pinned), fictional ones hand-written |
| Document tool, scope, reading list | `assistant` `core/tools.py` (`build_document_tool`), `core/chat.py`, `api.py` (`_chat_scope`, `GET /documents/{slug}`); shared retrieval in `ai_circus_shared/retrieval.py` |
| Provenance chips, reading room | `ui-react` `chatProvenance.ts`, `ChatPanel.tsx`, `NormativaDesk.tsx` |

## Run it yourself

```bash
make k3s-pipeline                                    # ETL (rules before the model) + training
make k3s-text-embeddings SCENARIOS=prestaciones_sociales   # host GPU: embeds the 3 boxes, retrains the challenger
# index the regulations (the same job as every RAG scenario)
kubectl -n ai-circus delete job etl-vectorize --ignore-not-found && kubectl apply -f k8s/jobs/etl-vectorize-job.yaml
```

Refresh the real legal texts (cached under `~/.cache/ai-circus/prestaciones_normativa/`):

```bash
uv run --with httpx python scripts/prepare_prestaciones_normativa.py
```

etl-vectorize only bootstraps a tenant's documents when its `normativa/` prefix is empty — after editing
them, delete that prefix in SeaweedFS (or the tenant keeps its copy) and re-run the job.

Regenerate the corpus (the texts need the cluster's llm-gateway on `localhost:4000`):

```bash
cd services/training
uv run python ../../scripts/generate_prestaciones_sociales_dataset.py structure   # seeded facts + resolutions
LLM_GATEWAY_API_KEY=… uv run python ../../scripts/generate_prestaciones_sociales_dataset.py texts --model gemini-flash
uv run python ../../scripts/generate_prestaciones_sociales_dataset.py build       # CSV + ablation
cd ../form-agent && uv run python ../../scripts/generate_prestaciones_sample_documents.py   # the four scans
```

## Credits

Original content: the entity, its rules ("bases reguladoras"), every application and every scanned
document are **fictional**, and so are its bases, internal instruction and citizen-service protocol. The
IPREM (600 €/month) is the real Spanish public-income indicator. The 15 legal texts are real and quoted
verbatim: Spanish laws from the BOE's consolidated legislation (no copyright on legal texts, art. 13 LPI),
the AI Act from EUR-Lex (© European Union, reuse authorised by Decision 2011/833/EU). Not legal advice.
