# 🛡️ Insurance Retention & Next Best Offer - Run it yourself

Source: docs/scenarios/insurance_portfolio.md (this repository)

```bash
python scripts/generate_insurance_portfolio.py   # only to regenerate the committed samples
kubectl -n ai-circus rollout restart deployment/platform-registry   # seeds the scenario
make k3s-pipeline                                 # trains every model, this scenario's three included
```

Then open **Insurance Retention & Next Best Offer** in the picker (domain *Insurance*).
