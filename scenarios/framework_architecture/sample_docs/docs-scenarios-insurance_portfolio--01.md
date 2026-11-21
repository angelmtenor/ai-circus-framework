# 🛡️ Insurance Retention & Next Best Offer

Source: docs/scenarios/insurance_portfolio.md (this repository)

A multi-line insurer's portfolio (motor, home, health, life, pet, funeral and personal accident) with three
models in one scenario (`insurance_portfolio`, [ADR 0018](../adr/0018-several-models-per-scenario.md)):

| Model | Address | Question | Kind |
|---|---|---|---|
| Lapse risk (main) | `insurance_portfolio` | Will this policyholder lapse at renewal? | LightGBM classification, SHAP |
| Cross-sell | `insurance_portfolio~cross_sell` | Will they add a product after a campaign? | LightGBM classification, SHAP |
| Product affinity | `insurance_portfolio~products` | Which products are held together? | Association rules and item factors |

**The data is synthetic.** `scripts/generate_insurance_portfolio.py` invents 5,000 policyholders of a fictional
insurer. No real person, insurer or market is behind the numbers, so the metrics illustrate the method, not a
real book of business.
