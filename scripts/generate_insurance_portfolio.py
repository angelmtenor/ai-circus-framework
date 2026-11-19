#!/usr/bin/env python3
"""Synthetic data generator for the `insurance_portfolio` scenario (three models, ADR 0018).

One invented portfolio of a multi-line insurer (motor, home, health, life, pet, funeral,
personal accident) — no real customer, insurer or market behind it — written as three files
under `scenarios/insurance_portfolio/sample_data/`:

  - `policyholders_lapse.csv`, target `Lapsed`: the policyholder let their policies lapse at
    the last renewal (the main model).
  - `policyholders_cross_sell.csv`, target `AddedProduct`: the policyholder bought one more
    product after the last campaign (extra model `cross_sell`).
  - `portfolio_baskets.csv`: one row per (policyholder, product held) — the baskets of the
    `products` recommender, "customers who hold this also hold".

The two policyholder files share the same 17 feature columns on purpose: the retention
watchlist scores the selected policyholder with the cross-sell model too
(`ui_extras.next_best_offer`), so both models must read the same record.

Lines are held together the way a real book tends to bundle them: pet cover mostly comes with a
home policy, personal accident with motor, life with health, funeral with life after 55.

The outcomes are designed logistic formulas, not fitted to any real book:
  - lapse rises with the last renewal's price increase, late payments, recent claims, a
    high total premium and the phone channel; it falls with tenure, the number of policies
    held (bundled customers stay), use of the digital area and an agent relationship.
  - adding a product rises with digital engagement, tenure, an agent relationship and an
    obvious cover gap for the customer's age (no health cover after 35, no life cover after
    40); it falls with late payments and with how many lines the customer already holds.
    A customer who lapsed rarely buys more, so the two outcomes are anti-correlated.

Run: `python scripts/generate_insurance_portfolio.py` (numpy + pandas; any service venv has them).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

SEED = 7
N_POLICYHOLDERS = 5000
ROOT = Path(__file__).resolve().parents[1] / "scenarios"
REGIONS = ["Capital", "North", "South", "East", "West"]
CHANNELS = ["Online", "Agent", "Branch", "Phone"]
# Yearly premium of each line before individual pricing noise (EUR, invented).
LINE_PREMIUM = {
    "HasAuto": 520,
    "HasHome": 240,
    "HasHealth": 680,
    "HasLife": 310,
    "HasPet": 130,
    "HasFuneral": 190,
    "HasAccident": 90,
}


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-z))


def generate(n: int = N_POLICYHOLDERS, seed: int = SEED) -> pd.DataFrame:
    """Every feature column plus both outcomes (`Lapsed`, `AddedProduct`)."""
    rng = np.random.default_rng(seed)
    age = np.clip(rng.normal(46, 14, n), 18, 89).astype(int)
    region = rng.choice(REGIONS, n, p=[0.28, 0.17, 0.22, 0.20, 0.13])
    tenure = np.clip(rng.gamma(2.2, 3.0, n), 0, 30).astype(int)
    auto = rng.binomial(1, 0.55, n)
    home = rng.binomial(1, 0.50, n)
    health = rng.binomial(1, 0.30 + 0.10 * (age > 45), n)
    life = rng.binomial(1, 0.08 + 0.10 * (age > 40) + 0.30 * health, n)
    lines = {
        "HasAuto": auto,
        "HasHome": home,
        "HasHealth": health,
        "HasLife": life,
        "HasPet": rng.binomial(1, 0.04 + 0.22 * home, n),
        "HasFuneral": rng.binomial(1, 0.03 + 0.10 * (age > 55) + 0.25 * life * (age > 55), n),
        "HasAccident": rng.binomial(1, 0.05 + 0.28 * auto, n),
    }
    # Every policyholder holds at least one line: give the uncovered ones a motor policy.
    lines["HasAuto"][sum(lines.values()) == 0] = 1
    policies = sum(lines.values())
    premium = sum(LINE_PREMIUM[k] * v for k, v in lines.items()) * rng.normal(1.0, 0.12, n)
    claims = rng.poisson(0.4 + 0.5 * lines["HasAuto"], n).clip(0, 6)
    price_change = np.clip(rng.normal(4, 6, n) + 3 * claims, -10, 30).round(1)
    channel = rng.choice(CHANNELS, n, p=[0.30, 0.30, 0.25, 0.15])
    digital = rng.binomial(1, np.where(channel == "Online", 0.85, 0.35), n)
    late = rng.poisson(0.35, n).clip(0, 5)

    z_lapse = (
        -1.6
        - 0.55 * (policies - 2)
        - 0.10 * tenure
        + 0.16 * np.clip(price_change, 0, None)
        + 0.60 * late
        + 0.35 * claims
        - 1.00 * digital
        + 0.70 * (channel == "Phone")
        - 0.50 * (channel == "Agent")
        + 0.020 * (45 - age)
        + 0.80 * (premium > 1100)
    )
    lapsed = rng.binomial(1, _sigmoid(z_lapse))
    z_buy = (
        -2.0
        - 0.60 * (policies - 2)
        + 0.08 * tenure
        + 1.80 * digital
        - 0.90 * late
        + 1.30 * ((lines["HasHealth"] == 0) & (age > 35))
        + 1.00 * ((lines["HasLife"] == 0) & (age > 40))
        - 1.50 * lapsed
        + 0.80 * (channel == "Agent")
    )
    added = rng.binomial(1, _sigmoid(z_buy))

    return pd.DataFrame(
        {
            "CustomerId": 100001 + np.arange(n),
            "Age": age,
            "Region": region,
            "TenureYears": tenure,
            "NumPolicies": policies,
            **lines,
            "AnnualPremium": premium.round(0),
            "ClaimsLast3Y": claims,
            "PremiumChangePct": price_change,
            "Channel": channel,
            "DigitalActive": digital,
            "LatePayments": late,
            "Lapsed": lapsed,
            "AddedProduct": added,
        }
    )


# Recommender item id -> (label, family). The family is only the baskets file's nominal target.
PRODUCTS = {
    "HasAuto": ("MOTOR", "Motor", "Property & mobility"),
    "HasHome": ("HOME", "Home", "Property & mobility"),
    "HasHealth": ("HEALTH", "Health", "Personal"),
    "HasLife": ("LIFE", "Life", "Personal"),
    "HasPet": ("PET", "Pet", "Property & mobility"),
    "HasFuneral": ("FUNERAL", "Funeral", "Personal"),
    "HasAccident": ("ACCIDENT", "Personal accident", "Personal"),
}


def baskets(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (policyholder, product held): what the recommender learns co-holding from."""
    rows = [
        {
            "row_id": f"{customer}|{item}",
            "basket_id": str(customer),
            "product_id": item,
            "product_name": label,
            "product_family": family,
        }
        for customer, *held in df[["CustomerId", *PRODUCTS]].itertuples(index=False)
        for flag, (item, label, family) in zip(held, PRODUCTS.values(), strict=True)
        if flag
    ]
    return pd.DataFrame(rows)


def main() -> None:
    df = generate()
    out_dir = ROOT / "insurance_portfolio" / "sample_data"
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, target, other in (
        ("policyholders_lapse.csv", "Lapsed", "AddedProduct"),
        ("policyholders_cross_sell.csv", "AddedProduct", "Lapsed"),
    ):
        df.drop(columns=other).to_csv(out_dir / name, index=False)
        print(f"{name}: {len(df)} rows, {target} rate {df[target].mean():.1%}")
    held = baskets(df)
    held.to_csv(out_dir / "portfolio_baskets.csv", index=False)
    print(f"portfolio_baskets.csv: {len(held)} rows over {held['basket_id'].nunique()} baskets")


if __name__ == "__main__":
    main()
