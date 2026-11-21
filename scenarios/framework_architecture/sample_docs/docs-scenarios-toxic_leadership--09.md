# 🚩 Toxic Management & Work Environment — an NLP + ML tutorial - Run it yourself

Source: docs/scenarios/toxic_leadership.md (this repository)

On a running cluster (see the main [README](../../README.md#getting-started)):

```bash
make k3s-pipeline
```

That trains the deployed TF-IDF model. For the transformer challenger, embed the reviews on this
machine's GPU (CPU works too, much slower) and retrain:

```bash
make k3s-text-embeddings SCENARIOS=toxic_leadership
```

Then open <http://aiopen.localhost>, set **Domain → Tutorials** and open **Toxic Management & Work Environment — NLP + ML
Tutorial**.

<p align="center"><img src="../screenshots/toxic_leadership/gallery-tutorials.png" alt="Scenario gallery filtered to the Tutorials domain: Titanic and Toxic Management" width="700"></p>

To change the recipe, edit the script and regenerate the seed file (and the tutorial's ablation
numbers), then re-run the pipeline:

```bash
cd services/training && uv run python ../../scripts/prepare_toxic_leadership_dataset.py --ablation
```

---
