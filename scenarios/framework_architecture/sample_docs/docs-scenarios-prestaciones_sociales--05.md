# 🤝 Emergency Social Aid — rules, a model and a person - The regulations: RAG as one more tool

Source: docs/scenarios/prestaciones_sociales.md (this repository)

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
