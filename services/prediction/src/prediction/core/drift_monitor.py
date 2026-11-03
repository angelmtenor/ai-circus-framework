"""
- Title:    Live model-drift metrics on /metrics (ADR 0015) and per tenant (GET /model/{slug}/drift)
- Author:   Angel Martinez-Tenor

Every request the primary model scores is folded, after the response, into a bounded window per
(tenant, scenario, model version) — an `ai_circus_shared.drift.DriftWindow` against the `drift_reference`
training wrote into the model metadata. Windows are per tenant because tenants without a model of their own
share the fallback one: a tenant's drift card (`report`) must reflect its own traffic only.

Prometheus reads the Population Stability Index at scrape time, every tenant of a model summed — labels are
scenario slugs, model versions and feature names only, never tenants, values or rows:

- `ai_circus_model_feature_psi{scenario, model_version, feature}` — per profiled feature (top 8 by SHAP);
- `ai_circus_model_score_psi{scenario, model_version}` — of the served score itself;
- `ai_circus_model_drift_window_rows{scenario, model_version}` — rows behind those numbers (PSI needs ≥ 100);
- `ai_circus_scored_rows_total{scenario, model_variant}` — every scored row, challenger included.

A model trained before drift references existed has none: its scenario simply exports no PSI until the next
`make k3s-pipeline`. Bulk requests are sampled (evenly spaced rows) so a 30,000-row dataset scoring costs
the background thread a few milliseconds, not seconds.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from ai_circus_shared.decision_records import model_version
from ai_circus_shared.drift import MIN_ROWS, PSI_MODERATE, PSI_SIGNIFICANT, WINDOW_ROWS, DriftWindow, psi, sample_rows
from prometheus_client import Counter
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import REGISTRY, Collector, CollectorRegistry

from prediction.core.logger import get_logger

logger = get_logger(__name__)

MAX_ROWS_PER_REQUEST = 1_000
MAX_WINDOWS = 128  # (tenant, scenario, model version) triples kept — the least recently used goes first

WindowKey = tuple[str, str, str]  # org_id, scenario, model version


class DriftMonitor(Collector):
    """The per-tenant drift windows, the tenant's own report, and the Prometheus collector that reads them."""

    def __init__(self) -> None:
        """Start with no windows; one appears on the first scored request of each tenant and model version."""
        self._lock = threading.Lock()
        self._windows: OrderedDict[WindowKey, DriftWindow] = OrderedDict()

    def observe(
        self,
        org_id: str,
        scenario: str,
        metadata: Mapping[str, Any],
        records: Sequence[Mapping[str, Any]],
        scores: Sequence[float],
    ) -> None:
        """Fold one scored request into its tenant's window — a no-op without a drift reference; never raises."""
        try:
            reference = metadata.get("drift_reference")
            if not reference or not records:
                return
            rows = sample_rows(len(records), MAX_ROWS_PER_REQUEST)
            columns = {name: [records[i].get(name) for i in rows] for name in reference.get("features") or {}}
            sampled_scores = [scores[i] for i in rows]
            key = (org_id, scenario, model_version(metadata))
            with self._lock:
                window = self._windows.get(key)
                if window is None:
                    window = self._windows[key] = DriftWindow(reference)
                    while len(self._windows) > MAX_WINDOWS:
                        self._windows.popitem(last=False)
                self._windows.move_to_end(key)
                window.observe(columns, sampled_scores)
        except Exception:  # metrics must never touch a prediction
            logger.opt(exception=True).warning("drift observation failed for scenario={}", scenario)

    def report(self, org_id: str, scenario: str, metadata: Mapping[str, Any]) -> dict[str, Any]:
        """The caller's own drift of its deployed model: PSI per feature (worst first) and of the score.

        `available` is False when the model carries no drift reference (trained before they existed).
        """
        base: dict[str, Any] = {
            "available": bool(metadata.get("drift_reference")),
            "window_rows": 0,
            "min_rows": MIN_ROWS,
            "window_size": WINDOW_ROWS,
            "score_psi": None,
            "features": [],
            "thresholds": {"moderate": PSI_MODERATE, "significant": PSI_SIGNIFICANT},
        }
        if not base["available"]:
            return base
        with self._lock:
            window = self._windows.get((org_id, scenario, model_version(metadata)))
            if window is None:
                return base
            by_feature = window.feature_psi()
            base["window_rows"] = window.rows
            base["score_psi"] = window.score_psi()
        base["features"] = [
            {"feature": name, "psi": value} for name, value in sorted(by_feature.items(), key=lambda kv: -kv[1])
        ]
        return base

    def collect(self) -> Iterator[GaugeMetricFamily]:
        """Yield the PSI gauges per (scenario, model version), every tenant's window summed."""
        features = GaugeMetricFamily(
            "ai_circus_model_feature_psi",
            "Population Stability Index of a feature: live scoring traffic vs. the training data.",
            labels=["scenario", "model_version", "feature"],
        )
        score = GaugeMetricFamily(
            "ai_circus_model_score_psi",
            "Population Stability Index of the served score: live traffic vs. the training data.",
            labels=["scenario", "model_version"],
        )
        rows = GaugeMetricFamily(
            "ai_circus_model_drift_window_rows",
            "Scored rows in the current drift windows (PSI is reported from 100 rows).",
            labels=["scenario", "model_version"],
        )
        with self._lock:
            merged = _merge(self._windows)
        for (scenario, version), (window, counts, score_counts, total) in merged.items():
            rows.add_metric([scenario, version], float(total))
            for feature, feature_counts in counts.items():
                value = psi(window.features[feature]["expected"], feature_counts)
                if value is not None:
                    features.add_metric([scenario, version, feature], value)
            if window.score is not None:
                value = psi(window.score["expected"], score_counts)
                if value is not None:
                    score.add_metric([scenario, version], value)
        yield features
        yield score
        yield rows


def _merge(
    windows: Mapping[WindowKey, DriftWindow],
) -> dict[tuple[str, str], tuple[DriftWindow, dict[str, list[int]], list[int], int]]:
    """Every tenant's bin counts summed per (scenario, model version) — same reference, so same bins."""
    merged: dict[tuple[str, str], tuple[DriftWindow, dict[str, list[int]], list[int], int]] = {}
    for (_org, scenario, version), window in windows.items():
        key = (scenario, version)
        if key not in merged:
            merged[key] = (
                window,
                {n: list(c) for n, c in window.counts.items()},
                list(window.score_counts),
                window.rows,
            )
            continue
        first, counts, score_counts, total = merged[key]
        for name, window_counts in window.counts.items():
            counts[name] = [a + b for a, b in zip(counts[name], window_counts, strict=True)]
        score_counts = [a + b for a, b in zip(score_counts, window.score_counts, strict=True)]
        merged[key] = (first, counts, score_counts, total + window.rows)
    return merged


MONITOR = DriftMonitor()
SCORED_ROWS = Counter(
    "ai_circus_scored_rows", "Rows scored by prediction.", ["scenario", "model_variant"], registry=None
)
_registered: set[int] = set()


def register_drift_metrics(registry: CollectorRegistry = REGISTRY) -> DriftMonitor:
    """Add the drift collector and the scored-rows counter to `registry` (the one /metrics serves) — once."""
    if id(registry) not in _registered:
        registry.register(MONITOR)
        registry.register(SCORED_ROWS)
        _registered.add(id(registry))
    return MONITOR
