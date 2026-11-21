# 🕸️ Enron: Follow the Network — link analysis for financial-crime investigation - Run it yourself

Source: docs/scenarios/enron_fraud_network.md (this repository)

On a running cluster (see the main [README](../../README.md#getting-started)):

```bash
make k3s-pipeline
```

Regenerating the data (downloads ~493 MB of e-mail headers once, cached in `~/.cache/ai-circus/enron/`):

```bash
cd services/training && uv run --with 'networkx>=3.4,<4' python ../../scripts/prepare_enron_fraud_network_dataset.py --ablation
```
