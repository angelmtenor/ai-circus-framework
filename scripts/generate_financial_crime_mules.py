#!/usr/bin/env python3
"""Synthetic mule-account network for the `financial_crime_desk` scenario (extra model `mules`).

The same invented European bank as `generate_financial_crime.py`: 60 days of transfers between
its retail, student, freelancer and small-business accounts and a few hundred outside parties,
written under `scenarios/financial_crime_desk/sample_data/` as

  - `mule_accounts.csv`, target `Mule`: the account was later confirmed as a money mule (it
    received and passed on proceeds of crime). One row per account, with behaviour features
    computed here from the simulated transfers — the model never sees the network itself.
  - `mule_network.json` (ai_circus_shared.network_graph): every account (`row`), the outside
    parties (`context`: payers, employers, crypto exchanges, the ATM network) and the
    devices and home addresses two or more accounts share (`entity`), with one `transfer` edge per
    pair of parties that paid each other (everyday spending at merchants left out), its amount per day
    as a `series`, and `uses_device` / `lives_at` relations.

Mule rings come in three designed shapes (kept in the `Typology` display column, never trained
on): a *collector ring* (many outside payers → two or three collectors → layering accounts →
crypto or cash within hours), a *chain* (A → B → C → D → exchange, each hop the same day) and a
*device farm* (new accounts opened the same weeks on shared devices and addresses that collect and
cash out). Genuine accounts overlap with each shape on purpose: small businesses collect from
many payers (but hold the money for days), households and couples share addresses and tablets,
students receive from family and spend at once, freelancers get paid by many clients. A few
mules are recruited students and a few recruited accounts are years old, so neither the segment
nor the age decides alone.

Accounts are pseudonymous ids; there are no names, no ages and no nationalities.

Run: `python scripts/generate_financial_crime_mules.py` (numpy + pandas; any service venv has them).
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 23
N_ACCOUNTS = 800
DAYS = 60
START = date(2026, 9, 1)
OUT_DIR = Path(__file__).resolve().parents[1] / "scenarios" / "financial_crime_desk" / "sample_data"
SEGMENTS = ["Retail", "Student", "Freelancer", "Small business"]
SEGMENT_SHARE = [0.6, 0.15, 0.1, 0.15]
RING_SHAPES = ["Collector ring", "Chain", "Device farm"]

PERIODS = [(START + timedelta(days=d)).isoformat() for d in range(DAYS)]


class World:
    """The simulated transfers and the facts the features are built from."""

    def __init__(self, rng: np.random.Generator) -> None:
        self.rng = rng
        self.txns: list[tuple[str, str, int, float, float]] = []  # source, target, day, hour, amount
        self.devices: dict[str, set[str]] = defaultdict(set)
        self.addresses: dict[str, set[str]] = defaultdict(set)
        self.context: dict[str, str] = {}  # id -> type

    def pay(self, source: str, target: str, day: int, hour: float, amount: float) -> None:
        if source != target and 0 <= day < DAYS and amount > 0:
            self.txns.append((source, target, int(day), float(hour) % 24, round(float(amount), 2)))

    def outside(self, prefix: str, kind: str, n: int) -> list[str]:
        ids = [f"{prefix}-{i:03d}" for i in range(1, n + 1)]
        for i in ids:
            self.context[i] = kind
        return ids


def _hour(rng: np.random.Generator, night: float = 0.05) -> float:
    return float(rng.uniform(0, 6) if rng.random() < night else rng.uniform(7, 23))


def simulate(rng: np.random.Generator) -> tuple[World, pd.DataFrame]:
    w = World(rng)
    accounts = pd.DataFrame(
        {
            "AccountId": [f"A-{10000 + i * 7 + int(rng.integers(0, 7))}" for i in range(N_ACCOUNTS)],
            "Segment": rng.choice(SEGMENTS, size=N_ACCOUNTS, p=SEGMENT_SHARE),
        }
    )
    accounts["AccountAgeDays"] = np.minimum(rng.exponential(1400, N_ACCOUNTS) + 30, 9000).astype(int)
    accounts["Typology"] = "Genuine"
    accounts["Ring"] = ""
    payers = w.outside("P", "Outside payer", 100)
    employers = w.outside("E", "Employer", 25)
    merchants = w.outside("M", "Merchant", 30)
    exchanges = w.outside("X", "Crypto exchange", 6)
    atm = w.outside("ATM", "ATM network", 1)[0]

    # ── mule rings ───────────────────────────────────────────────────────────
    order = rng.permutation(N_ACCOUNTS)
    cursor = 0
    ring_id = 0
    for shape in [*RING_SHAPES * 5]:
        size = int(rng.integers(4, 9))
        members = [accounts.at[i, "AccountId"] for i in order[cursor : cursor + size]]
        idx = list(order[cursor : cursor + size])
        cursor += size
        ring_id += 1
        ring = f"Ring {ring_id:02d}"
        accounts.loc[idx, "Typology"] = shape
        accounts.loc[idx, "Ring"] = ring
        # Most mule accounts are recent; a few recruited ones are years old.
        accounts.loc[idx, "AccountAgeDays"] = [
            int(rng.integers(5, 120)) if rng.random() < 0.75 else int(rng.integers(400, 4000)) for _ in idx
        ]
        if rng.random() < 0.4:  # a recruited-student ring
            accounts.loc[idx, "Segment"] = "Student"
        active = sorted(rng.choice(DAYS, size=int(rng.integers(6, 14)), replace=False))
        # A third of the rings are patient: they hold the money for days and keep a share of it.
        patient = rng.random() < 0.35
        delay = (lambda: int(rng.integers(1, 5))) if patient else (lambda: 0)
        keep = (lambda: rng.uniform(0.6, 0.8)) if patient else (lambda: rng.uniform(0.93, 0.99))
        if shape == "Collector ring":
            collectors, layers = members[: max(2, size // 3)], members[max(2, size // 3) :]
            for day in active:
                for c in collectors:
                    received = 0.0
                    for p in rng.choice(payers, size=int(rng.integers(2, 7)), replace=False):
                        amount = rng.lognormal(6.6, 0.6)
                        w.pay(str(p), c, day, _hour(rng, 0.15), amount)
                        received += amount
                    for layer in rng.choice(layers, size=min(2, len(layers)), replace=False):
                        part = received * rng.uniform(0.35, 0.48) * keep()
                        hop = day + delay()
                        w.pay(c, str(layer), hop, _hour(rng, 0.25), part)
                        w.pay(str(layer), str(rng.choice(exchanges)) if rng.random() < 0.7 else atm, hop + delay(), _hour(rng, 0.3), part * keep())
        elif shape == "Chain":
            for day in active:
                amount = rng.lognormal(8.0, 0.5)
                w.pay(str(rng.choice(payers)), members[0], day, _hour(rng, 0.2), amount)
                hop = day
                for a, b in zip(members, members[1:], strict=False):
                    amount *= keep()
                    hop += delay()
                    w.pay(a, b, hop, _hour(rng, 0.3), amount)
                w.pay(members[-1], str(rng.choice(exchanges)), hop + delay(), _hour(rng, 0.3), amount * keep())
        else:  # Device farm
            shared_devices = [f"D-{ring_id:02d}{k}" for k in range(int(rng.integers(1, 3)))]
            shared_address = f"H-{ring_id:02d}"
            for m in members:
                w.devices[str(rng.choice(shared_devices))].add(m)
                if rng.random() < 0.7:
                    w.addresses[shared_address].add(m)
            for day in active:
                for m in rng.choice(members, size=int(rng.integers(2, len(members) + 1)), replace=False):
                    amount = 0.0
                    for p in rng.choice(payers, size=int(rng.integers(1, 4)), replace=False):
                        a = rng.lognormal(6.4, 0.7)
                        w.pay(str(p), str(m), day, _hour(rng, 0.2), a)
                        amount += a
                    w.pay(str(m), atm if rng.random() < 0.6 else str(rng.choice(exchanges)), day + delay(), _hour(rng, 0.35), amount * keep())

    # ── genuine behaviour ────────────────────────────────────────────────────
    genuine = accounts[accounts["Typology"] == "Genuine"]
    ids = list(genuine["AccountId"])
    for i, (acc, seg) in enumerate(zip(genuine["AccountId"], genuine["Segment"], strict=True)):
        if seg in {"Retail", "Freelancer"} or (seg == "Student" and rng.random() < 0.3):
            employer = str(rng.choice(employers))
            pay_day = int(rng.integers(0, 28))
            salary = rng.lognormal(7.6, 0.35) if seg == "Retail" else rng.lognormal(6.6, 0.4)
            # Some people sweep most of their pay to a savings or partner account the same day.
            sweep = str(rng.choice(ids)) if rng.random() < 0.12 else None
            for day in (pay_day, pay_day + 30):
                w.pay(employer, acc, day, 9, salary)
                if sweep:
                    w.pay(acc, sweep, day, _hour(rng), salary * rng.uniform(0.7, 0.95))
        if seg == "Freelancer":
            for p in rng.choice(payers, size=int(rng.integers(3, 12)), replace=False):
                w.pay(str(p), acc, int(rng.integers(0, DAYS)), _hour(rng), rng.lognormal(6.8, 0.6))
        if seg == "Small business":
            customers = rng.choice(payers, size=int(rng.integers(6, 25)), replace=False)
            for p in customers:
                for _ in range(int(rng.integers(1, 4))):
                    w.pay(str(p), acc, int(rng.integers(0, DAYS)), _hour(rng), rng.lognormal(6.5, 0.8))
            for _ in range(int(rng.integers(2, 6))):  # suppliers, paid in batches days later
                w.pay(acc, str(rng.choice(merchants)), int(rng.integers(10, DAYS)), _hour(rng), rng.lognormal(7.8, 0.5))
            if rng.random() < 0.08:  # some businesses buy crypto
                w.pay(acc, str(rng.choice(exchanges)), int(rng.integers(0, DAYS)), _hour(rng), rng.lognormal(7.0, 0.6))
        if seg == "Student":
            parent = str(rng.choice(ids))
            for day in sorted(rng.choice(DAYS, size=int(rng.integers(2, 5)), replace=False)):
                amount = rng.lognormal(5.6, 0.4)
                w.pay(parent, acc, day, _hour(rng), amount)
                w.pay(acc, str(rng.choice(merchants)), day, _hour(rng, 0.2), amount * rng.uniform(0.6, 1.0))
        # Everyday spending, friends and family, the occasional cash or crypto.
        for _ in range(int(rng.integers(4, 16))):
            w.pay(acc, str(rng.choice(merchants)), int(rng.integers(0, DAYS)), _hour(rng, 0.04), rng.lognormal(4.0, 0.8))
        for _ in range(int(rng.poisson(1.5))):
            w.pay(acc, str(rng.choice(ids)), int(rng.integers(0, DAYS)), _hour(rng, 0.05), rng.lognormal(4.5, 0.8))
        if rng.random() < 0.35:
            w.pay(acc, atm, int(rng.integers(0, DAYS)), _hour(rng, 0.05), rng.lognormal(4.8, 0.5))
        if rng.random() < 0.07:
            w.pay(acc, str(rng.choice(exchanges)), int(rng.integers(0, DAYS)), _hour(rng, 0.1), rng.lognormal(5.5, 0.8))

    # Households and couples: shared addresses and tablets among genuine accounts too.
    shuffled = list(rng.permutation(ids))
    h = 0
    while shuffled and h < 100:
        size = int(rng.choice([2, 2, 2, 3, 4]))
        group, shuffled = shuffled[:size], shuffled[size:]
        h += 1
        for g in group:
            w.addresses[f"H-G{h:03d}"].add(str(g))
        if rng.random() < 0.3:
            for g in group[:2]:
                w.devices[f"D-G{h:03d}"].add(str(g))
    return w, accounts


def _pagerank(nodes: list[str], edges: dict[tuple[str, str], float], damping: float = 0.85, steps: int = 60) -> dict[str, float]:
    index = {n: i for i, n in enumerate(nodes)}
    out_weight = np.zeros(len(nodes))
    for (s, _), v in edges.items():
        out_weight[index[s]] += v
    rank = np.full(len(nodes), 1 / len(nodes))
    for _ in range(steps):
        new = np.full(len(nodes), (1 - damping) / len(nodes))
        for (s, t), v in edges.items():
            new[index[t]] += damping * rank[index[s]] * v / out_weight[index[s]]
        dangling = rank[out_weight == 0].sum()
        new += damping * dangling / len(nodes)
        rank = new
    return {n: float(rank[index[n]]) for n in nodes}


def features(w: World, accounts: pd.DataFrame) -> pd.DataFrame:
    ids = set(accounts["AccountId"])
    tx = pd.DataFrame(w.txns, columns=["source", "target", "day", "hour", "amount"])
    tx["t"] = tx["day"] * 24 + tx["hour"]
    rows = []
    neighbours: dict[str, set[str]] = defaultdict(set)
    for s, t in zip(tx["source"], tx["target"], strict=True):
        neighbours[s].add(t)
        neighbours[t].add(s)
    pair = tx.groupby(["source", "target"])["amount"].sum().to_dict()
    nodes = sorted({*tx["source"], *tx["target"]})
    pr = _pagerank(nodes, pair)
    acc_pr = pd.Series({a: pr.get(a, 0.0) for a in ids})
    pr_pct = acc_pr.rank(pct=True) * 100
    shared_dev = defaultdict(set)
    for members in w.devices.values():
        for m in members:
            shared_dev[m] |= members - {m}
    shared_addr = defaultdict(set)
    for members in w.addresses.values():
        for m in members:
            shared_addr[m] |= members - {m}
    by_source = dict(tuple(tx.groupby("source")))
    by_target = dict(tuple(tx.groupby("target")))
    for acc in accounts["AccountId"]:
        out = by_source.get(acc, tx.iloc[0:0])
        inc = by_target.get(acc, tx.iloc[0:0])
        inflow, outflow = float(inc["amount"].sum()), float(out["amount"].sum())
        # Dwell: hours between each outgoing payment and the latest incoming one before it.
        in_times = np.sort(inc["t"].to_numpy())
        dwell = []
        for t in out["t"].to_numpy():
            k = np.searchsorted(in_times, t, side="right")
            if k:
                dwell.append(t - in_times[k - 1])
        n = sorted(neighbours.get(acc, set()))
        links = sum(1 for i, a in enumerate(n) for b in n[i + 1 :] if b in neighbours.get(a, set()))
        clustering = 2 * links / (len(n) * (len(n) - 1)) if len(n) > 1 else 0.0
        all_tx = pd.concat([inc, out])
        rows.append(
            {
                "AccountId": acc,
                "InflowEUR": round(inflow, 2),
                "OutflowEUR": round(outflow, 2),
                "DistinctSenders": int(inc["source"].nunique()),
                "DistinctRecipients": int(out["target"].nunique()),
                "DistinctCounterparties": len(n),
                "PassThroughRatio": round(min(outflow / inflow, 3.0), 3) if inflow > 0 else 0.0,
                "MedianDwellHours": round(float(np.median(dwell)), 1) if dwell else 720.0,
                "CryptoShare": round(float(out.loc[out["target"].str.startswith("X-"), "amount"].sum() / outflow), 3) if outflow else 0.0,
                "CashShare": round(float(out.loc[out["target"] == "ATM-001", "amount"].sum() / outflow), 3) if outflow else 0.0,
                "NightShare": round(float((all_tx["hour"] < 6).mean()), 3) if len(all_tx) else 0.0,
                "ActiveDays": int(all_tx["day"].nunique()),
                "SharedDeviceAccounts": len(shared_dev.get(acc, set())),
                "SharedAddressAccounts": len(shared_addr.get(acc, set())),
                "PageRankPctl": round(float(pr_pct[acc]), 1),
                "Clustering": round(clustering, 3),
            }
        )
    out_df = accounts.merge(pd.DataFrame(rows), on="AccountId")
    # Confirmed after investigation: most ring members, a handful of genuine accounts.
    rng = w.rng
    is_ring = out_df["Typology"] != "Genuine"
    out_df["Mule"] = np.where(is_ring, rng.random(len(out_df)) < 0.88, rng.random(len(out_df)) < 0.01).astype(int)
    out_df.insert(1, "Name", "Account " + out_df["AccountId"])
    return out_df.drop(columns=["Ring"])


def graph(w: World, accounts: pd.DataFrame, communities: dict[str, str]) -> dict:
    tx = pd.DataFrame(w.txns, columns=["source", "target", "day", "hour", "amount"])
    # Everyday card spending at merchants is in the features but left out of the drawing: over half the
    # edges, none of the story, and a lighter graph to download and draw.
    tx = tx[~tx["target"].isin([c for c, kind in w.context.items() if kind == "Merchant"])]
    nodes = [
        {"id": a, "kind": "row", "label": f"Account {a}", **({"group": communities[a]} if a in communities else {})}
        for a in accounts["AccountId"]
    ]
    used = set(tx["source"]) | set(tx["target"])
    nodes += [{"id": c, "kind": "context", "label": c, "type": kind} for c, kind in w.context.items() if c in used]
    edges = []
    for (s, t), g in tx.groupby(["source", "target"]):
        series = g.groupby("day")["amount"].sum()
        edges.append(
            {
                "source": s,
                "target": t,
                "kind": "transfer",
                "weight": round(float(g["amount"].sum()), 2),
                "series": {PERIODS[int(d)]: round(float(v), 2) for d, v in series.items()},
            }
        )
    for kind, table, label, edge_kind in (("Device", w.devices, "Shared device", "uses_device"), ("Address", w.addresses, "Shared address", "lives_at")):
        shared = [e for e, members in table.items() if len(members) >= 2]
        # Renumbered in a shuffled order, so an id never tells a ring's device from a household's.
        renamed = {e: f"{kind[0]}-{i + 1:03d}" for i, e in enumerate(w.rng.permutation(shared))}
        for entity in shared:
            members, new_id = table[entity], renamed[entity]
            nodes.append({"id": new_id, "kind": "entity", "label": f"{label} {new_id}", "type": kind})
            edges += [{"source": m, "target": new_id, "kind": edge_kind, "label": label.lower()} for m in sorted(members)]
    return {"version": 1, "periods": PERIODS, "nodes": nodes, "edges": edges}


def _communities(w: World, accounts: pd.DataFrame, rng: np.random.Generator) -> dict[str, str]:
    """Label propagation over the account-to-account transfers and the shared devices and addresses."""
    ids = list(accounts["AccountId"])
    adj: dict[str, set[str]] = defaultdict(set)
    known = set(ids)
    for s, t, *_ in w.txns:
        if s in known and t in known:
            adj[s].add(t)
            adj[t].add(s)
    for table in (w.devices, w.addresses):
        for members in table.values():
            for m in members:
                adj[m] |= members - {m}
    label = {a: a for a in ids}
    for _ in range(15):
        for a in rng.permutation(ids):
            if adj[a]:
                counts: dict[str, int] = defaultdict(int)
                for b in adj[a]:
                    counts[label[b]] += 1
                label[a] = max(sorted(counts), key=lambda k: counts[k])
    sizes: dict[str, int] = defaultdict(int)
    for v in label.values():
        sizes[v] += 1
    big = sorted((k for k, v in sizes.items() if v >= 3), key=lambda k: -sizes[k])
    names = {k: f"Community {i + 1}" for i, k in enumerate(big)}
    return {a: names[label[a]] for a in ids if label[a] in names}


def main() -> None:
    rng = np.random.default_rng(SEED)
    world, accounts = simulate(rng)
    df = features(world, accounts)
    communities = _communities(world, accounts, rng)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUT_DIR / "mule_accounts.csv", index=False)
    doc = graph(world, accounts, communities)
    (OUT_DIR / "mule_network.json").write_text(json.dumps(doc, separators=(",", ":")))
    print(
        f"mule_accounts: {len(df)} rows, positive rate {df['Mule'].mean():.3f}; "
        f"graph: {len(doc['nodes'])} nodes, {len(doc['edges'])} edges, {len(set(communities.values()))} communities"
    )


if __name__ == "__main__":
    main()
