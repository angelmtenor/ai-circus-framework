#!/usr/bin/env python3
"""Dataset preparation for the `toxic_leadership` tutorial scenario.

Source: Kaggle "Glassdoor Job Reviews" by David Gauthier (CC BY-SA 4.0) — 838,566
anonymous employee reviews of UK employers, 2008-2021, each with 1-5 star sub-ratings
and free-text headline / pros / cons. The raw file (~290 MB) is never committed: it is
downloaded from a pinned Hugging Face mirror of the Kaggle file (or read from `--raw`,
e.g. a zip you downloaded from Kaggle yourself) and checked against the reviewed
SHA-256 before anything else runs. Every step below is one the scenario's Tutorial tab
explains — this file is the "Clean, anonymise, de-leak" chapter's source of truth:

- **Engineers only**: job titles are matched (first match wins) into five JobFamily
  groups — Data & AI, DevOps & Cloud, QA & Test, Leads & Architects, Software
  Engineering; every other role is dropped.
- **Target** `BadLeadership` = the review's *Senior Management* rating is 1-2 stars.
- **Anonymised**: the employer column is dropped and replaced by a coarse
  `EmployerType` sector; employer names (the reviewed firm's own and a list of
  well-known companies) are masked as "[company]" inside the text and job title,
  public executives and HQ towns that would give the employer away as "[name]";
  e-mails and URLs are removed. The model must learn what the words say about
  leadership — not which company wrote them.
- **De-leaked**: the overall rating, CEO approval, "would recommend" and business
  outlook answers are dropped (near-restatements of the target), and so are the
  culture and career ratings — a *halo effect*: someone who rates leadership 1 star
  rates culture 1 star too, and a model given both stops reading the text. Only the
  pay and work-life-balance ratings are kept, as controls: is it the money, the
  hours — or the manager?
- **Review** = headline + ". " + cons, whitespace-normalised and cut at 1,000
  characters; exact duplicate reviews are dropped.
- **Sample**: 4,000 reviews stratified by JobFamily with a fixed seed (Data & AI is
  deliberately over-sampled relative to its share of engineering reviews).

The derived CSV is a CC BY-SA 4.0 adaptation — see `sample_data/README.md`.

Run: `python scripts/prepare_toxic_leadership_dataset.py [--raw PATH] [--ablation]`
with a service venv (pandas; `--ablation` runs training's own pipeline — use
`cd services/training && uv run python ../../scripts/prepare_toxic_leadership_dataset.py --ablation`).
"""

from __future__ import annotations

import argparse
import hashlib
import io
import re
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

SCENARIO_DIR = Path(__file__).resolve().parent.parent / "scenarios/toxic_leadership/sample_data"
OUT_PATH = SCENARIO_DIR / "toxic_leadership.csv"
CACHE_PATH = Path.home() / ".cache/ai-circus/glassdoor_job_reviews.zip"
# A pinned revision of the Hugging Face mirror of the Kaggle file.
RAW_URL = (
    "https://huggingface.co/datasets/lallantop/glassdoor/resolve/"
    "e1da091bd4c84ee6d5db8f911b2a88b3cbdde32a/archive%20(1).zip"
)
CSV_NAME = "glassdoor_reviews.csv"
# The reviewed raw CSV — re-running on anything else must fail loudly, not silently
# produce a different training set (checked on the CSV, so a Kaggle zip works too).
CSV_SHA256 = "9273f0ba1a4643ccd4fbd47ac2aa016b6d46c2c3444dc3f0039d6f1d20644387"

