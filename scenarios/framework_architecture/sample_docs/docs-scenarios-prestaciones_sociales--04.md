# 🤝 Emergency Social Aid — rules, a model and a person - The Mesa de valoración tab

Source: docs/scenarios/prestaciones_sociales.md (this repository)

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
