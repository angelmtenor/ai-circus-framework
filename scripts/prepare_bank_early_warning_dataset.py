#!/usr/bin/env python3
"""Dataset preparation for the `bank_early_warning` scenario.

Source: the FDIC BankFind Suite public API (<https://api.fdic.gov/banks/>) — US
federal government data, public domain. Three pinned queries, cached as raw JSON
under `~/.cache/ai-circus/fdic/` so a re-run never hits the network twice:

- **financials** at the 2008-12-31 call report (every insured institution that
  filed one), plus total assets at 2007-12-31 for year-on-year growth;
- **institutions** — name, city, state, headquarters coordinates, charter class
  and establishment date;
- **failures** — every closure dated 2009-01-01 .. 2010-12-31 (both inclusive).

One row per bank, one snapshot: the balance sheet a supervisor would have had on
the desk in early 2009, labelled with what happened over the following 24 months
(`failed_24m` = 1 if the FDIC closed the bank in 2009-2010). One snapshot per bank
keeps the train/test split honest — a bank can never appear on both sides of it.

Ratios are the call report's own (%, as published), grouped the CAMELS way in the
scenario's feature schema; a few are derived from dollar amounts (share of total
assets / deposits), and the heavy-tailed ones are clipped to a plausible range —
a small bank with a near-zero denominator can publish a ratio in the thousands:

- **Capital**: tier-1 leverage ratio, total risk-based capital ratio.
- **Asset quality**: noncurrent loans, 30-89 days past due, net charge-offs,
  foreclosed real estate (OREO), loan-loss reserve coverage of noncurrent loans.
- **Management**: efficiency ratio, asset growth over 2008.
- **Earnings**: return on assets, net interest margin.
- **Liquidity**: net loans / deposits, brokered deposits, core deposits.
- **Sensitivity / concentration**: construction & land development and nonfarm
  nonresidential (CRE) real-estate loans as a share of assets.
- **Profile**: total assets, age, charter type.

Banks that merged or closed voluntarily in 2009-2010 stay labelled 0 — the label
is "closed by the FDIC as a failure", a proxy for "failing or likely to fail", not
the whole distress spectrum (assisted mergers count as survivors here).

Run: `cd services/training && uv run python ../../scripts/prepare_bank_early_warning_dataset.py`
(any service venv with pandas). `--refresh` ignores the raw-JSON cache.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

SCENARIO_DIR = Path(__file__).resolve().parent.parent / "scenarios/bank_early_warning/sample_data"
OUT_PATH = SCENARIO_DIR / "bank_early_warning.csv"
CACHE_DIR = Path.home() / ".cache/ai-circus/fdic"
API = "https://api.fdic.gov/banks"
PAGE = 10_000

SNAPSHOT = "20081231"
PRIOR = "20071231"
FAIL_FROM, FAIL_TO = pd.Timestamp("2009-01-01"), pd.Timestamp("2010-12-31")

FINANCIAL_FIELDS = [
    "CERT", "ASSET", "RBC1AAJ", "RBCRWAJ", "NCLNLSR", "P3ASSET", "NTLNLSR", "ORE", "LNRESNCR",
    "EEFFR", "ROA", "NIMY", "LNLSDEPR", "BRO", "DEP", "COREDEP", "LNRECONS", "LNRENRES",
]  # fmt: skip
INSTITUTION_FIELDS = ["CERT", "NAME", "CITY", "STALP", "LATITUDE", "LONGITUDE", "ESTYMD", "BKCLASS"]
# FDIC institution class (the institution's last charter on record) -> a plain label.
CHARTER_TYPES = {
    "N": "National bank",
    "SM": "State member bank",
    "NM": "State non-member bank",
    "SB": "Savings bank",
    "SA": "Savings institution",
    "SL": "Savings institution",
    "SI": "Savings institution",
}


def _fetch(endpoint: str, filters: str, fields: list[str], refresh: bool) -> list[dict]:
    """Every row of one BankFind query, paginated, cached as JSON on first fetch."""
    key = hashlib.sha256(f"{endpoint}|{filters}|{','.join(fields)}".encode()).hexdigest()[:16]
    cache = CACHE_DIR / f"{endpoint}_{key}.json"
    if cache.exists() and not refresh:
        return json.loads(cache.read_text())
    rows: list[dict] = []
    offset = 0
    while True:
        query = urllib.parse.urlencode(
            {"filters": filters, "fields": ",".join(fields), "limit": PAGE, "offset": offset, "format": "json"}
        )
        with urllib.request.urlopen(f"{API}/{endpoint}?{query}", timeout=120) as response:  # noqa: S310 — fixed https host
            payload = json.load(response)
        batch = [item["data"] for item in payload["data"]]
        rows.extend(batch)
        total = payload["meta"]["total"]
        print(f"  {endpoint}: {len(rows)}/{total}")
        offset += PAGE
        if offset >= total or not batch:
            break
        time.sleep(0.5)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(rows))
    return rows


def build(refresh: bool) -> pd.DataFrame:
    """Join the three queries into the one-row-per-bank training table."""
    fin = pd.DataFrame(_fetch("financials", f"REPDTE:{SNAPSHOT}", FINANCIAL_FIELDS, refresh))
    prior = pd.DataFrame(_fetch("financials", f"REPDTE:{PRIOR}", ["CERT", "ASSET"], refresh))
    inst = pd.DataFrame(_fetch("institutions", "CERT:[1 TO 99999999]", INSTITUTION_FIELDS, refresh))
    failures = pd.DataFrame(_fetch("failures", "FAILYR:[2009 TO 2010]", ["CERT", "NAME", "FAILDATE"], refresh))

    failures["FAILDATE"] = pd.to_datetime(failures["FAILDATE"], format="%m/%d/%Y")
    failed = set(failures.loc[failures["FAILDATE"].between(FAIL_FROM, FAIL_TO), "CERT"].astype(int))

    df = fin.merge(prior.rename(columns={"ASSET": "ASSET_PRIOR"}), on="CERT", how="left")
    df = df.merge(inst, on="CERT", how="inner")
    df = df[df["ASSET"] > 0].copy()

    est = pd.to_datetime(df["ESTYMD"], format="%m/%d/%Y", errors="coerce")
    snapshot = pd.Timestamp(SNAPSHOT)
    out = pd.DataFrame(
        {
            "cert": df["CERT"].astype(int),
            "bank_name": df["NAME"].str.strip(),
            "city": df["CITY"].str.strip(),
            "state": df["STALP"],
            "latitude": df["LATITUDE"].round(4),
            "longitude": df["LONGITUDE"].round(4),
            # Capital
            "tier1_leverage_pct": df["RBC1AAJ"].clip(lower=-20, upper=60),
            "total_capital_ratio_pct": df["RBCRWAJ"].clip(upper=100),
            # Asset quality
            "noncurrent_loans_pct": df["NCLNLSR"],
            "past_due_30_89_pct": 100 * df["P3ASSET"] / df["ASSET"],
            "net_chargeoffs_pct": df["NTLNLSR"],
            "oreo_pct": 100 * df["ORE"] / df["ASSET"],
            "reserve_coverage_pct": df["LNRESNCR"].clip(upper=500),
            # Management
            "efficiency_ratio_pct": df["EEFFR"].clip(lower=0, upper=300),
            "asset_growth_pct": (100 * (df["ASSET"] / df["ASSET_PRIOR"] - 1)).clip(lower=-50, upper=200),
            # Earnings
            "roa_pct": df["ROA"].clip(lower=-20, upper=20),
            "net_interest_margin_pct": df["NIMY"].clip(lower=-1, upper=15),
            # Liquidity
            "loans_to_deposits_pct": df["LNLSDEPR"].clip(upper=300),
            "brokered_deposits_pct": 100 * df["BRO"] / df["DEP"].where(df["DEP"] > 0),
            "core_deposits_pct": 100 * df["COREDEP"] / df["ASSET"],
            # Sensitivity / concentration
            "construction_loans_pct": 100 * df["LNRECONS"] / df["ASSET"],
            "cre_loans_pct": 100 * df["LNRENRES"] / df["ASSET"],
            # Profile
            "total_assets_musd": (df["ASSET"] / 1000).round(1),
            "bank_age_years": ((snapshot - est).dt.days / 365.25).round(1),
            "charter_type": df["BKCLASS"].map(CHARTER_TYPES).fillna("Other"),
            "failed_24m": df["CERT"].astype(int).isin(failed).astype(int),
        }
    )
    ratio_cols = [c for c in out.columns if c.endswith("_pct")]
    out[ratio_cols] = out[ratio_cols].round(3)
    # A bank that reported no noncurrent loans has no coverage ratio (0/0) — fully
    # covered, for the model's purposes.
    out["reserve_coverage_pct"] = out["reserve_coverage_pct"].fillna(500.0)
    before = len(out)
    out = out.replace([np.inf, -np.inf], np.nan).dropna().sort_values("cert").reset_index(drop=True)
    print(f"Dropped {before - len(out)} banks with missing ratios/coordinates (of {before}).")
    unmatched = failed - set(out["cert"])
    print(f"{len(failed)} failures in window; {len(unmatched)} have no 2008 snapshot row (opened/merged mid-window).")
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--refresh", action="store_true", help="Ignore the raw-JSON cache and re-query the API.")
    args = parser.parse_args()
    out = build(args.refresh)
    SCENARIO_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT_PATH, index=False)
    digest = hashlib.sha256(OUT_PATH.read_bytes()).hexdigest()
    print(f"Wrote {len(out)} rows to {OUT_PATH} ({OUT_PATH.stat().st_size / 1e6:.2f} MB), sha256 {digest}")
    print(f"failed_24m rate: {out['failed_24m'].mean():.4f} ({out['failed_24m'].sum()} failures)")
    print(out.describe().T[["mean", "min", "50%", "max"]].to_string())


if __name__ == "__main__":
    main()
