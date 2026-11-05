"""
- Title:    Recommenders from baskets: association rules and item factors
- Author:   Angel Martinez-Tenor

"What goes with this item?" (`model.task_type: recommendation`, docs/adr/0018). Baskets are the only
input: anonymous ids and the items in them, never a customer. Both candidates are served side by
side; the score says how often one finds an item hidden from a basket it never saw, next to the plain
popularity list, which is what either has to beat to be worth anything.
"""

from __future__ import annotations

import zlib
from collections import Counter
from dataclasses import dataclass
from itertools import combinations
from typing import Any

import numpy as np
import pandas as pd
from ai_circus_shared.scenario_schema import RecommenderConfig
from scipy.sparse import csr_matrix
from sklearn.decomposition import TruncatedSVD

# One basket in five, by checksum of its id: the split never depends on row order.
_HOLDOUT_EVERY = 5
_MIN_BASKETS_TO_EVALUATE = 20

Neighbours = dict[str, list[tuple[str, float]]]


@dataclass
class RecommenderCandidate:
    """One candidate's neighbours (refit on every basket) and its score on hidden items."""

    name: str
    metrics: dict[str, float]
    neighbours: Neighbours

    @property
    def selection_score(self) -> float:
        """The hit rate on hidden basket items."""
        return self.metrics["hit_rate"]

    @property
    def test_score(self) -> float:
        """The hit rate on hidden basket items."""
        return self.metrics["hit_rate"]


@dataclass
class RecommenderRun:
    """Everything training writes for a recommendation model."""

    candidates: list[RecommenderCandidate]
    selected: RecommenderCandidate
    document: dict[str, Any]
    popularity_hit_rate: float
    baskets: int


def association_rules(baskets: list[list[str]], top_k: int, min_baskets: int) -> Neighbours:
    """For each item, the items most often in its baskets: the share of them that also hold the
    other (confidence). Ranking by lift instead finds fewer of the items people really add (ADR 0018).
    """
    item_counts: Counter[str] = Counter(item for basket in baskets for item in basket)
    pair_counts: Counter[tuple[str, str]] = Counter(
        pair for basket in baskets for pair in combinations(sorted(basket), 2)
    )
    scored: dict[str, list[tuple[str, float]]] = {item: [] for item in item_counts}
    for (a, b), together in pair_counts.items():
        if together >= min_baskets:
            scored[a].append((b, round(together / item_counts[a], 4)))
            scored[b].append((a, round(together / item_counts[b], 4)))
    return {item: sorted(pairs, key=lambda p: (-p[1], p[0]))[:top_k] for item, pairs in scored.items()}


def item_factors(baskets: list[list[str]], top_k: int, factors: int) -> Neighbours:
    """For each item, the items a factor model learned from the baskets (truncated SVD of the item x
    basket matrix) expects in its baskets: its smoothed version of the rules' confidence, so two
    items can match without sharing many baskets.
    """
    items = sorted({item for basket in baskets for item in basket})
    position = {item: i for i, item in enumerate(items)}
    rows = [position[item] for basket in baskets for item in basket]
    columns = [b for b, basket in enumerate(baskets) for _ in basket]
    matrix = csr_matrix((np.ones(len(rows)), (rows, columns)), shape=(len(items), len(baskets)))
    components = min(factors, len(items) - 1, len(baskets) - 1)
    if components < 1:
        return {item: [] for item in items}
    vectors = TruncatedSVD(n_components=components, random_state=0).fit_transform(matrix)
    together = vectors @ vectors.T  # the low-rank estimate of how many baskets hold both items
    np.fill_diagonal(together, -1)
    counts = np.asarray(matrix.sum(axis=1)).ravel()
    neighbours: Neighbours = {}
    for i, item in enumerate(items):
        order = np.argsort(-together[i], kind="stable")[:top_k]
        neighbours[item] = [
            (items[j], round(float(together[i, j] / counts[i]), 4)) for j in order if together[i, j] > 0
        ]
    return neighbours


