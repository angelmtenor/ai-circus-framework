# 🛡️ Insurance Retention & Next Best Offer - The data

Source: docs/scenarios/insurance_portfolio.md (this repository)

One row per policyholder, 17 features shared by both classifiers:

| Group | Features |
|---|---|
| Relationship | `TenureYears`, `NumPolicies`, `Channel` (Online, Agent, Branch, Phone), `DigitalActive` |
| Cover held | `HasAuto`, `HasHome`, `HasHealth`, `HasLife`, `HasPet`, `HasFuneral`, `HasAccident` (0/1) |
| Price and service | `AnnualPremium`, `PremiumChangePct`, `ClaimsLast3Y`, `LatePayments` |
| Profile | `Age`, `Region` |

The outcomes are designed logistic formulas:

- **Lapse** rises with the renewal price increase, late payments, claims, a high premium and the phone channel,
  and falls with tenure, bundled policies, use of the digital area and an agent relationship (30.5% lapsed).
- **Adding a product** rises with digital engagement, tenure, an agent and an obvious cover gap for the
  customer's age, and falls with late payments, the lines already held and a lapse (46.6% added one).
- **Products are bundled** the way a real book tends to be: pet cover mostly with home, personal accident with
  motor, life with health, funeral with life after 55. The recommender's baskets are each policyholder's
  holdings (10,964 rows over 5,000 baskets).
