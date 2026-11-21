# 🚢 Titanic Survival — an explained ML tutorial - Run it yourself

Source: docs/scenarios/titanic.md (this repository)

On a running cluster (see the main [README](../../README.md#getting-started)):

```bash
make k3s-pipeline
```

Then open <http://aiopen.localhost>, set **Domain → Tutorials** and open **Titanic Survival — ML
Tutorial**.

<p align="center"><img src="../screenshots/titanic/gallery-tutorials.png" alt="Scenario gallery filtered to the Tutorials domain" width="700"></p>

To change the feature recipe, edit the script and regenerate the seed file, then re-run the
pipeline:

```bash
services/training/.venv/bin/python scripts/prepare_titanic_dataset.py
```

---
