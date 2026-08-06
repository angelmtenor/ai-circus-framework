"""Tests for the tabular_ml ETL pipeline, against a fake in-memory object store."""

from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import pytest
from ai_circus_shared.scenario_schema import TabularDataset

from etl_tabular.core.etl import clean, ensure_raw_dataset, load_raw, run_etl, save_normalized

DATASET = TabularDataset(
    bucket="scenario-churn",
    raw_object="raw/customers.csv",
    seed_file="sample_data/customers.csv",
    index_col="CustomerId",
    target="Exited",
    protected_features_excluded=["Gender"],
    feature_columns=["CreditScore", "Geography", "Age"],
    feature_schema={
        "CreditScore": {"type": "numeric", "min": 300, "max": 850, "default": 650},
        "Geography": {"type": "categorical", "options": ["France", "Spain"], "default": "France"},
        "Age": {"type": "numeric", "min": 18, "max": 92, "default": 40},
    },
)

SAMPLE_CSV = (
    "CustomerId,CreditScore,Geography,Gender,Age,Exited\n"
    "1,600,France,Female,40,0\n"
    "2,650,Spain,Male,35,1\n"
    "2,650,Spain,Male,35,1\n"  # duplicate row, dropped by clean()
)


class FakeObjectStore:
    """In-memory stand-in for ai_circus_shared.storage.ObjectStore."""

    def __init__(self) -> None:
        """Start with an empty object map."""
        self._objects: dict[tuple[str, str], bytes] = {}

    def exists(self, org_id: str, path: str) -> bool:
        """Return whether an object was previously put() under this tenant/path."""
        return (org_id, path) in self._objects

    def put(self, org_id: str, path: str, data: bytes) -> str:
        """Store bytes under a tenant-scoped path; return a fake key."""
        self._objects[org_id, path] = data
        return f"tenant-{org_id}/{path}"

    def get(self, org_id: str, path: str) -> bytes:
        """Retrieve previously stored bytes."""
        return self._objects[org_id, path]


@pytest.fixture
def scenario_dir(tmp_path: Path) -> Path:
    """A scenario directory with a tracked sample_data/ CSV, mirroring the real repo layout."""
    sample_dir = tmp_path / "sample_data"
    sample_dir.mkdir()
    (sample_dir / "customers.csv").write_text(SAMPLE_CSV)
    return tmp_path


def test_ensure_raw_dataset_bootstraps_from_seed_file_when_missing(scenario_dir: Path) -> None:
    """If no raw object exists yet for the tenant, it's uploaded from the tracked seed file."""
    store = FakeObjectStore()

    ensure_raw_dataset(store, "org-1", DATASET, scenario_dir)

    assert store.exists("org-1", DATASET.raw_object)
    assert store.get("org-1", DATASET.raw_object).decode() == SAMPLE_CSV


def test_ensure_raw_dataset_leaves_existing_data_untouched(scenario_dir: Path) -> None:
    """An already-uploaded raw dataset for the tenant is never overwritten."""
    store = FakeObjectStore()
    store.put("org-1", DATASET.raw_object, b"already here")

    ensure_raw_dataset(store, "org-1", DATASET, scenario_dir)

    assert store.get("org-1", DATASET.raw_object) == b"already here"


def test_load_raw_reads_csv_indexed_by_configured_column() -> None:
    """load_raw parses the tenant's raw CSV into a DataFrame indexed by dataset.index_col."""
    store = FakeObjectStore()
    store.put("org-1", DATASET.raw_object, SAMPLE_CSV.encode())

    df = load_raw(store, "org-1", DATASET)

    assert df.index.name == "CustomerId"
    assert len(df) == 3


def test_clean_drops_protected_columns_duplicates_and_casts_categories() -> None:
    """clean() keeps only feature/target columns, dedupes, and casts non-numeric features."""
    df = pd.read_csv(io.StringIO(SAMPLE_CSV), index_col="CustomerId")

    cleaned = clean(df, DATASET)

    assert list(cleaned.columns) == ["CreditScore", "Geography", "Age", "Exited"]
    assert "Gender" not in cleaned.columns
    assert len(cleaned) == 2  # duplicate row removed
    assert cleaned["Geography"].dtype.name == "category"
    assert pd.api.types.is_numeric_dtype(cleaned["Age"])


def test_save_normalized_writes_parquet_readable_back(scenario_dir: Path) -> None:
    """save_normalized round-trips a DataFrame through MinIO as parquet."""
    store = FakeObjectStore()
    df = pd.read_csv(io.StringIO(SAMPLE_CSV), index_col="CustomerId")

    key = save_normalized(store, "org-1", df)

    assert key == "tenant-org-1/processed/normalized.parquet"
    restored = pd.read_parquet(io.BytesIO(store.get("org-1", "processed/normalized.parquet")))
    assert len(restored) == len(df)


def test_run_etl_end_to_end(scenario_dir: Path) -> None:
    """The full pipeline bootstraps, loads, cleans, and saves in one call."""
    store = FakeObjectStore()

    key = run_etl(store, "org-1", DATASET, scenario_dir)

    assert key.endswith("processed/normalized.parquet")
    restored = pd.read_parquet(io.BytesIO(store.get("org-1", "processed/normalized.parquet")))
    assert list(restored.columns) == ["CreditScore", "Geography", "Age", "Exited"]
    assert len(restored) == 2
