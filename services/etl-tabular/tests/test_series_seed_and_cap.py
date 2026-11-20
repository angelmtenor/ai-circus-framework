"""
- Title:    Tests for a gzipped series seed and a scenario's own row cap (docs/adr/0030)
- Author:   Angel Martinez-Tenor
"""

from __future__ import annotations

import gzip

import pandas as pd
import pytest
from ai_circus_shared.scenario_schema import TabularDataset, TimeSeriesConfig

from etl_tabular.core.etl import clean_series, load_raw

DATASET = TabularDataset(
    bucket="scenario-water",
    raw_object="raw/daily.csv",
    seed_file="sample_data/daily.csv.gz",
    index_col="date",
    target="pct",
    feature_columns=["series", "rain"],
    feature_schema={
        "series": {"type": "categorical", "label": "Series", "options": ["a", "b"], "default": "a"},
        "rain": {"type": "numeric", "label": "Rain", "min": 0, "max": 100, "default": 0},
    },
)


def _config(**extra: object) -> TimeSeriesConfig:
    return TimeSeriesConfig(
        time_column="date", series_id_columns=["series"], frequency="D", horizon=2, lags=[1],
        exogenous_features=["rain"], **extra,
    )  # fmt: skip


def _daily(days: int) -> pd.DataFrame:
    frames = [
        pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=days, freq="D").strftime("%Y-%m-%d"),
            "series": name,
            "rain": 0.0,
            "pct": [50.0 + i for i in range(days)],
        })
        for name in ("a", "b")
    ]
    return pd.concat(frames, ignore_index=True)


class _Store:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def get(self, org_id: str, path: str) -> bytes:
        return self.payload


def test_a_gzipped_seed_is_read_like_a_plain_one() -> None:
    plain = _daily(5).to_csv(index=False).encode()
    for payload in (plain, gzip.compress(plain)):
        df = load_raw(_Store(payload), "org", DATASET)  # type: ignore[arg-type]
        assert len(df) == 10 and list(df.columns) == ["series", "rain", "pct"] and df.index.name == "date"


def test_the_default_row_cap_cuts_a_long_series_to_its_most_recent_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("etl_tabular.core.etl.MAX_DATASET_ROWS", 20)
    cleaned = clean_series(_daily(30).set_index("date"), DATASET, _config())
    assert len(cleaned) == 20 and cleaned.index.min() == pd.Timestamp("2024-01-21")


def test_a_scenario_can_raise_its_own_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("etl_tabular.core.etl.MAX_DATASET_ROWS", 20)
    cleaned = clean_series(_daily(30).set_index("date"), DATASET, _config(max_rows=60))
    assert len(cleaned) == 60 and cleaned.index.min() == pd.Timestamp("2024-01-01")


def test_the_cap_a_scenario_may_ask_for_is_bounded() -> None:
    with pytest.raises(ValueError):
        _config(max_rows=10_000_000)
