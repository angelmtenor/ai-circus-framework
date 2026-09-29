"""Tests for the tabular_ml ETL pipeline, against a fake in-memory object store."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pandas as pd
import pytest
from ai_circus_shared.network_graph import NetworkGraph
from ai_circus_shared.scenario_schema import TabularDataset, TabularGraph
from ai_circus_shared.tabular_ml import GRAPH_KEY, MAX_DATASET_ROWS
from pydantic import ValidationError

from etl_tabular.core.etl import clean, ensure_raw_dataset, load_raw, process_graph, run_etl, save_normalized

DATASET = TabularDataset(
    bucket="scenario-churn",
    raw_object="raw/customers.csv",
    seed_file="sample_data/customers.csv",
    index_col="CustomerId",
    target="Exited",
    protected_features_excluded=["Gender"],
    feature_columns=["CreditScore", "Geography", "Age"],
    feature_schema={
        "CreditScore": {"type": "numeric", "label": "Credit score", "min": 300, "max": 850, "default": 650},
        "Geography": {"type": "categorical", "label": "Country", "options": ["France", "Spain"], "default": "France"},
        "Age": {"type": "numeric", "label": "Age", "min": 18, "max": 92, "default": 40},
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


def test_clean_casts_boolean_feature_columns_to_category() -> None:
    """A bool-dtype feature column (e.g. is_holiday) is treated as categorical, not numeric.

    pandas' is_numeric_dtype() returns True for bool columns, so a naive check would
    leave them uncast — silently mismatching the "True"/"False" string options declared
    in feature_schema and breaking downstream filtering/training.
    """
    dataset = DATASET.model_copy(
        update={
            "feature_columns": ["CreditScore", "IsHoliday"],
            "feature_schema": {
                "CreditScore": {"type": "numeric", "label": "Credit score", "min": 300, "max": 850, "default": 650},
                "IsHoliday": {
                    "type": "categorical",
                    "label": "Holiday",
                    "options": ["False", "True"],
                    "default": "False",
                },
            },
        }
    )
    df = pd.DataFrame(
        {"CreditScore": [600, 650, 700], "IsHoliday": [True, False, True], "Exited": [0, 1, 0]},
        index=pd.Index([1, 2, 3], name="CustomerId"),
    )

    cleaned = clean(df, dataset)

    assert cleaned["IsHoliday"].dtype.name == "category"


def test_clean_boolean_feature_column_stays_categorical_through_parquet_round_trip() -> None:
    """A `category` dtype whose categories are themselves bool doesn't survive a
    parquet round-trip: pyarrow silently reads it back as plain `bool` (see pandas
    issue with categorical(bool) columns), which then breaks training's
    dtype-based numeric/categorical split (SimpleImputer rejects bool outright).
    clean() must cast bool -> str -> category, not bool -> category directly, so the
    dtype actually survives being written to and read back from SeaweedFS.
    """
    dataset = DATASET.model_copy(
        update={
            "feature_columns": ["CreditScore", "IsHoliday"],
            "feature_schema": {
                "CreditScore": {"type": "numeric", "label": "Credit score", "min": 300, "max": 850, "default": 650},
                "IsHoliday": {
                    "type": "categorical",
                    "label": "Holiday",
                    "options": ["False", "True"],
                    "default": "False",
                },
            },
        }
    )
    df = pd.DataFrame(
        {"CreditScore": [600, 650, 700], "IsHoliday": [True, False, True], "Exited": [0, 1, 0]},
        index=pd.Index([1, 2, 3], name="CustomerId"),
    )

    cleaned = clean(df, dataset)
    buffer = io.BytesIO()
    cleaned.to_parquet(buffer)
    buffer.seek(0)
    restored = pd.read_parquet(buffer)

    assert restored["IsHoliday"].dtype.name == "category"


def test_clean_downsamples_a_dataset_larger_than_the_row_cap() -> None:
    """A dataset larger than MAX_DATASET_ROWS is downsampled to exactly that many rows."""
    n = MAX_DATASET_ROWS + 5000
    df = pd.DataFrame(
        {"CreditScore": range(n), "Geography": ["France"] * n, "Age": range(n), "Exited": [0] * n},
        index=pd.Index(range(n), name="CustomerId"),
    )

    cleaned = clean(df, DATASET)

    assert len(cleaned) == MAX_DATASET_ROWS


def test_save_normalized_writes_parquet_readable_back(scenario_dir: Path) -> None:
    """save_normalized round-trips a DataFrame through SeaweedFS as parquet."""
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


def test_clean_keeps_distinct_ids_that_share_every_feature_value() -> None:
    """Two different records (ids 1 and 2) with identical features are both real rows
    — only a repeat of the same id+values is a duplicate (see clean()'s docstring).
    """
    raw = pd.read_csv(
        io.StringIO(
            "CustomerId,CreditScore,Geography,Gender,Age,Exited\n"
            "1,600,France,Female,40,0\n"
            "2,600,France,Male,40,0\n"
            "2,600,France,Male,40,0\n"
        ),
        index_col="CustomerId",
    )
    cleaned = clean(raw, DATASET)
    assert list(cleaned.index) == [1, 2]


def test_clean_carries_display_columns_but_not_protected_ones() -> None:
    """display_columns ride along for the UI; protected columns are still dropped."""
    dataset = DATASET.model_copy(update={"display_columns": ["Surname"]})
    raw = pd.read_csv(
        io.StringIO(
            "CustomerId,Surname,CreditScore,Geography,Gender,Age,Exited\n"
            "1,Smith,600,France,Female,40,0\n"
            "2,Jones,650,Spain,Male,35,1\n"
        ),
        index_col="CustomerId",
    )
    cleaned = clean(raw, dataset)
    assert list(cleaned.columns) == ["Surname", "CreditScore", "Geography", "Age", "Exited"]
    assert list(cleaned["Surname"]) == ["Smith", "Jones"]


def test_clean_keeps_text_features_as_trimmed_truncated_strings(scenario_dir: Path) -> None:
    """A `type: text` feature is never cast to category (that would one-hot every
    unique review): it stays a plain string, missing -> "", cut at max_length, and
    survives the parquet round-trip as a string.
    """
    dataset = TabularDataset.model_validate({
        **DATASET.model_dump(),
        "feature_columns": [*DATASET.feature_columns, "Review"],
        "feature_schema": {
            **DATASET.model_dump()["feature_schema"],
            "Review": {"type": "text", "label": "Review", "max_length": 12},
        },
    })
    raw = pd.read_csv(
        io.StringIO(
            "CustomerId,CreditScore,Geography,Gender,Age,Review,Exited\n"
            '1,600,France,Female,40,"  Great manager, listens to the team  ",0\n'
            "2,650,Spain,Male,35,,1\n"
        ),
        index_col="CustomerId",
    )
    cleaned = clean(raw, dataset)
    assert list(cleaned.index) == [1, 2]  # an empty review is not a missing record
    assert list(cleaned["Review"]) == ["Great manage", ""]
    assert not isinstance(cleaned["Review"].dtype, pd.CategoricalDtype)
    assert isinstance(cleaned["Geography"].dtype, pd.CategoricalDtype)

    store = FakeObjectStore()
    save_normalized(store, "acme", cleaned)  # type: ignore[arg-type]
    round_trip = pd.read_parquet(io.BytesIO(store.get("acme", "processed/normalized.parquet")))
    assert list(round_trip["Review"]) == ["Great manage", ""]


# --- dataset.graph ---------------------------------------------------------------

GRAPH_DATASET = DATASET.model_copy(
    update={"graph": TabularGraph(seed_file="sample_data/g.json", raw_object="raw/g.json")}
)
GRAPH = {
    "periods": ["2001-01"],
    "nodes": [
        {"id": "1", "kind": "row"},
        {"id": "2", "kind": "row"},
        {"id": "3", "kind": "row"},  # not in SAMPLE_CSV: etl drops it with its edges
        {"id": "ctx-1", "kind": "context"},
        {"id": "ljm", "kind": "entity", "label": "LJM"},
    ],
    "edges": [
        {"source": "1", "target": "2", "kind": "email", "weight": 4, "series": {"2001-01": 4}},
        {"source": "3", "target": "ctx-1", "kind": "email", "weight": 3},
        {"source": "3", "target": "ljm", "kind": "role", "label": "partner"},
    ],
}


@pytest.fixture
def graph_scenario_dir(scenario_dir: Path) -> Path:
    (scenario_dir / "sample_data" / "g.json").write_text(json.dumps(GRAPH))
    return scenario_dir


def test_run_etl_writes_the_graph_restricted_to_the_cleaned_rows(graph_scenario_dir: Path) -> None:
    store = FakeObjectStore()

    run_etl(store, "org-1", GRAPH_DATASET, graph_scenario_dir)

    graph = NetworkGraph.model_validate_json(store.get("org-1", GRAPH_KEY))
    assert [n.id for n in graph.nodes] == ["1", "2", "ljm"]  # row 3 gone; ctx-1 orphaned; entity kept
    assert [(e.source, e.target) for e in graph.edges] == [("1", "2")]
    assert store.exists("org-1", "raw/g.json")


def test_process_graph_never_overwrites_the_tenants_raw_graph(graph_scenario_dir: Path) -> None:
    store = FakeObjectStore()
    own = {"nodes": [{"id": "1", "kind": "row"}], "edges": []}
    store.put("org-1", "raw/g.json", json.dumps(own).encode())

    process_graph(store, "org-1", GRAPH_DATASET, graph_scenario_dir, pd.Index([1, 2]))

    assert json.loads(store.get("org-1", "raw/g.json")) == own
    assert [n.id for n in NetworkGraph.model_validate_json(store.get("org-1", GRAPH_KEY)).nodes] == ["1"]


def test_run_etl_without_a_graph_writes_none(scenario_dir: Path) -> None:
    store = FakeObjectStore()
    run_etl(store, "org-1", DATASET, scenario_dir)
    assert not store.exists("org-1", GRAPH_KEY)


def test_an_invalid_graph_fails_loudly_and_writes_nothing(scenario_dir: Path) -> None:
    (scenario_dir / "sample_data" / "g.json").write_text(
        json.dumps({"nodes": [], "edges": [{"source": "x", "target": "y", "kind": "e"}]})
    )
    store = FakeObjectStore()
    with pytest.raises(ValidationError, match="unknown node"):
        run_etl(store, "org-1", GRAPH_DATASET, scenario_dir)
    assert not store.exists("org-1", GRAPH_KEY)
