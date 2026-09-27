#!/usr/bin/env python3
"""Feature engineering for the `titanic` tutorial scenario.

Turns the verbatim Kaggle "Titanic — Machine Learning from Disaster" training file
(`scenarios/titanic/sample_data/titanic_kaggle_train.csv`, 891 of the 2,224 people
aboard, as shipped in github.com/angelmtenor/data-science-keras) into the model-ready
`titanic.csv` that etl-tabular seeds. Every step here is one the scenario's Tutorial
tab explains — this file is the "Clean & engineer" chapter's source of truth:

- **Title** (Mr / Mrs / Miss / Master / Rare) parsed from `Name` — it encodes sex, age
  group (a "Master" is a boy) and marital status in one feature. Mlle/Ms -> Miss,
  Mme -> Mrs; everything else (Dr, Rev, Col, Lady, Countess, ...) -> Rare.
- **Age**: 177 of 891 are missing; each is filled with the median age of passengers
  sharing its Title *and* class (a missing "Master" in 3rd class becomes a child, not
  the overall median 28). Fitted on all 891 rows — a small, disclosed leak the tutorial
  points out.
- **TicketGroup**: how many passengers in this file share the same ticket (families,
  friends, servants travelling together).
- **FarePerPerson**: `Fare` is the price of the whole ticket, so it is divided by
  TicketGroup — otherwise a large family on a cheap ticket looks "rich".
- **Pclass / Embarked**: readable labels ("1st", "Southampton", ...). The 2 missing
  ports are filled with Southampton (the mode, and historically correct for both).
- **Dropped**: `Cabin` (77% missing, and recorded far more often for survivors — a
  label leak), `Ticket` (an identifier; its signal is kept as TicketGroup), `Fare`
  (replaced by FarePerPerson). `Name` is kept only as a display column, never a model
  input (see scenario.yaml's `dataset.display_columns`).

Run: `python scripts/prepare_titanic_dataset.py` (needs pandas — any service venv has it).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pandas as pd

SCENARIO_DIR = Path(__file__).resolve().parent.parent / "scenarios/titanic/sample_data"
RAW_PATH = SCENARIO_DIR / "titanic_kaggle_train.csv"
OUT_PATH = SCENARIO_DIR / "titanic.csv"
# The reviewed raw file — re-running on anything else must fail loudly, not silently
# produce a different training set.
RAW_SHA256 = "7d118fef8b6ccf7f81111877bc388536f7b1e498a655e3d649d19aaa010e9f6f"

COMMON_TITLES = ("Mr", "Mrs", "Miss", "Master")
TITLE_ALIASES = {"Mlle": "Miss", "Ms": "Miss", "Mme": "Mrs"}
CLASS_LABELS = {1: "1st", 2: "2nd", 3: "3rd"}
PORT_LABELS = {"S": "Southampton", "C": "Cherbourg", "Q": "Queenstown"}
OUTPUT_COLUMNS = [
    "PassengerId",
    "Name",
    "Pclass",
    "Sex",
    "Age",
    "Title",
    "SibSp",
    "Parch",
    "TicketGroup",
    "FarePerPerson",
    "Embarked",
    "Survived",
]


def extract_title(name: pd.Series) -> pd.Series:
    """'Braund, Mr. Owen Harris' -> 'Mr'; uncommon honorifics collapse into 'Rare'."""
    raw = name.str.extract(r",\s*([^.]+)\.", expand=False).str.strip().replace(TITLE_ALIASES)
    return raw.where(raw.isin(COMMON_TITLES), "Rare")


def prepare(raw: pd.DataFrame) -> pd.DataFrame:
    """Apply every documented feature-engineering step; return the model-ready frame."""
    df = raw.copy()
    df["Title"] = extract_title(df["Name"])
    df["TicketGroup"] = df.groupby("Ticket")["Ticket"].transform("count").astype(int)
    df["FarePerPerson"] = (df["Fare"] / df["TicketGroup"]).round(2)
    group_median_age = df.groupby(["Title", "Pclass"])["Age"].transform("median")
    df["Age"] = df["Age"].fillna(group_median_age).fillna(df["Age"].median()).round(1)
    df["Embarked"] = df["Embarked"].fillna(df["Embarked"].mode()[0]).map(PORT_LABELS)
    df["Pclass"] = df["Pclass"].map(CLASS_LABELS)
    out = df.loc[:, OUTPUT_COLUMNS]
    if out.isna().any().any():
        raise ValueError(f"Unexpected missing values after preparation:\n{out.isna().sum()}")
    return out


def main() -> None:
    digest = hashlib.sha256(RAW_PATH.read_bytes()).hexdigest()
    if digest != RAW_SHA256:
        raise SystemExit(f"{RAW_PATH} checksum {digest} != reviewed {RAW_SHA256}")
    out = prepare(pd.read_csv(RAW_PATH))
    out.to_csv(OUT_PATH, index=False)
    print(f"Wrote {len(out)} rows to {OUT_PATH}")
    print(out.describe(include="all").T[["count", "unique", "top", "min", "max"]].to_string())


if __name__ == "__main__":
    main()
