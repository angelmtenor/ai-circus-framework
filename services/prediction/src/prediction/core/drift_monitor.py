"""
- Title:    Live model-drift metrics on /metrics (ADR 0015)
- Author:   Angel Martinez-Tenor

Every request the primary model scores is folded, after the response, into a bounded window per
(scenario, model version) — an `ai_circus_shared.drift.DriftWindow` against the `drift_reference` training
wrote into the model metadata — and Prometheus reads the Population Stability Index at scrape time:

- `ai_circus_model_feature_psi{scenario, model_version, feature}` — per profiled feature (top 8 by SHAP);
- `ai_circus_model_score_psi{scenario, model_version}` — of the served score itself;
- `ai_circus_model_drift_window_rows{scenario, model_version}` — rows behind those numbers (PSI needs ≥ 100);
- `ai_circus_scored_rows_total{scenario, model_variant}` — every scored row, challenger included.

Labels are scenario slugs, model versions and feature names only — never tenants, values or rows. A model
trained before drift references existed has none: its scenario simply exports no PSI until the next
`make k3s-pipeline`. Bulk requests are sampled (evenly spaced rows) so a 30,000-row dataset scoring costs
the background thread a few milliseconds, not seconds.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from ai_circus_shared.decision_records import model_version
from ai_circus_shared.drift import DriftWindow, sample_rows
from prometheus_client import Counter
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import REGISTRY, Collector, CollectorRegistry

from prediction.core.logger import get_logger

logger = get_logger(__name__)

MAX_ROWS_PER_REQUEST = 1_000
MAX_WINDOWS = 64  # (scenario, model version) pairs kept — the oldest goes first


class DriftMonitor(Collector):
    """The per-model drift windows, and the Prometheus collector that reads them."""

    def __init__(self) -> None:
        """Start with no windows; one appears on the first scored request of each model version."""
        self._lock = threading.Lock()
        self._windows: OrderedDict[tuple[str, str], DriftWindow] = OrderedDict()

    def observe(
        self, scenario: str, metadata: Mapping[str, Any], records: Sequence[Mapping[str, Any]], scores: Sequence[float]
    ) -> None:
        """Fold one scored request into its model's window — a no-op without a drift reference; never raises."""
        try:
            reference = metadata.get("drift_reference")
            if not reference or not records:
                return
            rows = sample_rows(len(records), MAX_ROWS_PER_REQUEST)
            columns = {name: [records[i].get(name) for i in rows] for name in reference.get("features") or {}}
            sampled_scores = [scores[i] for i in rows]
            key = (scenario, model_version(metadata))
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

    def collect(self) -> Iterator[GaugeMetricFamily]:
        """Yield the PSI gauges for every window, computed now from its bin counts."""
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
            "Scored rows in the current drift window (PSI is reported from 100 rows).",
            labels=["scenario", "model_version"],
        )
        with self._lock:
            windows = [(key, window, window.feature_psi(), window.score_psi()) for key, window in self._windows.items()]
        for (scenario, version), window, by_feature, score_psi in windows:
            rows.add_metric([scenario, version], float(window.rows))
            for feature, value in by_feature.items():
                features.add_metric([scenario, version, feature], value)
            if score_psi is not None:
                score.add_metric([scenario, version], score_psi)
        yield features
        yield score
        yield rows


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
