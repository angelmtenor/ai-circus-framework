# 🚢 Titanic Survival — an explained ML tutorial - Adding the next tutorial

Source: docs/scenarios/titanic.md (this repository)

A tutorial is a YAML file:

1. Add `scenarios/<slug>/` with the data under `sample_data/` and a `scenario.yaml` whose
   `industry` is `tutorial`.
2. Write the `tutorial:` chapters: markdown `body`, an optional `takeaway`, `charts`, and
   `widgets`. The schema rejects unknown chart columns and invalid example records at seed time.
3. Optionally, set `model.cv_folds` / `model.selection_metric` for a small dataset, and add a
   `ui_extras` tab that suits the story.
4. Restart `platform-registry` (it seeds scenarios), then run `make k3s-pipeline`.

The second tutorial, [Toxic Management & Work Environment](toxic_leadership.md), follows exactly this recipe and adds
free text (`type: text` features), a transformer challenger and an LLM rubric check. Good
candidates from the same notebook collection are *Student Admissions* and *House Prices*. The
**Society & Ethics** domain is ready for fairness-oriented datasets.
