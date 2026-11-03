"""Tests for core/drift_monitor.py — PSI of live scoring traffic on /metrics."""

from __future__ import annotations

import numpy as np
import pytest
from ai_circus_shared.drift import PSI_MODERATE, PSI_SIGNIFICANT, build_reference
from prometheus_client import CollectorRegistry

from prediction.core import drift_monitor
from prediction.core.drift_monitor import DriftMonitor

RNG = np.random.default_rng(3)
TRAIN_AGE = [float(v) for v in RNG.normal(40, 8, 3_000)]
TRAIN_PLAN = [str(v) for v in RNG.choice(["basic", "pro"], 3_000)]
TRAIN_SCORES = [float(v) for v in RNG.random(3_000)]
METADATA = {
    "model_checksums": {"pipeline": "abc"},
    "drift_reference": build_reference(
        {"age": TRAIN_AGE, "plan": TRAIN_PLAN},
        {"age": "numeric", "plan": "categorical"},
        ["age", "plan"],
        TRAIN_SCORES,
    ),
}


def _registry(monitor: DriftMonitor) -> CollectorRegistry:
    registry = CollectorRegistry()
    registry.register(monitor)
    return registry


def _records(n: int, age_mean: float) -> list[dict]:
    ages, plans = RNG.normal(age_mean, 8, n), RNG.choice(["basic", "pro"], n)
    return [{"age": float(ages[i]), "plan": str(plans[i]), "id": f"row-{i}"} for i in range(n)]


def _value(registry: CollectorRegistry, name: str, **labels: str) -> float | None:
    version = drift_monitor.model_version(METADATA)
    return registry.get_sample_value(name, {"scenario": "churn", "model_version": version, **labels})


def test_a_shifted_feature_shows_up_and_a_stable_one_does_not() -> None:
    monitor = DriftMonitor()
    monitor.observe("acme", "churn", METADATA, _records(500, age_mean=60), [float(v) for v in RNG.random(500)])
    registry = _registry(monitor)
    assert _value(registry, "ai_circus_model_feature_psi", feature="age") > PSI_SIGNIFICANT
    assert _value(registry, "ai_circus_model_feature_psi", feature="plan") < PSI_MODERATE
    assert _value(registry, "ai_circus_model_score_psi") < PSI_MODERATE
    assert _value(registry, "ai_circus_model_drift_window_rows") == 500


def test_no_psi_below_the_minimum_rows_but_the_window_size_is_reported() -> None:
    monitor = DriftMonitor()
    monitor.observe("acme", "churn", METADATA, _records(20, age_mean=60), [0.5] * 20)
    registry = _registry(monitor)
    assert _value(registry, "ai_circus_model_feature_psi", feature="age") is None
    assert _value(registry, "ai_circus_model_drift_window_rows") == 20


def test_a_model_without_a_reference_exports_nothing() -> None:
    monitor = DriftMonitor()
    monitor.observe("acme", "churn", {"model_checksums": {}}, _records(500, 40), [0.5] * 500)
    assert list(_registry(monitor).collect())[2].samples == []


def test_bulk_requests_are_sampled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(drift_monitor, "MAX_ROWS_PER_REQUEST", 100)
    monitor = DriftMonitor()
    monitor.observe("acme", "churn", METADATA, _records(1_000, 40), [0.5] * 1_000)
    assert _value(_registry(monitor), "ai_circus_model_drift_window_rows") == 100


def test_windows_are_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(drift_monitor, "MAX_WINDOWS", 2)
    monitor = DriftMonitor()
    for slug in ("a", "b", "c"):
        monitor.observe("acme", slug, METADATA, _records(10, 40), [0.5] * 10)
    scenarios = {s.labels["scenario"] for s in list(_registry(monitor).collect())[2].samples}
    assert scenarios == {"b", "c"}


def test_a_broken_reference_never_raises() -> None:
    DriftMonitor().observe(
        "acme", "churn", {"drift_reference": {"features": {"age": {"kind": "numeric"}}}}, _records(5, 40), [0.1] * 5
    )


def test_labels_never_carry_row_values_or_ids() -> None:
    monitor = DriftMonitor()
    monitor.observe("acme", "churn", METADATA, _records(200, 40), [0.5] * 200)
    labels = {value for family in _registry(monitor).collect() for s in family.samples for value in s.labels.values()}
    assert not any(label.startswith("row-") or label in {"basic", "pro", "acme"} for label in labels)


def test_a_tenants_report_reflects_its_own_traffic_only() -> None:
    monitor = DriftMonitor()
    monitor.observe("acme", "churn", METADATA, _records(400, age_mean=65), [0.5] * 400)  # shifted
    monitor.observe("globex", "churn", METADATA, _records(400, age_mean=40), [0.5] * 400)  # same model, calm
    acme, globex = monitor.report("acme", "churn", METADATA), monitor.report("globex", "churn", METADATA)
    assert acme["window_rows"] == globex["window_rows"] == 400
    assert acme["features"][0]["feature"] == "age" and acme["features"][0]["psi"] > PSI_SIGNIFICANT
    assert all(f["psi"] < PSI_MODERATE for f in globex["features"])
    assert monitor.report("initech", "churn", METADATA)["window_rows"] == 0  # no traffic of its own yet


def test_the_report_orders_features_worst_first_and_says_when_there_is_no_reference() -> None:
    monitor = DriftMonitor()
    monitor.observe("acme", "churn", METADATA, _records(300, age_mean=70), [0.5] * 300)
    report = monitor.report("acme", "churn", METADATA)
    psis = [f["psi"] for f in report["features"]]
    assert report["available"] and psis == sorted(psis, reverse=True)
    assert report["thresholds"] == {"moderate": PSI_MODERATE, "significant": PSI_SIGNIFICANT}
    assert monitor.report("acme", "churn", {"model_checksums": {}})["available"] is False


def test_metrics_sum_every_tenant_of_a_model() -> None:
    monitor = DriftMonitor()
    for org in ("acme", "globex"):
        monitor.observe(org, "churn", METADATA, _records(60, age_mean=40), [0.5] * 60)
    registry = _registry(monitor)
    assert _value(registry, "ai_circus_model_drift_window_rows") == 120
    assert _value(registry, "ai_circus_model_feature_psi", feature="age") is not None  # 120 rows ≥ MIN_ROWS
