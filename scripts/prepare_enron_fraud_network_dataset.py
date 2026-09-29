#!/usr/bin/env python3
"""Dataset + network preparation for the `enron_fraud_network` scenario.

Three public sources, each pinned by SHA-256 and cached under `~/.cache/ai-circus/enron/`:

- **Insider pay** — the canonical "ud120" Enron financial dataset (a Python dict pickle
  derived from *In re Enron Corp.*, Case No. 01-16034, "Payments to Insiders", Exhibit
  3b.2), from the maintainer's MIT-licensed `angelmtenor/data-science-keras` repo. It is
  only unpickled after its checksum matches (and it holds plain dicts only). One row per
  person; `poi` = the 18 persons of interest of that dataset (indicted, settled with the
  SEC, or testified in exchange for immunity).
- **E-mail** — the FERC/CMU Enron e-mail corpus (517,401 messages) via a pinned Hugging
  Face parquet mirror (`corbt/enron-emails`). Only the `from`/`to`/`cc`/`date` columns
  are ever read — no subject or body is loaded, let alone stored.
- **Powers Report** — *Report of Investigation by the Special Investigative Committee of
  the Board of Directors of Enron Corp.* (February 1, 2002; SEC EDGAR, Enron Form 8-K
  exhibit 99.2). Every curated entity relation below carries a short verbatim quote that
  must be found in that text; the printed page it sits on becomes the citation.

What this script does (and the scenario's README documents):

- **Cleans the pay table**: drops `TOTAL` (the spreadsheet's sum row), `THE TRAVEL AGENCY
  IN THE PARK` (a company, kept as a graph entity instead) and `LOCKHART EUGENE E` (no
  data at all); fixes the two rows ud120 shifted by one column (`BELFER ROBERT`,
  `BHATNAGAR SANJAY`, re-read from the PDF) and then checks every row's payment and
  stock components add up to their totals. A dash in the PDF means "no payment": 0.
- **De-leaks**: ud120's e-mail counts with persons of interest
  (`from_poi_to_this_person`, `from_this_person_to_poi`, `shared_receipt_with_poi`) are
  computed *from the label* — they are dropped and replaced by label-free network
  features. Whether a person's mailbox is in the corpus (FERC chose whose mailboxes to
  seize) is never a feature either.
- **Builds the company e-mail network** (networkx): messages de-duplicated across
  mailbox folders, dated 1999-01..2002-06, internal addresses only, broadcasts (> 50
  recipients) left out of the ties; a *tie* is a pair who e-mailed each other both
  ways. Per person: e-mails sent/received, contacts, PageRank and betweenness
  percentiles, clustering, crisis-period share, after-hours share.
- **Writes the graph** the Network tab draws (`enron_network.json`, see
  `ai_circus_shared.network_graph`): every person, the ~100 anonymised colleagues who
  connect them most, the curated entities and their cited relations, and monthly
  e-mail volumes per tie for the timeline.

Run: `cd services/training && uv run --with networkx python ../../scripts/prepare_enron_fraud_network_dataset.py [--ablation]`
(`--ablation` prints the README's CV comparison with training's own pipeline).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import pickletools
import re
import shutil
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
SCENARIO_DIR = ROOT / "scenarios/enron_fraud_network/sample_data"
CSV_PATH = SCENARIO_DIR / "enron_fraud_network.csv"
GRAPH_PATH = SCENARIO_DIR / "enron_network.json"
CACHE_DIR = Path.home() / ".cache/ai-circus/enron"


@dataclass(frozen=True)
class Source:
    url: str
    sha256: str
    filename: str


FINANCIAL = Source(
    "https://raw.githubusercontent.com/angelmtenor/data-science-keras/"
    "74c105befd21e21b5a1555b3f3c2c9ce6f9568c0/data/enron_financial_data.pkl",
    "da4440bbae952d8acfa569cb786289737bb8a0ef27d333bd13275fe8fb4d6ed8",
    "enron_financial_data.pkl",
)
_EMAILS = "https://huggingface.co/datasets/corbt/enron-emails/resolve/cfc06c758093d90993abce1a43668fb7357258a6/data/"
EMAIL_SHARDS = [
    Source(_EMAILS + "train-00000-of-00003.parquet", "97165e587875e3e57715c14f63547fedbf664f748de1124122d4d2952c56a9fc", "train-00000-of-00003.parquet"),  # noqa: E501
    Source(_EMAILS + "train-00001-of-00003.parquet", "c14be60e6c1be0c7a80d20dc8a6769d587988ba5260368868f7944d83a364e7a", "train-00001-of-00003.parquet"),  # noqa: E501
    Source(_EMAILS + "train-00002-of-00003.parquet", "79d1b6b9d73461289a84aecdbfbbf1a242fedb21c57a28b57ea07a2d171e1cf7", "train-00002-of-00003.parquet"),  # noqa: E501
]
POWERS = Source(
    "https://www.sec.gov/Archives/edgar/data/0001024401/000090951802000089/big.txt",
    "f4fd5ac59a7d7089ce36536e50ce0516060c6c4c73c169aeb79657c240d63e31",
    "powers_report.txt",
)
POWERS_CITE = "Powers Report (Special Investigative Committee of Enron's Board, Feb 1, 2002), p. {page}"
INSIDER_PAY_CITE = "In re Enron Corp., No. 01-16034, Payments to Insiders (Exhibit 3b.2), note (j)"

PAYMENTS = [
    "salary", "bonus", "long_term_incentive", "deferred_income", "deferral_payments",
    "loan_advances", "other", "expenses", "director_fees",
]  # fmt: skip
STOCK = ["exercised_stock_options", "restricted_stock", "restricted_stock_deferred"]
MONEY = [*PAYMENTS, "total_payments", *STOCK, "total_stock_value"]
LEAKY_EMAIL = ["to_messages", "from_messages", "from_poi_to_this_person", "from_this_person_to_poi", "shared_receipt_with_poi"]  # fmt: skip
RATIOS = ["bonus_to_salary", "exercised_share", "stock_to_cash"]
NETWORK = [
    "emails_sent", "emails_received", "distinct_contacts", "pagerank_pctl", "betweenness_pctl",
    "clustering", "crisis_activity_share", "after_hours_share",
]  # fmt: skip
FEATURES = [*MONEY, *RATIOS, *NETWORK]

# ud120 shifted these two rows by one column; values re-read from the insider-pay PDF.
CORRECTIONS: dict[str, dict[str, float]] = {
    "BELFER ROBERT": {
        "deferred_income": -102500, "deferral_payments": 0, "expenses": 3285, "director_fees": 102500,
        "total_payments": 3285, "exercised_stock_options": 0, "restricted_stock": 44093,
        "restricted_stock_deferred": -44093, "total_stock_value": 0,
    },
    "BHATNAGAR SANJAY": {
        "other": 0, "expenses": 137864, "director_fees": 0, "total_payments": 137864,
        "exercised_stock_options": 15456290, "restricted_stock": 2604490,
        "restricted_stock_deferred": -2604490, "total_stock_value": 15456290,
    },
}  # fmt: skip
DROPPED_ROWS = ["TOTAL", "THE TRAVEL AGENCY IN THE PARK", "LOCKHART EUGENE E"]

# Titles as the Powers Report states them (quote -> verified on load); everyone else is
# a non-employee director (paid director fees, footnote 9 of the schedule) or a senior
# employee (the schedule lists insiders only).
ROLES: dict[str, tuple[str, str]] = {
    "LAY KENNETH L": ("Chairman and CEO", "Kenneth Lay (who was Chairman and CEO)"),
    "SKILLING JEFFREY K": ("President and COO, later CEO", "Jeffrey Skilling, the President and COO (and later CEO)"),
    "FASTOW ANDREW S": ("Executive Vice President and CFO", "Andrew S. Fastow, Enron's former Executive Vice President and Chief Financial Officer"),  # noqa: E501
    "CAUSEY RICHARD A": ("Chief Accounting Officer", "Richard Causey, the Chief Accounting Officer"),
    "BUY RICHARD B": ("Chief Risk Officer", "Richard Buy, the Chief Risk Officer"),
    "GLISAN JR BEN F": ("Treasurer (from May 2000)", "In May 2000, Glisan succeeded McMahon as Treasurer of Enron"),
    "MCMAHON JEFFREY": ("Treasurer (until May 2000)", "Jeffrey McMahon, Enron's Treasurer (who reported to Fastow)"),
    "KOPPER MICHAEL J": ("Finance (reported to the CFO)", "Michael Kopper, who worked for Fastow in the Finance area"),
    "MORDAUNT KRISTINA M": ("In-house lawyer, Structured Finance", "She was involved in the initial Rhythms transaction as General Counsel, Structured Finance"),  # noqa: E501
    "DERRICK JR. JAMES V": ("General Counsel", "James Derrick, Enron's General Counsel"),
    "WHALLEY LAWRENCE G": ("Chief Operating Officer (2001)", "Greg Whalley (Enron's COO)"),
    "RICE KENNETH D": ("CEO, Enron Broadband Services", "Ken Rice, the CEO of EBS"),
    "KAMINSKI WINCENTY J": ("Head of the Research Group", "Vincent Kaminski, head of Enron's Research Group"),
    "JAEDICKE ROBERT": ("Director, chair of the Audit & Compliance Committee", "Robert Jaedicke, the Chairman of the Audit and Compliance Committee"),  # noqa: E501
    "DUNCAN JOHN H": ("Director, chair of the Executive Committee", "John Duncan, Chairman of the Executive Committee"),
    "WINOKUR JR. HERBERT S": ("Director, Finance Committee", "Herbert S. Winokur, Jr., was a member of the Board of Directors and the Finance Committee"),  # noqa: E501
}  # fmt: skip

# Curated entities. Relations are (person_id | entity id, entity id, label, quote): the
# quote must appear verbatim (modulo whitespace) in the Powers Report.
ENTITIES = [
    ("jedi", "JEDI", "Joint venture", "Joint Energy Development Investments, L.P. — a $500 million Enron / CalPERS investment partnership (1993), kept off Enron's balance sheet because control was shared."),  # noqa: E501
    ("chewco", "Chewco", "Special-purpose entity", "Chewco Investments, L.P. — formed in 1997 to buy CalPERS' stake in JEDI. It never had the 3% independent equity the accounting rules required, so JEDI should have been consolidated: part of Enron's November 2001 restatement."),  # noqa: E501
    ("ljm1", "LJM1", "Related-party partnership", "LJM Cayman, L.P. — formed in June 1999, with the CFO as general partner, to 'hedge' Enron's investment in Rhythms NetConnections."),  # noqa: E501
    ("ljm2", "LJM2", "Related-party partnership", "LJM2 Co-Investment, L.P. — a larger CFO-managed private-equity fund (late 1999) that transacted with Enron and provided the outside equity of the Raptors."),  # noqa: E501
    ("talon", "Talon (Raptor I)", "Special-purpose entity", "Raptor I's vehicle, funded mostly with Enron's own stock, used to offset losses on Enron's merchant investments (2000)."),  # noqa: E501
    ("timberwolf", "Timberwolf (Raptor II)", "Special-purpose entity", "Raptor II's vehicle — the same Enron-stock-funded 'hedge' structure as Talon."),  # noqa: E501
    ("porcupine", "Porcupine (Raptor III)", "Special-purpose entity", "Raptor III's vehicle, funded with Enron's holding in The New Power Company."),
    ("bobcat", "Bobcat (Raptor IV)", "Special-purpose entity", "Raptor IV's vehicle — the last Raptor, set up in September 2000."),
    ("southampton", "Southampton Place", "Investment partnership", "Southampton Place, L.P. — a partnership through which the CFO's family foundation and several Enron employees invested in the unwinding of the Rhythms hedge (March 2000), with extraordinary returns."),  # noqa: E501
    ("andersen", "Arthur Andersen", "Auditor", "Enron's outside auditor, which also advised on the structure and accounting of the related-party transactions."),  # noqa: E501
    ("travel_agency", "The Travel Agency in the Park", "Supplier", "A travel agency (later Alliance Worldwide) that Enron employees paid for business travel — $362,096 on the insider-pay schedule."),  # noqa: E501
]  # fmt: skip
RELATIONS = [
    ("chewco", "jedi", "Bought CalPERS' stake in JEDI (1997)", "Fastow explained that Chewco would purchase CalPERS' interest in JEDI"),
    ("KOPPER MICHAEL J", "chewco", "Manager and owner of Chewco's general partner", "was the manager and owner of Chewco's general partner"),  # noqa: E501
    ("FASTOW ANDREW S", "chewco", "Presented Chewco to the Board's Executive Committee (1997)", "Fastow explained that Chewco would purchase CalPERS' interest in JEDI"),  # noqa: E501
    ("FASTOW ANDREW S", "ljm1", "General partner", "Fastow disclosed that he would serve as the general partner of LJM1"),
    ("FASTOW ANDREW S", "ljm2", "Managing member of LJM2's general partner", "The general partner was LJM2 Capital Management, LLC, of which Fastow was the managing member"),  # noqa: E501
    ("KOPPER MICHAEL J", "ljm2", "Limited partner (via Big Doe); bought the CFO's interest (July 2001)", "In July 2001, Kopper resigned from Enron and purchased Fastow's interest in LJM2"),  # noqa: E501
    ("LAY KENNETH L", "ljm1", "Chaired the June 1999 Board meeting that approved LJM1", "At a Board meeting on June 28, 1999, Lay called on Skilling, who in turn called on Fastow, to present the proposal"),  # noqa: E501
    ("SKILLING JEFFREY K", "ljm1", "Presented LJM1 to the Board (June 1999)", "At a Board meeting on June 28, 1999, Lay called on Skilling, who in turn called on Fastow, to present the proposal"),  # noqa: E501
    ("CAUSEY RICHARD A", "ljm1", "Required reviewer of every LJM transaction", "review and approval of all LJM transactions by Richard Causey, the Chief Accounting Officer"),  # noqa: E501
    ("CAUSEY RICHARD A", "ljm2", "Required reviewer of every LJM transaction", "review and approval of all LJM transactions by Richard Causey, the Chief Accounting Officer"),  # noqa: E501
    ("BUY RICHARD B", "ljm1", "Required reviewer of every LJM transaction", "and Richard Buy, the Chief Risk Officer"),
    ("BUY RICHARD B", "ljm2", "Required reviewer of every LJM transaction", "and Richard Buy, the Chief Risk Officer"),
    ("MCMAHON JEFFREY", "ljm2", "Raised concerns about the LJM dealings with the President (March 2000)", "McMahon told us that he approached Skilling with serious concerns about Enron's dealings with the LJM partnerships"),  # noqa: E501
    ("ljm2", "talon", "Provided the outside equity", "LJM2 provided the outside equity designed to avoid consolidation of the Raptor SPEs"),
    ("ljm2", "timberwolf", "Provided the outside equity", "LJM2 provided the outside equity designed to avoid consolidation of the Raptor SPEs"),
    ("ljm2", "porcupine", "Provided the outside equity", "LJM2 provided the outside equity designed to avoid consolidation of the Raptor SPEs"),
    ("ljm2", "bobcat", "Provided the outside equity", "LJM2 provided the outside equity designed to avoid consolidation of the Raptor SPEs"),
    ("GLISAN JR BEN F", "talon", "Negotiated for Enron; signed the approval documents", "Enron approval documents show Glisan as the \"business unit originator\" and \"person negotiating for Enron\" in the Raptor I, II, and IV transactions"),  # noqa: E501
    ("GLISAN JR BEN F", "timberwolf", "Negotiated for Enron; signed the approval documents", "Enron approval documents show Glisan as the \"business unit originator\" and \"person negotiating for Enron\" in the Raptor I, II, and IV transactions"),  # noqa: E501
    ("GLISAN JR BEN F", "bobcat", "Negotiated for Enron; signed the approval documents", "Enron approval documents show Glisan as the \"business unit originator\" and \"person negotiating for Enron\" in the Raptor I, II, and IV transactions"),  # noqa: E501
    ("SKILLING JEFFREY K", "bobcat", "Signed the LJM2 approval sheet for Raptor IV (March 2001)", "Skilling signed the LJM2 Approval Sheet for Raptor IV"),  # noqa: E501
    ("FASTOW ANDREW S", "southampton", "Invested $25,000 through a family foundation; received $4.5 million", "In exchange for a $25,000 investment, Fastow received (through a family foundation) $4.5 million in approximately two months"),  # noqa: E501
    ("GLISAN JR BEN F", "southampton", "Limited partner ($5,800 contribution)", "$5,800 each for Glisan and Mordaunt"),
    ("MORDAUNT KRISTINA M", "southampton", "Limited partner ($5,800 contribution)", "$5,800 each for Glisan and Mordaunt"),
    ("KOPPER MICHAEL J", "southampton", "Signed the partnership agreement (as a member of Big Doe)", "Kopper signed the agreement as a member of Big Doe"),  # noqa: E501
    ("andersen", "chewco", "Reviewed the transaction (billed $80,000, 1997)", "Andersen billed Enron $80,000 in connection with its 1997 review of the Chewco transaction"),  # noqa: E501
    ("andersen", "talon", "Participated in structuring and accounting of the Raptors", "Andersen participated in the structuring and accounting treatment of the Raptor transactions"),  # noqa: E501
    ("andersen", "ljm2", "Billed $5.7 million for advice on the LJM and Chewco transactions", "Andersen billed Enron $5.7 million for advice in connection with the LJM and Chewco transactions"),  # noqa: E501
]  # fmt: skip
# Not from the Powers Report: the insider-pay schedule's own footnote.
TRAVEL_RELATION = ("LAY KENNETH L", "travel_agency", "Co-owned by the sister of Enron's former Chairman", INSIDER_PAY_CITE)

PERIODS = pd.period_range("1999-01", "2002-06", freq="M")
CRISIS = (pd.Timestamp("2001-08-01", tz="America/Chicago"), pd.Timestamp("2002-01-01", tz="America/Chicago"))
MAX_RECIPIENTS = 50  # a message to more people than this is a broadcast, not a tie
MIN_EDGE_EMAILS = 3  # the Network tab draws pairs with at least this many messages
MAX_EDGES = 3000
CONTEXT_NODES = 100


def fetch(source: Source) -> Path:
    """Download `source` into the cache once, then refuse anything whose SHA-256
    differs from the reviewed one."""
    path = CACHE_DIR / source.filename
    if not path.exists():
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        print(f"Downloading {source.url} …")
        partial = path.with_suffix(path.suffix + ".part")
        request = urllib.request.Request(source.url, headers={"User-Agent": "ai-circus-framework research script"})
        with urllib.request.urlopen(request, timeout=600) as response, partial.open("wb") as out:  # noqa: S310
            shutil.copyfileobj(response, out, length=1 << 20)
        partial.rename(path)
    digest = hashlib.sha256()
    with path.open("rb") as data:
        for block in iter(lambda: data.read(1 << 20), b""):
            digest.update(block)
    if digest.hexdigest() != source.sha256:
        raise SystemExit(f"{path} has SHA-256 {digest.hexdigest()}, expected {source.sha256} — refusing to use it.")
    return path


# --- insider pay -----------------------------------------------------------------


def load_plain_pickle(data: bytes) -> object:
    """Unpickle `data` only if it references no global at all (no class, no function —
    plain dicts, strings and numbers), so loading it can never run code."""
    for opcode, argument, _ in pickletools.genops(data):
        if opcode.name in {"GLOBAL", "STACK_GLOBAL", "INST", "OBJ", "REDUCE", "NEWOBJ", "NEWOBJ_EX", "EXT1", "EXT2", "EXT4"}:
            raise SystemExit(f"Refusing to unpickle: opcode {opcode.name} {argument!r}.")
    return pickle.loads(data)  # noqa: S301 — checksum-pinned and opcode-checked above


def load_financial() -> pd.DataFrame:
    raw = load_plain_pickle(fetch(FINANCIAL).read_bytes())
    df = pd.DataFrame.from_dict(raw, orient="index").replace("NaN", np.nan)
    df = df.drop(index=DROPPED_ROWS)
    df[MONEY + LEAKY_EMAIL] = df[MONEY + LEAKY_EMAIL].astype(float).fillna(0.0)
    for person, values in CORRECTIONS.items():
        for column, value in values.items():
            df.loc[person, column] = value
    bad_payments = (df[PAYMENTS].sum(axis=1) - df["total_payments"]).abs() > 1
    bad_stock = (df[STOCK].sum(axis=1) - df["total_stock_value"]).abs() > 1
    if bad_payments.any() or bad_stock.any():
        raise SystemExit(f"Totals don't add up for {sorted(df.index[bad_payments | bad_stock])}.")
    df["poi"] = df["poi"].astype(bool).astype(int)
    if len(df) != 143 or df["poi"].sum() != 18:
        raise SystemExit(f"Expected 143 people / 18 persons of interest, got {len(df)} / {df['poi'].sum()}.")
    df["email_address"] = df["email_address"].where(df["email_address"].notna(), None)
    df.index.name = "person_id"
    return df.sort_index()


def display_name(person_id: str) -> str:
    """"LAY KENNETH L" -> "Kenneth L. Lay"; "GLISAN JR BEN F" -> "Ben F. Glisan Jr."."""
    parts = person_id.replace(".", "").split()
    last, rest = parts[0].title(), parts[1:]
    if last.startswith("Mc") and len(last) > 2:  # MCMAHON -> McMahon
        last = "Mc" + last[2:].title()
    suffix = ""
    if rest and rest[0] in {"JR", "SR", "II", "III"}:
        suffix, rest = f" {rest[0].title()}.", rest[1:]
    given = " ".join(p.title() + ("." if len(p) == 1 else "") for p in rest)
    return f"{given} {last}{suffix}".strip()


def roles(df: pd.DataFrame, powers: str) -> pd.Series:
    role = pd.Series("Senior employee", index=df.index)
    role[(df["director_fees"] > 0) & (df["salary"] == 0)] = "Non-employee director"
    for person, (title, quote) in ROLES.items():
        powers_page(powers, quote)  # raises if the quote isn't in the report
        role[person] = title
    return role


# --- Powers Report ---------------------------------------------------------------


def load_powers() -> str:
    return fetch(POWERS).read_text(encoding="latin-1")


def powers_page(powers: str, quote: str) -> int:
    """The printed page number a verbatim `quote` sits on (each page of the EDGAR text
    ends with its number just before a `<PAGE>` marker)."""
    pattern = r"\s+".join(re.escape(word) for word in quote.split())
    match = re.search(pattern, powers)
    if match is None:
        raise SystemExit(f"Quote not found in the Powers Report: {quote!r}")
    for marker in re.finditer(r"(\d+)\s*<PAGE>", powers):
        if marker.start() > match.start():
            return int(marker.group(1))
    raise SystemExit(f"No page marker after quote {quote!r}")


# --- e-mail ----------------------------------------------------------------------


def load_emails() -> pd.DataFrame:
    """One row per distinct message (the corpus stores a message once per folder it
    sits in): sender, recipients (to + cc), local timestamp; 1999-01..2002-06 only."""
    frames = [pq.read_table(fetch(shard), columns=["from", "to", "cc", "date"]).to_pandas() for shard in EMAIL_SHARDS]
    mail = pd.concat(frames, ignore_index=True)
    mail["sender"] = mail["from"].str.strip().str.lower()
    mail["recipients"] = [
        tuple(sorted({a.strip().lower() for a in [*to, *cc] if a and a.strip()}))
        for to, cc in zip(mail["to"], mail["cc"], strict=True)
    ]
    mail["date"] = mail["date"].dt.tz_convert("America/Chicago")
    mail = mail.drop_duplicates(subset=["sender", "date", "recipients"])
    start, end = PERIODS[0].start_time.tz_localize("America/Chicago"), PERIODS[-1].end_time.tz_localize("America/Chicago")
    mail = mail[(mail["date"] >= start) & (mail["date"] <= end)]
    return mail[["sender", "recipients", "date"]].reset_index(drop=True)


def internal(address: str) -> bool:
    return address.endswith("@enron.com")


def company_ties(mail: pd.DataFrame) -> pd.DataFrame:
    """Directed internal (sender, recipient, month) message counts, broadcasts excluded."""
    ties = mail[mail["recipients"].map(len).between(1, MAX_RECIPIENTS) & mail["sender"].map(internal)]
    ties = ties.explode("recipients").rename(columns={"recipients": "recipient"})
    ties = ties[ties["recipient"].map(internal) & (ties["recipient"] != ties["sender"])]
    ties["period"] = ties["date"].dt.tz_localize(None).dt.to_period("M").astype(str)
    return ties.groupby(["sender", "recipient", "period"]).size().rename("emails").reset_index()


def reciprocal_graph(ties: pd.DataFrame) -> nx.DiGraph:
    """Directed, weighted graph of *mutual* correspondents: A->B kept only if B->A exists."""
    totals = ties.groupby(["sender", "recipient"])["emails"].sum()
    pairs = set(totals.index)
    graph = nx.DiGraph()
    graph.add_weighted_edges_from((s, r, float(w)) for (s, r), w in totals.items() if (r, s) in pairs)
    return graph


def percentile(values: dict[str, float]) -> dict[str, float]:
    series = pd.Series(values, dtype=float)
    return (series.rank(pct=True) * 100).round(1).to_dict()


def network_features(df: pd.DataFrame, mail: pd.DataFrame, graph: nx.DiGraph) -> pd.DataFrame:
    undirected = graph.to_undirected()
    pagerank = percentile(nx.pagerank(graph, weight="weight"))
    k = min(1000, undirected.number_of_nodes())
    betweenness = percentile(nx.betweenness_centrality(undirected, k=k, seed=0))
    clustering = nx.clustering(undirected)
    sent = mail.groupby("sender").size()
    received = mail.explode("recipients").groupby("recipients").size()
    in_crisis = mail[(mail["date"] >= CRISIS[0]) & (mail["date"] < CRISIS[1])].groupby("sender").size()
    local = mail["date"]
    off_hours = (local.dt.hour < 7) | (local.dt.hour >= 20) | (local.dt.dayofweek >= 5)
    after_hours = mail[off_hours].groupby("sender").size()

    rows = {}
    for person, address in df["email_address"].items():
        n_sent = int(sent.get(address, 0)) if address else 0
        rows[person] = {
            "emails_sent": n_sent,
            "emails_received": int(received.get(address, 0)) if address else 0,
            "distinct_contacts": undirected.degree(address) if address in undirected else 0,
            "pagerank_pctl": pagerank.get(address, 0.0),
            "betweenness_pctl": betweenness.get(address, 0.0),
            "clustering": round(clustering.get(address, 0.0), 4),
            "crisis_activity_share": round(in_crisis.get(address, 0) / n_sent, 4) if n_sent else 0.0,
            "after_hours_share": round(after_hours.get(address, 0) / n_sent, 4) if n_sent else 0.0,
        }
    return pd.DataFrame.from_dict(rows, orient="index")[NETWORK]


# --- dataset ---------------------------------------------------------------------


def money_ratios(df: pd.DataFrame) -> pd.DataFrame:
    salary, stock, cash = df["salary"], df["total_stock_value"], df["total_payments"]
    return pd.DataFrame(
        {
            "bonus_to_salary": np.where(salary > 0, df["bonus"] / salary.where(salary > 0, 1), 0.0).clip(0, 20),
            "exercised_share": np.where(stock > 0, df["exercised_stock_options"] / stock.where(stock > 0, 1), 0.0).clip(0, 1),  # noqa: E501
            "stock_to_cash": np.where(cash > 0, stock / cash.where(cash > 0, 1), 0.0).clip(0, 200),
        },
        index=df.index,
    ).round(4)


# --- graph -----------------------------------------------------------------------


def build_graph(
    df: pd.DataFrame, names: pd.Series, ties: pd.DataFrame, graph: nx.DiGraph, powers: str
) -> dict[str, object]:
    person_of = {address: person for person, address in df["email_address"].items() if address}
    undirected = graph.to_undirected()

    # Context: the colleagues (not in the dataset) with the strongest mutual ties to people who are.
    strength: dict[str, float] = {}
    reach: dict[str, int] = {}
    for address in person_of:
        if address not in undirected:
            continue
        for neighbour, data in undirected[address].items():
            if neighbour in person_of:
                continue
            strength[neighbour] = strength.get(neighbour, 0.0) + data["weight"]
            reach[neighbour] = reach.get(neighbour, 0) + 1
    candidates = sorted((a for a in strength if reach[a] >= 2), key=lambda a: (-strength[a], a))[:CONTEXT_NODES]
    context_id = {address: f"ctx-{i + 1:03d}" for i, address in enumerate(sorted(candidates))}
    node_of = {**person_of, **context_id}

    pair_ties = ties[ties["sender"].isin(node_of) & ties["recipient"].isin(node_of)].copy()
    pair_ties["source"] = pair_ties["sender"].map(node_of)
    pair_ties["target"] = pair_ties["recipient"].map(node_of)
    totals = pair_ties.groupby(["source", "target"])["emails"].sum()
    totals = totals[totals >= MIN_EDGE_EMAILS].sort_values(ascending=False).head(MAX_EDGES)
    series = pair_ties.groupby(["source", "target", "period"])["emails"].sum()

    drawn = nx.Graph()
    drawn.add_nodes_from(node_of.values())
    for (source, target), weight in totals.items():
        prior = drawn.get_edge_data(source, target, {"weight": 0})["weight"]
        drawn.add_edge(source, target, weight=prior + weight)
    communities = nx.community.louvain_communities(drawn, weight="weight", seed=42)
    communities = sorted((c for c in communities if len(c) >= 3), key=lambda c: (-len(c), min(c)))
    group_of = {node: f"Community {chr(65 + i)}" for i, c in enumerate(communities[:12]) for node in c}

    nodes: list[dict[str, object]] = [
        {"id": person, "kind": "row", "label": names[person], **({"group": group_of[person]} if person in group_of else {})}
        for person in df.index
    ]
    nodes += [
        {"id": node, "kind": "context", "label": "Unscored colleague", **({"group": group_of[node]} if node in group_of else {})}
        for node in sorted(context_id.values())
    ]
    nodes += [
        {"id": key, "kind": "entity", "label": label, "type": kind, "description": description}
        for key, label, kind, description in ENTITIES
    ]
    edges: list[dict[str, object]] = []
    for (source, target), weight in totals.items():
        per_month = series.loc[(source, target)]
        edges.append(
            {
                "source": source,
                "target": target,
                "kind": "email",
                "weight": float(weight),
                "series": {str(period): float(n) for period, n in per_month.items() if n > 0},
            }
        )
    for source, target, label, quote in RELATIONS:
        edges.append(
            {
                "source": source,
                "target": target,
                "kind": "role",
                "label": label,
                "citation": POWERS_CITE.format(page=powers_page(powers, quote)),
            }
        )
    source, target, label, citation = TRAVEL_RELATION
    edges.append({"source": source, "target": target, "kind": "role", "label": label, "citation": citation})
    return {"version": 1, "periods": [str(p) for p in PERIODS], "nodes": nodes, "edges": edges}


# --- main ------------------------------------------------------------------------


def prepare() -> tuple[pd.DataFrame, dict[str, object]]:
    df = load_financial()
    powers = load_powers()
    names = df.index.to_series().map(display_name)
    print(f"Insider pay: {len(df)} people, {int(df['poi'].sum())} persons of interest, {df['email_address'].notna().sum()} with an e-mail address.")  # noqa: E501
    mail = load_emails()
    ties = company_ties(mail)
    graph = reciprocal_graph(ties)
    print(f"E-mail: {len(mail):,} distinct messages 1999-01..2002-06; company network of {graph.number_of_nodes():,} people, {graph.number_of_edges():,} mutual ties.")  # noqa: E501
    out = pd.concat([df[MONEY].round().astype("int64"), money_ratios(df), network_features(df, mail, graph)], axis=1)
    out.insert(0, "role", roles(df, powers))
    out.insert(0, "name", names)
    out["poi"] = df["poi"]
    out.attrs["leaky"] = df[LEAKY_EMAIL]
    return out, build_graph(df, names, ties, graph, powers)


def ablation(out: pd.DataFrame) -> None:
    """Repeated 5-fold CV ROC AUC (10 repeats — 18 positives make one 5-fold split
    noisy) with training's own pipeline, for the README's table."""
    from sklearn.model_selection import RepeatedStratifiedKFold, cross_val_score
    from training.core.training import CANDIDATE_ESTIMATORS, build_pipeline

    data = pd.concat([out, out.attrs["leaky"]], axis=1)
    data["any_email_trail"] = ((data["emails_sent"] + data["emails_received"]) > 0).astype(int)
    with_trail = data[data["any_email_trail"] == 1]
    cv = RepeatedStratifiedKFold(n_splits=5, n_repeats=10, random_state=0)

    def score(columns: list[str], model: str, rows: pd.DataFrame = data) -> str:
        estimator = CANDIDATE_ESTIMATORS["classification"][model]()
        pipeline = build_pipeline(columns, [], estimator, [])
        scores = cross_val_score(pipeline, rows, rows["poi"], cv=cv, scoring="roc_auc")
        return f"{np.mean(scores):.3f} ± {np.std(scores):.3f}"

    rows = [
        ("Money only (pay + stock + ratios)", [*MONEY, *RATIOS]),
        ("Network only (label-free)", NETWORK),
        ("**Money + network (what the scenario trains on)**", FEATURES),
        ("Money + ud120's e-mail counts (leak the label)", [*MONEY, *RATIOS, *LEAKY_EMAIL]),
        ("Diagnostic: 'has any e-mail trail' flag alone", ["any_email_trail"]),
    ]
    print("\n| Inputs | Logistic regression | LightGBM (small data) |\n| --- | --- | --- |")
    for label, columns in rows:
        print(f"| {label} | {score(columns, 'logistic_regression')} | {score(columns, 'lightgbm_small_data')} |")
    label = f"Money + network, only the {len(with_trail)} people with an e-mail trail"
    print(f"| {label} | {score(FEATURES, 'logistic_regression', with_trail)} | {score(FEATURES, 'lightgbm_small_data', with_trail)} |")  # noqa: E501
    print("\nE-mail trail vs person of interest:\n" + pd.crosstab(data["any_email_trail"], data["poi"]).to_string())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--ablation", action="store_true", help="print the README's CV comparison table")
    args = parser.parse_args()

    out, graph = prepare()
    SCENARIO_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(CSV_PATH, index_label="person_id")
    GRAPH_PATH.write_text(json.dumps(graph, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    written = pd.read_csv(CSV_PATH, index_col="person_id")
    if ((written[PAYMENTS].sum(axis=1) - written["total_payments"]).abs() > 1).any() or written.isna().any().any():
        raise SystemExit(f"{CSV_PATH} does not round-trip (totals or missing values).")
    for path in (CSV_PATH, GRAPH_PATH):
        print(f"Wrote {path.relative_to(ROOT)} ({path.stat().st_size:,} bytes, sha256 {hashlib.sha256(path.read_bytes()).hexdigest()})")  # noqa: E501
    kinds = pd.Series([n["kind"] for n in graph["nodes"]]).value_counts().to_dict()  # type: ignore[union-attr]
    edge_kinds = pd.Series([e["kind"] for e in graph["edges"]]).value_counts().to_dict()  # type: ignore[union-attr]
    print(f"Graph: nodes {kinds}, edges {edge_kinds}")
    print(out.describe().T.round(3).to_string())
    if args.ablation:
        ablation(out)


if __name__ == "__main__":
    main()