def _split(baskets: dict[str, list[str]]) -> tuple[list[list[str]], list[tuple[list[str], str]]]:
    """(train baskets, test (asked items, hidden item)); only a basket of two or more items can hide
    one and still ask. The hidden item is picked by checksum: always hiding the alphabetically last
    one would mostly hide wax, the most popular product, and flatter the popularity baseline.
    """
    train, test = [], []
    for basket_id, items in baskets.items():
        held_out = zlib.crc32(basket_id.encode()) % _HOLDOUT_EVERY == 0
        if held_out and len(items) >= 2:
            hidden = items[zlib.crc32(f"{basket_id}|hidden".encode()) % len(items)]
            test.append(([item for item in items if item != hidden], hidden))
        else:
            train.append(items)
    return train, test


def _hit_rate(neighbours: Neighbours, test: list[tuple[list[str], str]], top_k: int) -> float:
    """How often the hidden item of a basket is in the top_k recommended for the rest of it."""
    hits = 0
    for asked, hidden in test:
        scores: dict[str, float] = {}
        for item in asked:
            for other, score in neighbours.get(item, []):
                if other not in asked:
                    scores[other] = scores.get(other, 0.0) + score
        hits += hidden in sorted(scores, key=lambda item: (-scores[item], item))[:top_k]
    return round(hits / len(test), 4) if test else 0.0


def _popularity_hit_rate(train: list[list[str]], test: list[tuple[list[str], str]], top_k: int) -> float:
    popular = [item for item, _ in Counter(item for basket in train for item in basket).most_common()]
    hits = 0
    for asked, hidden in test:
        hits += hidden in [item for item in popular if item not in asked][:top_k]
    return round(hits / len(test), 4) if test else 0.0


def train_recommender(
    df: pd.DataFrame, config: RecommenderConfig, candidates: list[str], threshold: float
) -> RecommenderRun:
    """Score every candidate on hidden basket items, refit on every basket, and keep the simplest
    whose hit rate no later candidate beats by more than `threshold` (the Green Code rule).

    Raises:
        ValueError: too few baskets of two or more items to evaluate anything.
    """
    frame = df.reset_index(drop=True)[[config.basket_column, config.item_column, config.label_column]].astype(str)
    grouped = frame.groupby(config.basket_column, sort=True)[config.item_column]
    baskets = {str(basket_id): sorted(set(items)) for basket_id, items in grouped}
    train, test = _split(baskets)
    if len(test) < _MIN_BASKETS_TO_EVALUATE:
        raise ValueError(f"only {len(test)} held-out baskets of two or more items: too few to evaluate.")

    def fit(name: str, data: list[list[str]]) -> Neighbours:
        if name == "association_rules":
            return association_rules(data, config.top_k * 3, config.min_baskets)
        return item_factors(data, config.top_k * 3, config.factors)

    everything = list(baskets.values())
    scored = []
    for name in candidates:
        metrics = {"hit_rate": _hit_rate(fit(name, train), test, config.top_k)}
        neighbours = {item: pairs[: config.top_k] for item, pairs in fit(name, everything).items()}
        scored.append(RecommenderCandidate(name=name, metrics=metrics, neighbours=neighbours))
    selected = scored[0]
    for candidate in scored[1:]:
        if candidate.selection_score > selected.selection_score + threshold:
            selected = candidate

    counts = Counter(item for basket in everything for item in basket)
    labels = dict(zip(frame[config.item_column], frame[config.label_column], strict=False))
    document = {
        "selected": selected.name,
        "top_k": config.top_k,
        "items": {item: {"label": labels[item], "baskets": count} for item, count in counts.most_common()},
        "candidates": {
            c.name: {item: [{"item": o, "score": s} for o, s in pairs] for item, pairs in c.neighbours.items()}
            for c in scored
        },
    }
    return RecommenderRun(
        candidates=scored,
        selected=selected,
        document=document,
        popularity_hit_rate=_popularity_hit_rate(train, test, config.top_k),
        baskets=len(everything),
    )