SEED = 42
MAX_REVIEW_CHARS = 1000
# Reviews per JobFamily in the published sample (4,000 in total).
QUOTAS = {
    "Software Engineering": 1600,
    "Data & AI": 1000,
    "DevOps & Cloud": 500,
    "Leads & Architects": 500,
    "QA & Test": 400,
}
# First match wins, in this order (a "Lead Data Scientist" is Data & AI, not a lead).
JOB_FAMILIES = {
    "Data & AI": r"data scien|machine learning|\bml\b|\bai\b|artificial intel|deep learning|data engineer"
    r"|data analyst|research scientist|\bnlp\b|computer vision",
    "DevOps & Cloud": r"devops|site reliability|\bsre\b|cloud engineer|platform engineer|infrastructure engineer"
    r"|systems engineer",
    "QA & Test": r"test engineer|\bsdet\b|qa engineer|quality assurance engineer|automation engineer",
    "Leads & Architects": r"engineering manager|tech(?:nical)? lead|solutions? architect|software architect"
    r"|head of engineering|head of data|principal engineer",
    "Software Engineering": r"software|developer|programmer|full ?stack|front ?end|back ?end|web developer"
    r"|mobile engineer",
}
SECTORS = {
    "Tech & internet": "Google Apple Microsoft Facebook LinkedIn Booking-com Indeed Unity-Technologies FARFETCH ASOS "
    "YOOX-NET-A-PORTER-GROUP Babylon-Health",
    "Enterprise IT": "IBM Oracle SAP Salesforce Workday Blue-Yonder Sage Cisco-Systems Dynatrace Wipro Capita "
    "The-Access-Group-UK Bullhorn Equiniti Iron-Mountain-Inc Serco-Group",
    "Banking & finance": "J-P-Morgan HSBC-Holdings Barclays Citi Goldman-Sachs BNY-Mellon American-Express Mastercard "
    "BNP-Paribas Morgan-Stanley Deutsche-Bank Lloyds-Banking-Group Aviva Capital-Group",
    "Consulting": "Deloitte EY PwC KPMG Accenture McKinsey-and-Company Boston-Consulting-Group Mercer Aon "
    "Willis-Towers-Watson CBRE Adecco People-Group",
    "Media & telecom": "Thomson-Reuters Bloomberg-L-P Vodafone BT Sky BBC Pearson",
}
FIRM_SECTOR = {firm: sector for sector, firms in SECTORS.items() for firm in firms.split()}
OTHER_SECTOR = "Other industries"
# Well-known employers masked in every review (case-insensitive, whole words), on top
# of each review's own employer. Ambiguous everyday words are matched case-sensitively.
KNOWN_COMPANIES = (
    "Google Alphabet Microsoft MSFT Facebook Meta LinkedIn Amazon AWS IBM Oracle Salesforce Deloitte PwC KPMG "
    "Accenture McKinsey BCG Capgemini Cognizant Infosys Wipro TCS HCL JPMorgan JPMC Goldman HSBC Barclays Lloyds "
    "Citi Citibank Citigroup Amex Mastercard Bloomberg Reuters Vodafone Capita Tesco Sainsbury Sainsburys "
    "AstraZeneca GSK GlaxoSmithKline Unilever Roche Bayer Workday Dynatrace Farfetch ASOS Serco Equiniti Pearson"
).split()
# Multi-word names and spellings the per-firm variants miss.
KNOWN_COMPANIES += [
    "J.P Morgan", "J.P. Morgan", "JP Morgan", "JPMorgan Chase", "Morgan Stanley", "Goldman Sachs", "Thomson Reuters",
    "Deutsche Bank", "Lloyds Banking", "American Express", "Blue Yonder", "Booking.com", "Big Blue",
]
CASE_SENSITIVE_COMPANIES = ("Apple", "SAP", "EY", "BT", "Sky", "Sage", "Indeed", "Next", "Aon", "Citi", "MSR")
# Public executives and headquarters towns that would give the employer away.
GIVEAWAYS = [
    "Satya", "Nadella", "Bill Gates", "Ballmer", "Steve Jobs", "Tim Cook", "Sundar", "Pichai", "Zuckerberg", "Bezos",
    "Jassy", "Larry Ellison", "Benioff", "Ginni", "Rometty", "Arvind Krishna", "Jamie Dimon", "Redmond", "Cupertino",
    "Armonk", "Mountain View",
]
TENURE_LABELS = {
    "less than 1 year": "Under 1 year",
    "more than 1 year": "1+ years",
    "more than 3 years": "3+ years",
    "more than 5 years": "5+ years",
    "more than 8 years": "8+ years",
    "more than 10 years": "10+ years",
}
OUTPUT_COLUMNS = [
    "ReviewId",
    "JobTitle",
    "JobFamily",
    "EmployerType",
    "EmployeeStatus",
    "Tenure",
    "ReviewYear",
    "CompBenefits",
    "WorkLifeBalance",
    "Review",
    "BadLeadership",
]
URL_OR_EMAIL = re.compile(r"\S+@\S+\.\S+|https?://\S+|www\.\S+", re.IGNORECASE)


def read_raw(raw: Path | None) -> pd.DataFrame:
    """The verified raw CSV — from `raw` (a .zip or .csv) or the cached/pinned download."""
    if raw is None:
        if not CACHE_PATH.exists():
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            print(f"Downloading {RAW_URL} -> {CACHE_PATH}")
            urllib.request.urlretrieve(RAW_URL, CACHE_PATH)  # noqa: S310 (pinned https URL)
        raw = CACHE_PATH
    data = raw.read_bytes()
    if zipfile.is_zipfile(io.BytesIO(data)):
        data = zipfile.ZipFile(io.BytesIO(data)).read(CSV_NAME)
    digest = hashlib.sha256(data).hexdigest()
    if digest != CSV_SHA256:
        raise SystemExit(f"{raw}: {CSV_NAME} checksum {digest} != reviewed {CSV_SHA256}")
    return pd.read_csv(io.BytesIO(data))


