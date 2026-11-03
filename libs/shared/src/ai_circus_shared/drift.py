"""Model drift: a training-time reference profile, and PSI of live scoring traffic against it.

`training` builds the reference once (`build_reference`) from the rows the deployed model was fit on — the
top features by SHAP importance plus the model's own score — and writes it into the model metadata
(`drift_reference`). `prediction` folds every scored request into a bounded per-model window
(`DriftWindow`) after the response, and exports the Population Stability Index of each feature and of the
score on /metrics (the framework's model-drift dashboard and alert rules, ADR 0015).

Pure Python on purpose (libs/shared has no numpy): bins are fixed at training time, so observing a value is
one `bisect`, and `prediction` caps the rows it observes per request. Profiles carry bin edges and
proportions only — no raw values, no identifiers.

PSI reading, the usual convention: < 0.1 stable, 0.1-0.25 moderate shift, > 0.25 significant shift.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

REFERENCE_VERSION = 1
BINS = 10
MAX_FEATURES = 8
MAX_CATEGORIES = 10
OTHER = "__other__"
EPSILON = 1e-4  # an empty bin on either side would make PSI infinite
MIN_ROWS = 100  # below this a window's PSI is noise, so none is reported
WINDOW_ROWS = 2_000  # a window this full starts afresh (tumbling), keeping memory and staleness bounded
PSI_MODERATE, PSI_SIGNIFICANT = 0.1, 0.25


def _missing(value: object) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def _number(value: object) -> float | None:
    if _missing(value) or isinstance(value, bool):
        return None
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) or math.isinf(number) else number


def _quantile(ordered: Sequence[float], q: float) -> float:
    position = q * (len(ordered) - 1)
    low = math.floor(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def numeric_profile(values: Iterable[object]) -> dict[str, Any] | None:
    """Decile bin edges of `values` and the share of rows in each bin (last bin: missing)."""
    raw = list(values)
    numbers = sorted(n for n in (_number(v) for v in raw) if n is not None)
    if not numbers:
        return None
    edges = sorted({_quantile(numbers, i / BINS) for i in range(1, BINS)})
    profile: dict[str, Any] = {"kind": "numeric", "edges": edges}
    profile["expected"] = _shares(bin_counts(profile, raw))
    return profile


def categorical_profile(values: Iterable[object]) -> dict[str, Any] | None:
    """The most frequent categories, the share of each, of all others together, and of missing values."""
    raw = list(values)
    present = Counter(str(v) for v in raw if not _missing(v))
    if not present:
        return None
    profile: dict[str, Any] = {"kind": "categorical", "categories": [c for c, _ in present.most_common(MAX_CATEGORIES)]}
    profile["expected"] = _shares(bin_counts(profile, raw))
    return profile


def bin_counts(profile: Mapping[str, Any], values: Iterable[object]) -> list[int]:
    """How many of `values` fall in each of `profile`'s bins (its last bin counts missing values)."""
    if profile["kind"] == "numeric":
        edges = profile["edges"]
        counts = [0] * (len(edges) + 2)
        for value in values:
            number = _number(value)
            counts[-1 if number is None else bisect_right(edges, number)] += 1
        return counts
    index = {category: i for i, category in enumerate(profile["categories"])}
    counts = [0] * (len(index) + 2)  # categories..., other, missing
    for value in values:
        counts[-1 if _missing(value) else index.get(str(value), len(index))] += 1
    return counts


def _shares(counts: Sequence[int]) -> list[float]:
    total = sum(counts)
    return [round(c / total, 6) if total else 0.0 for c in counts]


def psi(expected: Sequence[float], counts: Sequence[int]) -> float | None:
    """Population Stability Index of observed `counts` against `expected` shares; None on too few rows."""
    total = sum(counts)
    if total < MIN_ROWS or len(expected) != len(counts):
        return None
    value = 0.0
    for e, c in zip(expected, counts, strict=True):
        e, a = max(e, EPSILON), max(c / total, EPSILON)
        value += (a - e) * math.log(a / e)
    return round(value, 4)


def build_reference(
    columns: Mapping[str, Sequence[object]],
    kinds: Mapping[str, str],
    ranked_features: Sequence[str],
    scores: Sequence[float],
) -> dict[str, Any]:
    """The `drift_reference` model-metadata block: up to MAX_FEATURES features, in `ranked_features` order
    (most important first), profiled as `kinds` says (`numeric` / `categorical`; anything else — free
    text — is skipped), plus the model's score distribution.
    """
    features: dict[str, Any] = {}
    for name in ranked_features:
        if len(features) == MAX_FEATURES:
            break
        if name not in columns:
            continue
        kind = kinds.get(name)
        profile = (
            numeric_profile(columns[name])
            if kind == "numeric"
            else categorical_profile(columns[name])
            if kind == "categorical"
            else None
        )
        if profile is not None:
            features[name] = profile
    return {"version": REFERENCE_VERSION, "features": features, "score": numeric_profile(scores)}


class DriftWindow:
    """Bin counts of the rows one model has scored lately, against its reference (not thread-safe alone)."""

    def __init__(self, reference: Mapping[str, Any]) -> None:
        self.reference = reference
        self.features: dict[str, Mapping[str, Any]] = dict(reference.get("features") or {})
        self.score: Mapping[str, Any] | None = reference.get("score")
        self._reset()

    def _reset(self) -> None:
        self.rows = 0
        self.counts = {name: [0] * len(p["expected"]) for name, p in self.features.items()}
        self.score_counts = [0] * len(self.score["expected"]) if self.score else []

    def observe(self, columns: Mapping[str, Sequence[object]], scores: Sequence[float]) -> None:
        """Add one batch of scored rows (`columns` by feature name, `scores` in the same row order)."""
        if self.rows >= WINDOW_ROWS:
            self._reset()
        for name, profile in self.features.items():
            if name in columns:
                for i, count in enumerate(bin_counts(profile, columns[name])):
                    self.counts[name][i] += count
        if self.score:
            for i, count in enumerate(bin_counts(self.score, scores)):
                self.score_counts[i] += count
        self.rows += len(scores)

    def feature_psi(self) -> dict[str, float]:
        """PSI per profiled feature — empty until the window holds MIN_ROWS rows."""
        values = {name: psi(self.features[name]["expected"], counts) for name, counts in self.counts.items()}
        return {name: value for name, value in values.items() if value is not None}

    def score_psi(self) -> float | None:
        """PSI of the model's score distribution, or None on too few rows / no score reference."""
        return psi(self.score["expected"], self.score_counts) if self.score else None


def sample_rows(count: int, limit: int) -> range:
    """Row positions to observe from a batch of `count` — every row, or an evenly spaced `limit` of them."""
    return range(count) if count <= limit else range(0, count, math.ceil(count / limit))
