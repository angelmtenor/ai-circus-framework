# 💸 Follow the Money — transaction monitoring for a bank consortium - Run it yourself

Source: docs/scenarios/aml_money_trail.md (this repository)

```bash
cd services/training && uv run python ../../scripts/prepare_aml_money_trail_dataset.py --diagnostics --ablation
make k3s-build k3s-import   # the scenario is read by every tabular service: rebuild them together
make k3s-pipeline           # etl-tabular -> training Jobs for the new scenario
```

Then open the app as admin, pick **Follow the Money** under *Banking & finance* and press play on
the **Money Trail** tab.