def job_family(title: pd.Series) -> pd.Series:
    """Job title -> JobFamily (None for a non-engineering role)."""
    lowered = title.fillna("").str.strip().str.lower()
    family = pd.Series(None, index=title.index, dtype=object)
    for name, pattern in JOB_FAMILIES.items():
        family = family.where(family.notna() | ~lowered.str.contains(pattern, regex=True), name)
    return family


def _word_pattern(names: list[str], *, ignore_case: bool, suffixes: bool = False) -> re.Pattern[str]:
    """Whole-word alternation; `_` counts as a boundary ("IBM_Review"), and with
    `suffixes` so do demonyms like "IBMers" / "Googley" / "Googlers"."""
    alternatives = sorted({re.escape(n) for n in names if n}, key=len, reverse=True)
    tail = r"(?:ers?|ites?|y|s)?" if suffixes else ""
    return re.compile(
        rf"(?<![A-Za-z0-9])(?:{'|'.join(alternatives)}){tail}(?![A-Za-z0-9])", re.IGNORECASE if ignore_case else 0
    )


def firm_variants(firm: str) -> list[str]:
    """'J-P-Morgan' -> ['J-P-Morgan', 'J P Morgan', 'JPMorgan', 'J.P. Morgan', ...]."""
    parts = firm.split("-")
    spaced = " ".join(parts)
    variants = {firm, spaced, spaced.replace(" and ", " & "), "".join(parts)}
    if len(parts) > 1 and all(len(p) == 1 for p in parts[:-1]):  # J-P-Morgan / Bloomberg-L-P style initials
        variants |= {"".join(parts[:-1]) + " " + parts[-1], ".".join(parts[:-1]) + ". " + parts[-1]}
    if parts[-1] in {"com", "Inc", "Group", "Holdings", "Systems", "Technologies", "UK", "L", "P", "s"}:
        variants.add(" ".join(p for p in parts[:-1]))
    return [v for v in variants if len(v) >= 2]


def anonymise(text: pd.Series, firm: pd.Series) -> pd.Series:
    """Mask each row's own employer plus well-known companies as "[company]"; drop URLs/e-mails."""
    known = _word_pattern(KNOWN_COMPANIES, ignore_case=True, suffixes=True)
    known_exact = _word_pattern(list(CASE_SENSITIVE_COMPANIES), ignore_case=False)
    giveaways = _word_pattern(GIVEAWAYS, ignore_case=False)
    own = {
        f: _word_pattern(firm_variants(f), ignore_case=f not in CASE_SENSITIVE_COMPANIES, suffixes=True)
        for f in firm.unique()
    }

    def mask(t: str, f: str) -> str:
        t = own[f].sub("[company]", URL_OR_EMAIL.sub("", t))
        t = known_exact.sub("[company]", known.sub("[company]", t))
        return giveaways.sub("[name]", t)

    masked = [mask(t, f) for t, f in zip(text, firm, strict=True)]
    return pd.Series(masked, index=text.index)


def normalise(text: pd.Series) -> pd.Series:
    """Collapse whitespace/newlines/bullets into single spaces."""
    return text.fillna("").str.replace(r"[\s••·]+", " ", regex=True).str.strip()


def build_review(headline: pd.Series, cons: pd.Series) -> pd.Series:
    """'headline. cons', cut at MAX_REVIEW_CHARS on a word boundary."""
    head, body = normalise(headline), normalise(cons)
    joined = head.where(head.str.contains(r"[.!?]$") | (head == ""), head + ".") + " " + body
    joined = joined.str.strip()
    too_long = joined.str.len() > MAX_REVIEW_CHARS
    joined[too_long] = joined[too_long].str.slice(0, MAX_REVIEW_CHARS - 1).str.replace(r"\s+\S*$", "", regex=True) + "…"
    return joined


def mostly_english(text: pd.Series) -> pd.Series:
    """Drop reviews that are mostly non-ASCII letters (a few non-English reviews)."""
    letters = text.str.count(r"[^\W\d_]")
    ascii_letters = text.str.count(r"[A-Za-z]")
    return ascii_letters >= 0.95 * letters.clip(lower=1)


