#!/usr/bin/env python3
"""Dataset + network preparation for the `aml_money_trail` scenario.

One public source, pinned by SHA-256 and cached under `~/.cache/ai-circus/aml/`: **IBM
"Transactions for Anti-Money Laundering" (AMLworld), HI-Small** (Altman et al., NeurIPS
2023, CDLA-Sharing-1.0) — a *synthetic* world of individuals, companies and banks whose
generator tracks illicit funds through every hop, so each transfer is labelled. The three
files come from a Hugging Face mirror pinned to a commit (`OsamaMIT/IBM-AML-HI-Small`):
`HI-Small_accounts.csv` (bank account -> holder), `HI-Small_Trans.csv` (5.08 M transfers)
and `HI-Small_Patterns.txt` (the laundering transfers that follow one of 8 typologies,
grouped by attempt).

The scenario frames the data as an **inter-bank transaction-monitoring utility** shared by
a consortium of member banks: it only ever sees the transfers that touch a member bank, so
laundering chains leave its view whenever they cross a non-member bank.

What this script does (and the scenario's README documents):

- **Cuts the window**: transfers dated after 2022-09-10 are dropped from features, labels
  and graph — the generator's tail after the primary period is dominated by laundering
  (~59 % vs 0.09 % overall), so keeping it would leak the label through timing.
- **Picks the consortium**: the banks with the most accounts, until their account holders
  number at least `MIN_HOLDERS`. A *row* is a holder (person or legal entity) with an
  account at a member bank; its features use every transfer between distinct holders that
  touches a member bank (holder-to-self moves — reinvestments — are left out).
- **Label** `laundering` = the holder sent or received at least one laundering transfer the
  consortium can see. Involvement, not guilt: receivers can be unwitting mules. Holders whose
  laundering only ever crossed non-member banks are counted and reported, not labelled.
- **Label-free features** (behaviour only): activity and value in USD (each currency's rate
  is the median implied by the dataset's own cross-currency transfers), pass-through ratio,
  dwell time, fan-in/fan-out, reciprocity, payment-format mix, cross-border share, night
  share, PageRank percentile and 3-hop return cycles. `--diagnostics` prints every
  feature's single-feature AUC to catch leaks.
- **Writes the graph** the Money Trail tab draws (`aml_money_trail_network.json`, see
  `ai_circus_shared.network_graph`): a *case slice* — ~60 laundering attempts covering all
  8 typologies with the holders they touch, plus legitimate holders they trade with — the
  banks (entities), holders outside the consortium (context), and per-hour USD flows per
  payment format.

Run: `cd services/training && uv run python ../../scripts/prepare_aml_money_trail_dataset.py [--diagnostics] [--ablation] [--print-countries]`
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import sparse

ROOT = Path(__file__).resolve().parent.parent
SCENARIO_DIR = ROOT / "scenarios/aml_money_trail/sample_data"
CSV_PATH = SCENARIO_DIR / "aml_money_trail.csv"
GRAPH_PATH = SCENARIO_DIR / "aml_money_trail_network.json"
CACHE_DIR = Path.home() / ".cache/ai-circus/aml"


@dataclass(frozen=True)
class Source:
    url: str
    sha256: str
    filename: str


_MIRROR = "https://huggingface.co/datasets/OsamaMIT/IBM-AML-HI-Small/resolve/95aa8c419e7c43eb1f3299901aab601889c6dd1b/"
ACCOUNTS = Source(_MIRROR + "HI-Small_accounts.csv", "786808526e33cfc441212dd6fccda7edfc24172149bed59c6ef59b186836b014", "HI-Small_accounts.csv")  # ruff: ignore[line-too-long]
TRANSFERS = Source(_MIRROR + "HI-Small_Trans.csv", "b19d39f515523373f991b689c07e11e7b0b95c17a2c27a87d91584ae16c5b040", "HI-Small_Trans.csv")  # ruff: ignore[line-too-long]
PATTERNS = Source(_MIRROR + "HI-Small_Patterns.txt", "2c546b5ce6009e73851f0139af053cf845f08bf92f3bc82fe1eb937dec2ef39b", "HI-Small_Patterns.txt")  # ruff: ignore[line-too-long]

WINDOW_START = pd.Timestamp("2022-09-01")
WINDOW_END = pd.Timestamp("2022-09-11")  # exclusive: the primary 10 days
HOURS = pd.date_range(WINDOW_START, WINDOW_END, freq="h", inclusive="left")  # 240 periods
MIN_HOLDERS = 12_000  # consortium size: the biggest bank of each country until this many holders
ATTEMPTS_PER_TYPOLOGY = 10  # the graph's case slice
MAX_ATTEMPT_ROWS, MAX_CONTEXT, SLICE_ROWS = 800, 500, 1000  # its node budget (schema cap: 2000 nodes)
ROW_TYPES = {"Individual": "Individual", "Sole Proprietorship": "Sole proprietorship", "Corporation": "Corporation", "Partnership": "Partnership"}  # ruff: ignore[line-too-long]
FORMATS = {"ACH": "ach", "Wire": "wire", "Cash": "cash", "Cheque": "cheque", "Credit Card": "credit_card", "Bitcoin": "bitcoin"}  # ruff: ignore[line-too-long]
TYPOLOGIES = {
    "FAN-OUT": "Fan-out", "FAN-IN": "Fan-in", "CYCLE": "Cycle", "BIPARTITE": "Bipartite",
    "SCATTER-GATHER": "Scatter-gather", "GATHER-SCATTER": "Gather-scatter", "STACK": "Stack", "RANDOM": "Random",
}  # fmt: skip
BTC_FALLBACK_USD = 20_000.0  # Sept 2022, only if the data implies no Bitcoin rate

# Where each bank country sits on the Money Trail map (label -> lat, lon of its financial centre).
COUNTRIES: dict[str, tuple[float, float]] = {
    "United States": (39.0, -98.0), "Germany": (50.1, 8.7), "Switzerland": (47.4, 8.5), "China": (31.2, 121.5),
    "France": (48.9, 2.35), "India": (19.1, 72.9), "Israel": (32.1, 34.8), "United Kingdom": (51.5, -0.1),
    "Italy": (45.5, 9.2), "Japan": (35.7, 139.7), "Spain": (40.4, -3.7), "Australia": (-33.9, 151.2),
    "Canada": (43.7, -79.4), "Russia": (55.8, 37.6), "Mexico": (19.4, -99.1), "Saudi Arabia": (24.7, 46.7),
    "Netherlands": (52.4, 4.9), "Brazil": (-23.5, -46.6), "Belgium": (50.8, 4.4), "Austria": (48.2, 16.4),
    "Greece": (37.98, 23.7), "Portugal": (38.7, -9.1), "Ireland": (53.3, -6.3), "Finland": (60.2, 24.9),
    "Slovakia": (48.1, 17.1), "Croatia": (45.8, 16.0), "Lithuania": (54.7, 25.3), "Slovenia": (46.05, 14.5),
    "Estonia": (59.4, 24.75), "Latvia": (56.95, 24.1), "Cyprus": (35.2, 33.4), "Malta": (35.9, 14.5),
    "Luxembourg": (49.6, 6.1), "Crypto exchanges": (-60.0, -30.0),  # drawn off-map, in the sea
}  # fmt: skip
COUNTRY_ALIASES = {"UK": "United Kingdom", "Crytpo": "Crypto exchanges"}  # IBM spells it "Crytpo"

FEATURES = [
    "holder_type", "accounts", "banks", "countries", "transfers_out", "transfers_in", "usd_out", "usd_in",
    "largest_transfer_usd", "pass_through", "dwell_hours", "counterparties_out", "counterparties_in",
    "max_daily_fan_out", "max_daily_fan_in", "reciprocity", "ach_share", "wire_share", "cash_share",
    "cheque_share", "card_share", "bitcoin_share", "cross_border_share", "currencies", "night_share",
    "peak_day_share", "pagerank_pctl", "return_cycles",
]  # fmt: skip
NUMERIC = [c for c in FEATURES if c != "holder_type"]


def fetch(source: Source) -> Path:
    """Download `source` into the cache once, then refuse anything whose SHA-256
    differs from the reviewed one.
    """
    path = CACHE_DIR / source.filename
    if not path.exists():
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        print(f"Downloading {source.url} …")
        partial = path.with_suffix(path.suffix + ".part")
        request = urllib.request.Request(source.url, headers={"User-Agent": "ai-circus-framework research script"})
        with urllib.request.urlopen(request, timeout=600) as response, partial.open("wb") as out:  # ruff: ignore[suspicious-url-open-usage]
            shutil.copyfileobj(response, out, length=1 << 20)
        partial.rename(path)
    digest = hashlib.sha256()
    with path.open("rb") as data:
        for block in iter(lambda: data.read(1 << 20), b""):
            digest.update(block)
    if digest.hexdigest() != source.sha256:
        raise SystemExit(f"{path} has SHA-256 {digest.hexdigest()}, expected {source.sha256} — refusing to use it.")
    return path


# --- loading ---------------------------------------------------------------------


def bank_country(bank_name: str) -> str:
    """"Spain Bank #439" -> "Spain"; the unnumbered US banks ("Arbor Savings Bank") -> "United States"."""
    match = re.fullmatch(r"(.+) Bank #\d+", bank_name)
    raw = match.group(1) if match else "United States"
    country = COUNTRY_ALIASES.get(raw, raw)
    if country not in COUNTRIES:
        raise SystemExit(f"Unknown bank country {country!r} (from {bank_name!r}) — add it to COUNTRIES.")
    return country


def account_key(bank: pd.Series, number: pd.Series) -> pd.Series:
    """"010" + "8000EBD30" -> "10|8000EBD30" (the transfer file zero-pads bank ids)."""
    return bank.astype(int).astype(str) + "|" + number


def load_accounts() -> pd.DataFrame:
    acc = pd.read_csv(fetch(ACCOUNTS), dtype=str)
    acc.columns = ["bank_name", "bank_id", "account", "holder", "holder_name"]
    acc["holder_type"] = acc["holder_name"].str.replace(r" #\d+$", "", regex=True)
    acc["country"] = acc["bank_name"].map(bank_country)
    # Eight account numbers exist at two banks each: an account is (bank, number).
    acc["key"] = account_key(acc["bank_id"], acc["account"])
    if acc["key"].duplicated().any():
        raise SystemExit("Accounts are not unique per (bank, number).")
    return acc.set_index("key")


def load_transfers(acc: pd.DataFrame) -> pd.DataFrame:
    """The window's transfers between distinct holders, with both holders and banks resolved
    through the accounts table.
    """
    cols = ["ts", "from_bank", "payer_acct", "to_bank", "payee_acct", "amt_recv", "cur_recv", "amt_paid", "cur_paid", "fmt", "laundering"]  # ruff: ignore[line-too-long]
    tx = pd.read_csv(fetch(TRANSFERS), header=0, names=cols, dtype={"from_bank": str, "to_bank": str, "payer_acct": str, "payee_acct": str})  # ruff: ignore[line-too-long]
    tx["ts"] = pd.to_datetime(tx["ts"], format="%Y/%m/%d %H:%M")
    total = len(tx)
    tail = tx[tx["ts"] >= WINDOW_END]
    print(f"Transfers: {total:,}; {len(tail):,} dated after {WINDOW_END.date()} dropped ({tail['laundering'].mean():.0%} of them laundering).")  # ruff: ignore[line-too-long]
    tx = tx[tx["ts"] < WINDOW_END].reset_index(drop=True)
    tx["payer_acct"] = account_key(tx.pop("from_bank"), tx["payer_acct"])
    tx["payee_acct"] = account_key(tx.pop("to_bank"), tx["payee_acct"])
    for side in ("payer", "payee"):
        tx[f"{side}_holder"] = tx[f"{side}_acct"].map(acc["holder"])
        tx[f"{side}_bank"] = tx[f"{side}_acct"].map(acc["bank_id"])
    if tx[["payer_holder", "payee_holder"]].isna().any().any():
        raise SystemExit("A transfer references an account missing from the accounts file.")
    return tx


def fx_to_usd(tx: pd.DataFrame) -> dict[str, float]:
    """USD value of one unit of each currency: the median rate implied by transfers that
    change currency (received / paid), directly against USD or, failing that, via another
    currency already priced.
    """
    cross = tx[tx["cur_paid"] != tx["cur_recv"]]
    rate: dict[tuple[str, str], float] = {}
    for (paid, recv), group in cross.groupby(["cur_paid", "cur_recv"]):
        rate[paid, recv] = float((group["amt_recv"] / group["amt_paid"]).median())
    usd = {"US Dollar": 1.0}
    for _ in range(4):
        for (paid, recv), value in rate.items():
            if recv in usd and paid not in usd and value > 0:
                usd[paid] = usd[recv] * value
            if paid in usd and recv not in usd and value > 0:
                usd[recv] = usd[paid] / value
    for currency in set(tx["cur_paid"]) - set(usd):
        if currency != "Bitcoin":
            raise SystemExit(f"No USD rate for {currency}.")
        usd[currency] = BTC_FALLBACK_USD
    return usd


def load_patterns() -> pd.DataFrame:
    """One row per patterned laundering transfer: its attempt number and typology."""
    rows: list[tuple[str, ...]] = []
    attempt, typology = 0, ""
    for line in fetch(PATTERNS).read_text().splitlines():
        if line.startswith("BEGIN LAUNDERING ATTEMPT"):
            attempt += 1
            typology = TYPOLOGIES[line.split("-", 1)[1].split(":")[0].strip().upper()]
        elif line and not line.startswith("END"):
            f = line.split(",")
            rows.append((f[0], f"{int(f[1])}|{f[2]}", f"{int(f[3])}|{f[4]}", f[7], f[9], f"A-{attempt:03d}", typology))
    return pd.DataFrame(rows, columns=["ts", "payer_acct", "payee_acct", "amt_paid", "fmt", "attempt", "typology"])


def pick_consortium(acc: pd.DataFrame) -> list[str]:
    """The biggest bank (most accounts) of each country, biggest first, until their holders
    reach MIN_HOLDERS — an international consortium, not just the largest US banks. Crypto
    exchanges are never members.
    """
    accounts = acc.reset_index()
    sizes = accounts.groupby(["country", "bank_id"]).size().rename("accounts").reset_index()
    sizes = sizes[sizes["country"] != "Crypto exchanges"].sort_values(["accounts", "bank_id"], ascending=[False, True])
    biggest = sizes.drop_duplicates("country")
    holders_of = accounts.groupby("bank_id")["holder"].agg(set)
    seen: set[str] = set()
    chosen: list[str] = []
    for bank in biggest["bank_id"]:
        seen |= holders_of[bank]
        chosen.append(bank)
        if len(seen) >= MIN_HOLDERS:
            break
    return chosen


# --- features --------------------------------------------------------------------


def percentile(series: pd.Series) -> pd.Series:
    return (series.rank(pct=True) * 100).round(1)


def pagerank(src: np.ndarray, dst: np.ndarray, weight: np.ndarray, n: int, damping: float = 0.85) -> np.ndarray:
    """Weighted PageRank by power iteration on a sparse matrix (scipy)."""
    w = sparse.csr_matrix((weight, (src, dst)), shape=(n, n))
    out = np.asarray(w.sum(axis=1)).ravel()
    inv = np.divide(1.0, out, out=np.zeros(n), where=out > 0)
    transition = sparse.diags(inv) @ w
    rank = np.full(n, 1.0 / n)
    for _ in range(60):
        dangling = rank[out == 0].sum()
        rank = damping * (transition.T @ rank + dangling / n) + (1 - damping) / n
    return rank


def holder_features(events: pd.DataFrame, rows: pd.Index, acc: pd.DataFrame) -> pd.DataFrame:
    """Behavioural features per row holder from the visible transfers `events` (columns:
    ts, payer, payee, fmt, usd, cur, payer_country, payee_country).
    """
    events = events.assign(day=events["ts"].dt.floor("D"), hour=events["ts"].dt.hour)
    out = events[events["payer"].isin(rows)]
    inn = events[events["payee"].isin(rows)]
    feats = pd.DataFrame(index=rows)
    account_rows = acc[acc["holder"].isin(rows)]
    feats["accounts"] = account_rows.groupby("holder").size()
    feats["banks"] = account_rows.groupby("holder")["bank_id"].nunique()
    feats["countries"] = account_rows.groupby("holder")["country"].nunique()
    feats["transfers_out"] = out.groupby("payer").size()
    feats["transfers_in"] = inn.groupby("payee").size()
    feats["usd_out"] = out.groupby("payer")["usd"].sum()
    feats["usd_in"] = inn.groupby("payee")["usd"].sum()
    largest = pd.concat([out.rename(columns={"payer": "holder"})[["holder", "usd"]], inn.rename(columns={"payee": "holder"})[["holder", "usd"]]])  # ruff: ignore[line-too-long]
    feats["largest_transfer_usd"] = largest.groupby("holder")["usd"].max()
    feats = feats.fillna(0.0)
    hi, lo = feats[["usd_in", "usd_out"]].max(axis=1), feats[["usd_in", "usd_out"]].min(axis=1)
    feats["pass_through"] = np.where(hi > 0, lo / hi.where(hi > 0, 1), 0.0)

    # Dwell: hours from each incoming transfer to the holder's next outgoing one.
    arrivals = inn[["payee", "ts"]].rename(columns={"payee": "holder", "ts": "arrived"}).sort_values("arrived")
    departures = out[["payer", "ts"]].rename(columns={"payer": "holder", "ts": "left"}).sort_values("left")
    joined = pd.merge_asof(arrivals, departures, left_on="arrived", right_on="left", by="holder", direction="forward")
    joined["hours"] = (joined["left"] - joined["arrived"]).dt.total_seconds() / 3600
    feats["dwell_hours"] = joined.groupby("holder")["hours"].median()
    feats["dwell_hours"] = feats["dwell_hours"].fillna(-1.0).round(2)  # -1: money never moved on

    feats["counterparties_out"] = out.groupby("payer")["payee"].nunique()
    feats["counterparties_in"] = inn.groupby("payee")["payer"].nunique()
    feats["max_daily_fan_out"] = out.groupby(["payer", "day"])["payee"].nunique().groupby("payer").max()
    feats["max_daily_fan_in"] = inn.groupby(["payee", "day"])["payer"].nunique().groupby("payee").max()
    pairs_out = out[["payer", "payee"]].drop_duplicates().rename(columns={"payer": "holder", "payee": "other"})
    pairs_in = inn[["payee", "payer"]].drop_duplicates().rename(columns={"payee": "holder", "payer": "other"})
    both = pairs_out.merge(pairs_in, on=["holder", "other"]).groupby("holder").size()
    ties = pd.concat([pairs_out, pairs_in]).drop_duplicates().groupby("holder").size()
    feats["reciprocity"] = (both / ties).reindex(rows)

    touching = pd.concat([out.rename(columns={"payer": "holder"}), inn.rename(columns={"payee": "holder"})])
    n_touch = touching.groupby("holder").size()
    for fmt, column in (("ACH", "ach_share"), ("Wire", "wire_share"), ("Cash", "cash_share"), ("Cheque", "cheque_share"), ("Credit Card", "card_share"), ("Bitcoin", "bitcoin_share")):  # ruff: ignore[line-too-long]
        feats[column] = touching[touching["fmt"] == fmt].groupby("holder").size() / n_touch
    feats["cross_border_share"] = touching[touching["payer_country"] != touching["payee_country"]].groupby("holder").size() / n_touch  # ruff: ignore[line-too-long]
    feats["currencies"] = touching.groupby("holder")["cur"].nunique()
    feats["night_share"] = touching[touching["hour"] < 6].groupby("holder").size() / n_touch
    daily = touching.groupby(["holder", "day"])["usd"].sum()
    feats["peak_day_share"] = daily.groupby("holder").max() / daily.groupby("holder").sum()
    feats = feats.fillna(0.0)

    # Network position over every visible holder (rows and outsiders alike).
    ids = pd.Index(pd.concat([events["payer"], events["payee"]]).unique())
    src, dst = ids.get_indexer(events["payer"]), ids.get_indexer(events["payee"])
    rank = pd.Series(pagerank(src, dst, np.log1p(events["usd"].to_numpy()), len(ids)), index=ids)
    feats["pagerank_pctl"] = percentile(rank).reindex(rows).fillna(0.0)
    adjacency = sparse.csr_matrix((np.ones(len(src)), (src, dst)), shape=(len(ids), len(ids)))
    adjacency.data[:] = 1.0
    picked = ids.get_indexer(rows)
    known = picked >= 0
    cycles = np.zeros(len(rows))
    closed = (adjacency[picked[known]] @ adjacency).multiply(adjacency.T[picked[known]])
    cycles[known] = np.asarray(closed.sum(axis=1)).ravel()
    feats["return_cycles"] = np.minimum(cycles, 50)
    return feats


# --- graph -----------------------------------------------------------------------


def build_graph(
    dataset: pd.DataFrame, events: pd.DataFrame, acc: pd.DataFrame, consortium: list[str], patterns: pd.DataFrame
) -> dict[str, object]:
    """The case slice: attempts of every typology, the holders they touch, and the legitimate
    holders those trade with; banks as entities, outsiders as context, hourly flows per format.
    """
    rng = np.random.default_rng(42)
    names = acc.drop_duplicates("holder").set_index("holder")["holder_name"]
    types = acc.drop_duplicates("holder").set_index("holder")["holder_type"]
    home_country = acc.groupby("holder")["country"].agg(lambda s: s.value_counts().index[0])
    rows = set(dataset.index)

    # Attempts with at least two consortium holders, drawn at random per typology and added
    # round-robin while the slice stays within its node budget.
    attempt_holders = patterns.groupby("attempt")["holders"].agg(lambda s: set().union(*s))
    attempt_type = patterns.groupby("attempt")["typology"].first()
    queues = {}
    for typology in TYPOLOGIES.values():
        eligible = sorted(a for a in attempt_type.index[attempt_type == typology] if 2 <= len(attempt_holders[a] & rows) <= 40)  # ruff: ignore[line-too-long]
        queues[typology] = [str(a) for a in rng.permutation(eligible)[:ATTEMPTS_PER_TYPOLOGY]]
    slice_holders: set[str] = set()
    for turn in range(ATTEMPTS_PER_TYPOLOGY):
        for typology in TYPOLOGIES.values():
            if turn < len(queues[typology]):
                grown = slice_holders | attempt_holders[queues[typology][turn]]
                if len(grown & rows) <= MAX_ATTEMPT_ROWS and len(grown - rows) <= MAX_CONTEXT:
                    slice_holders = grown

    # Legitimate consortium holders those trade with: biggest partners first.
    volume = events.groupby(["payer", "payee"])["usd"].sum()
    partners: dict[str, float] = {}
    for (payer, payee), usd in volume.items():
        for mine, other in ((payer, payee), (payee, payer)):
            if mine in slice_holders and other in rows and other not in slice_holders:
                partners[other] = partners.get(other, 0.0) + usd
    budget = SLICE_ROWS - len(slice_holders & rows)
    for holder in sorted(partners, key=lambda h: (-partners[h], h))[: max(budget, 0)]:
        slice_holders.add(holder)
    slice_rows = sorted(slice_holders & rows)
    context = sorted(slice_holders - rows)

    node_set = set(slice_rows) | set(context)
    visible = events[events["payer"].isin(node_set) & events["payee"].isin(node_set)].copy()
    visible["kind"] = visible["fmt"].map(FORMATS)
    visible = visible.dropna(subset=["kind"])
    visible["hour"] = visible["ts"].dt.strftime("%Y-%m-%dT%H")
    grouped = visible.groupby(["payer", "payee", "kind"])
    totals = grouped["usd"].sum().sort_values(ascending=False, kind="stable").head(14_000)
    per_hour = visible.groupby(["payer", "payee", "kind", "hour"])["usd"].sum()
    main_currency = grouped["cur"].agg(lambda s: s.value_counts().index[0])

    primary_country = acc[acc["holder"].isin(context)].groupby("holder")["country"].agg(lambda s: s.value_counts().index[0])
    nodes: list[dict[str, object]] = []
    for holder in slice_rows:
        nodes.append({"id": holder, "kind": "row", "label": names[holder], "type": types[holder], "group": dataset.loc[holder, "home_country"]})  # ruff: ignore[line-too-long]
    for holder in context:
        nodes.append({"id": holder, "kind": "context", "label": names[holder], "type": types[holder], "group": primary_country[holder]})  # ruff: ignore[line-too-long]

    holds = acc[acc["holder"].isin(slice_rows) & acc["bank_id"].isin(consortium)].groupby(["holder", "bank_id"]).size()
    bank_info = acc.drop_duplicates("bank_id").set_index("bank_id")[["bank_name", "country"]]
    for bank in consortium:
        info = bank_info.loc[bank]
        nodes.append({"id": f"bank-{bank}", "kind": "entity", "label": info["bank_name"], "type": info["country"], "group": info["country"], "description": "Member bank of the consortium: the monitoring utility sees every transfer that touches it."})  # ruff: ignore[line-too-long]

    edges: list[dict[str, object]] = []
    for (holder, bank), n_accounts in holds.items():
        edges.append({"source": holder, "target": f"bank-{bank}", "kind": "holds", "weight": float(n_accounts), "label": "account holder at"})  # ruff: ignore[line-too-long]
    for (payer, payee, kind), usd in totals.items():
        series = per_hour.loc[payer, payee, kind]
        edges.append({
            "source": payer, "target": payee, "kind": kind, "weight": round(float(usd), 2), "label": str(main_currency[payer, payee, kind]),  # ruff: ignore[line-too-long]
            "series": {str(h): round(float(v), 2) for h, v in series.items() if v > 0},
        })  # fmt: skip
    return {"version": 1, "periods": [t.strftime("%Y-%m-%dT%H") for t in HOURS], "nodes": nodes, "edges": edges}


# --- main ------------------------------------------------------------------------


def prepare() -> tuple[pd.DataFrame, dict[str, object]]:
    acc = load_accounts()
    tx = load_transfers(acc)
    usd = fx_to_usd(tx)
    print("USD per unit:", {k: round(v, 4) for k, v in sorted(usd.items())})
    patterns = load_patterns()
    patterns = patterns[pd.to_datetime(patterns["ts"], format="%Y/%m/%d %H:%M") < WINDOW_END]
    key = ["ts", "payer_acct", "payee_acct", "amt_paid", "fmt"]
    tx["ts_key"] = tx["ts"].dt.strftime("%Y/%m/%d %H:%M")
    tx["amt_key"] = tx["amt_paid"].map("{:.2f}".format)
    patterns = patterns.assign(amt_paid=patterns["amt_paid"].astype(float).map("{:.2f}".format))
    matched = tx.merge(
        patterns.rename(columns={"ts": "ts_key", "amt_paid": "amt_key"})[["ts_key", "payer_acct", "payee_acct", "amt_key", "fmt", "attempt", "typology"]].drop_duplicates(["ts_key", "payer_acct", "payee_acct", "amt_key", "fmt"]),  # ruff: ignore[line-too-long]
        on=["ts_key", "payer_acct", "payee_acct", "amt_key", "fmt"], how="left",
    )  # fmt: skip
    if matched["attempt"].notna().sum() != len(patterns):
        raise SystemExit(f"Matched {matched['attempt'].notna().sum()} of {len(patterns)} pattern transfers.")
    tx = matched.drop(columns=["ts_key", "amt_key"])

    consortium = pick_consortium(acc)
    member = set(consortium)
    tx["visible"] = tx["payer_bank"].isin(member) | tx["payee_bank"].isin(member)
    tx = tx[tx["payer_holder"] != tx["payee_holder"]]
    print(f"Consortium: {len(consortium)} banks; {tx['visible'].sum():,} of {len(tx):,} inter-holder transfers touch a member bank.")  # ruff: ignore[line-too-long]
    tx["usd"] = tx["amt_paid"] * tx["cur_paid"].map(usd)
    tx["payer_country"] = tx["payer_acct"].map(acc["country"])
    tx["payee_country"] = tx["payee_acct"].map(acc["country"])
    seen = tx[tx["visible"]]
    events = seen.rename(columns={"payer_holder": "payer", "payee_holder": "payee", "cur_paid": "cur"})[
        ["ts", "payer", "payee", "fmt", "usd", "cur", "payer_country", "payee_country", "laundering", "attempt", "typology"]
    ]  # fmt: skip
    print("Formats between distinct holders:", events["fmt"].value_counts().to_dict())

    member_accounts = acc[acc["bank_id"].isin(member) & acc["holder_type"].isin(ROW_TYPES)]
    rows = pd.Index(sorted(member_accounts["holder"].unique()), name="holder_id")
    feats = holder_features(events, rows, acc)
    feats.insert(0, "holder_type", acc.drop_duplicates("holder").set_index("holder")["holder_type"].map(ROW_TYPES).reindex(rows))  # ruff: ignore[line-too-long]

    laundering = events[events["laundering"] == 1]
    involved = pd.concat([laundering["payer"], laundering["payee"]]).unique()
    label = pd.Series(rows.isin(involved).astype(int), index=rows)
    all_laundering = tx[tx["laundering"] == 1]
    everyone = set(all_laundering["payer_holder"]) | set(all_laundering["payee_holder"])
    hidden = len(set(rows) & everyone) - int(label.sum())
    print(f"Rows: {len(rows):,} holders ({dict(feats['holder_type'].value_counts())}); {int(label.sum())} positives ({label.mean():.1%}); {hidden} more launder only through transfers the consortium cannot see (not labelled).")  # ruff: ignore[line-too-long]

    attempts = events.dropna(subset=["attempt"])
    first = attempts.sort_values("ts")
    per_holder = pd.concat([first[["payer", "attempt", "typology"]].rename(columns={"payer": "holder"}), first[["payee", "attempt", "typology"]].rename(columns={"payee": "holder"})]).drop_duplicates("holder")  # ruff: ignore[line-too-long]
    per_holder = per_holder.set_index("holder").reindex(rows)
    home = member_accounts.groupby("holder").agg(home_bank=("bank_name", "first"), home_country=("country", lambda s: s.value_counts().index[0]))  # ruff: ignore[line-too-long]
    out = pd.DataFrame(index=rows)
    out["name"] = acc.drop_duplicates("holder").set_index("holder")["holder_name"].reindex(rows)
    out["home_bank"], out["home_country"] = home["home_bank"], home["home_country"]
    out["typology"] = per_holder["typology"].fillna(pd.Series(np.where(label == 1, "Unpatterned", "—"), index=rows))
    out["attempt_id"] = per_holder["attempt"].fillna("—")
    out = pd.concat([out, feats[FEATURES]], axis=1)
    out["laundering"] = label
    out[NUMERIC] = out[NUMERIC].astype(float)
    for column in ("usd_out", "usd_in", "largest_transfer_usd"):
        out[column] = out[column].round(2)
    for column in NUMERIC:
        if column not in ("usd_out", "usd_in", "largest_transfer_usd"):
            out[column] = out[column].round(4)

    patterns_in = patterns.merge(pd.DataFrame({"payer_acct": acc.index, "h": acc["holder"].to_numpy()}), on="payer_acct").rename(columns={"h": "h1"})  # ruff: ignore[line-too-long]
    patterns_in = patterns_in.merge(pd.DataFrame({"payee_acct": acc.index, "h": acc["holder"].to_numpy()}), on="payee_acct").rename(columns={"h": "h2"})  # ruff: ignore[line-too-long]
    patterns_in["holders"] = [{a, b} for a, b in zip(patterns_in["h1"], patterns_in["h2"], strict=True)]
    return out, build_graph(out, events, acc, consortium, patterns_in)


def diagnostics(out: pd.DataFrame) -> None:
    """Single-feature ROC AUC for every numeric feature (0.5 = no signal, far from it = look for a leak)."""
    from sklearn.metrics import roc_auc_score

    scores = {c: roc_auc_score(out["laundering"], out[c]) for c in NUMERIC}
    print("\nSingle-feature ROC AUC (distance from 0.5 = signal):")
    for column, auc in sorted(scores.items(), key=lambda kv: -abs(kv[1] - 0.5)):
        print(f"  {column:22s} {auc:.3f}")
    print(pd.crosstab(out["holder_type"], out["laundering"], normalize="index").round(4).to_string())
    print(pd.crosstab(out["typology"], out["holder_type"]).to_string())


def ablation(out: pd.DataFrame) -> None:
    """Repeated 5-fold CV ROC AUC / PR AUC with training's own pipeline, for the README's table."""
    from sklearn.model_selection import RepeatedStratifiedKFold, cross_validate

    from training.core.training import CANDIDATE_ESTIMATORS, build_pipeline

    cv = RepeatedStratifiedKFold(n_splits=5, n_repeats=3, random_state=0)
    shares = ["ach_share", "wire_share", "cash_share", "cheque_share", "card_share", "bitcoin_share"]
    groups = [
        ("Everything (what the scenario trains on)", FEATURES),
        ("Without the payment-format mix (ACH artefact)", [c for c in FEATURES if c not in shares]),
        ("Behaviour of the flows only (no profile, no format mix, no network)", ["transfers_out", "transfers_in", "usd_out", "usd_in", "largest_transfer_usd", "pass_through", "dwell_hours", "counterparties_out", "counterparties_in", "max_daily_fan_out", "max_daily_fan_in", "reciprocity", "night_share", "peak_day_share"]),  # ruff: ignore[line-too-long]
        ("Network position only", ["pagerank_pctl", "return_cycles"]),
    ]  # fmt: skip
    print("\n| Inputs | Model | ROC AUC | PR AUC |\n| --- | --- | --- | --- |")
    for label, columns in groups:
        for model in ("logistic_regression", "lightgbm"):
            estimator = CANDIDATE_ESTIMATORS["classification"][model]()
            numeric = [c for c in columns if c != "holder_type"]
            categorical = ["holder_type"] if "holder_type" in columns else []
            pipeline = build_pipeline(numeric, categorical, estimator, [])
            res = cross_validate(pipeline, out, out["laundering"], cv=cv, scoring=["roc_auc", "average_precision"])
            print(f"| {label} | {model} | {res['test_roc_auc'].mean():.3f} ± {res['test_roc_auc'].std():.3f} | {res['test_average_precision'].mean():.3f} ± {res['test_average_precision'].std():.3f} |")  # ruff: ignore[line-too-long]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--diagnostics", action="store_true", help="print single-feature AUCs and crosstabs")
    parser.add_argument("--ablation", action="store_true", help="print the README's CV comparison table")
    parser.add_argument("--print-countries", action="store_true", help="print the scenario.yaml `countries` block")
    args = parser.parse_args()
    if args.print_countries:
        for label, (lat, lon) in COUNTRIES.items():
            print(f'    - {{key: "{label}", lat: {lat}, lon: {lon}}}')
        return

    out, graph = prepare()
    SCENARIO_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(CSV_PATH, index_label="holder_id")
    GRAPH_PATH.write_text(json.dumps(graph, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    written = pd.read_csv(CSV_PATH, index_col="holder_id")
    if written[NUMERIC].isna().any().any() or int(written["laundering"].sum()) != int(out["laundering"].sum()):
        raise SystemExit(f"{CSV_PATH} does not round-trip.")
    for path in (CSV_PATH, GRAPH_PATH):
        print(f"Wrote {path.relative_to(ROOT)} ({path.stat().st_size:,} bytes, sha256 {hashlib.sha256(path.read_bytes()).hexdigest()})")  # ruff: ignore[line-too-long]
    kinds = pd.Series([n["kind"] for n in graph["nodes"]]).value_counts().to_dict()  # type: ignore[union-attr]
    edge_kinds = pd.Series([e["kind"] for e in graph["edges"]]).value_counts().to_dict()  # type: ignore[union-attr]
    points = sum(len(e.get("series", {})) for e in graph["edges"])  # type: ignore[union-attr]
    print(f"Graph: nodes {kinds}, edges {edge_kinds}, {points:,} series points.")
    print(out[NUMERIC].describe().T.round(3).to_string())
    if args.diagnostics:
        diagnostics(out)
    if args.ablation:
        ablation(out)


if __name__ == "__main__":
    main()