def prepare(raw: pd.DataFrame) -> pd.DataFrame:
    """Apply every documented step; return the model-ready 4,000-review frame."""
    df = raw.copy()
    df["ReviewId"] = df.index + 1  # stable: row number in the pinned raw file
    df["JobFamily"] = job_family(df["job_title"])
    df = df[df["JobFamily"].notna() & df["senior_mgmt"].notna() & df["cons"].notna()]
    df = df[df["comp_benefits"].notna() & df["work_life_balance"].notna()]

    df["Review"] = build_review(anonymise(df["headline"].fillna(""), df["firm"]), anonymise(df["cons"], df["firm"]))
    df = df[mostly_english(df["Review"]) & (df["Review"].str.len() >= 3)]
    df = df.drop_duplicates(subset="Review")
    df["JobTitle"] = anonymise(normalise(df["job_title"]), df["firm"])
    df["EmployerType"] = df["firm"].map(FIRM_SECTOR).fillna(OTHER_SECTOR)
    df["EmployeeStatus"] = df["current"].str.startswith("Former").map({True: "Former", False: "Current"})
    tenure = df["current"].str.extract(rf"({'|'.join(TENURE_LABELS)})", expand=False)
    df["Tenure"] = tenure.map(TENURE_LABELS).fillna("Not stated")
    df["ReviewYear"] = df["date_review"].str.slice(0, 4).astype(int)
    df["CompBenefits"] = df["comp_benefits"].astype(int)
    df["WorkLifeBalance"] = df["work_life_balance"].astype(int)
    df["BadLeadership"] = (df["senior_mgmt"] <= 2).astype(int)

    sample = pd.concat(
        df[df["JobFamily"] == family].sample(n, random_state=SEED) for family, n in QUOTAS.items()
    ).sort_values("ReviewId")
    out = sample.loc[:, OUTPUT_COLUMNS].reset_index(drop=True)
    if out.isna().any().any():
        raise ValueError(f"Unexpected missing values after preparation:\n{out.isna().sum()}")
    # Kept aside for --ablation only (never written out): the halo ratings.
    out.attrs["halo"] = sample[["culture_values", "career_opp"]].reset_index(drop=True)
    return out


def ablation(out: pd.DataFrame) -> None:
    """5-fold CV ROC AUC of the tutorial's comparison table, with training's own
    pipeline (`lightgbm_small_data`: TF-IDF step, one-hot, scaling) — run with the
    training venv so `training.core.training` is importable."""
    import numpy as np
    from sklearn.model_selection import StratifiedKFold, cross_val_score
    from training.core.training import CANDIDATE_ESTIMATORS, build_pipeline

    data = pd.concat([out, out.attrs["halo"]], axis=1)
    categorical = ["JobFamily", "EmployerType", "EmployeeStatus", "Tenure"]
    for column in categorical:
        data[column] = data[column].astype("category")
    numeric = ["ReviewYear", "CompBenefits", "WorkLifeBalance"]
    halo = ["culture_values", "career_opp"]

    def score(numeric_cols: list[str], categorical_cols: list[str], text: bool, model: str = "lightgbm_small_data") -> str:
        estimator = CANDIDATE_ESTIMATORS["classification"][model]()
        pipeline = build_pipeline(numeric_cols, categorical_cols, estimator, ["Review"] if text else [])
        cv = StratifiedKFold(5, shuffle=True, random_state=0)
        scores = cross_val_score(pipeline, data, data["BadLeadership"], cv=cv, scoring="roc_auc")
        return f"{np.mean(scores):.3f} ± {np.std(scores):.3f}"

    print("\n| Inputs (LightGBM, small-data settings) | 5-fold CV ROC AUC |\n| --- | --- |")
    print(f"| Job context + pay + hours (no text) | {score(numeric, categorical, False)} |")
    print(f"| Review text only | {score([], [], True)} |")
    print(f"| Everything we use (context + text) | {score(numeric, categorical, True)} |")
    print(f"| + culture & career ratings (halo) | {score(numeric + halo, categorical, True)} |")
    print(f"| Everything, default LightGBM settings | {score(numeric, categorical, True, 'lightgbm')} |")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--raw", type=Path, help="a Kaggle-downloaded zip/csv instead of the pinned download")
    parser.add_argument("--ablation", action="store_true", help="print the tutorial's CV comparison table")
    args = parser.parse_args()

    out = prepare(read_raw(args.raw))
    SCENARIO_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT_PATH, index=False)
    print(f"Wrote {len(out)} rows to {OUT_PATH} ({OUT_PATH.stat().st_size / 1e6:.2f} MB)")
    print(f"BadLeadership rate: {out['BadLeadership'].mean():.3f}")
    print(out.describe(include="all").T[["count", "unique", "top", "min", "max"]].to_string())
    if args.ablation:
        ablation(out)


if __name__ == "__main__":
    main()
