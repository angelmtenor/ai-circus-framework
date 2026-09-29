"""
- Title:    ETL pipeline for tabular_ml scenarios
- Author:   Angel Martinez-Tenor

Extract: bootstrap the tenant's raw dataset into SeaweedFS from the scenario's tracked
sample_data/ file on first run (demo convenience — a real deployment would have each
tenant upload their own data instead). Transform: drop protected/identifier columns
(Responsible AI) and cast dtypes. Load: write the normalized parquet back to SeaweedFS.
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pandas as pd
from ai_circus_shared.network_graph import parse_graph, restrict_to_rows
from ai_circus_shared.scenario_schema import TabularDataset
from ai_circus_shared.storage import ObjectStore
from ai_circus_shared.tabular_ml import GRAPH_KEY, MAX_DATASET_ROWS, NORMALIZED_DATASET_KEY

from etl_tabular.core.logger import get_logger

logger = get_logger(__name__)


def ensure_raw_dataset(store: ObjectStore, org_id: str, dataset: TabularDataset, scenario_dir: Path) -> None:
    """Upload the scenario's tracked sample dataset to SeaweedFS if the tenant has none yet."""
    if store.exists(org_id, dataset.raw_object):
        return

    seed_path = scenario_dir / dataset.seed_file
    logger.warning(
        "No raw dataset found for org={} at {} — bootstrapping from tracked seed file {} (demo convenience).",
        org_id,
        dataset.raw_object,
        seed_path,
    )
    store.put(org_id, dataset.raw_object, seed_path.read_bytes())


def load_raw(store: ObjectStore, org_id: str, dataset: TabularDataset) -> pd.DataFrame:
    """Load the tenant's raw CSV dataset from SeaweedFS into a DataFrame."""
    raw_bytes = store.get(org_id, dataset.raw_object)
    df = pd.read_csv(io.BytesIO(raw_bytes), index_col=dataset.index_col)
    logger.info("Loaded raw dataset for org={}: {} rows, {} columns", org_id, *df.shape)
    return df


def clean(df: pd.DataFrame, dataset: TabularDataset) -> pd.DataFrame:
    """Select id/display/feature/target columns, drop protected columns, dedupe, and
    cast dtypes.

    Deliberately generic (not hardcoded to any one scenario's column names) so this
    same logic serves any future tabular_ml scenario: numeric columns keep their
    inferred dtype, non-numeric feature columns become `category` — except `type: text`
    features (free text, e.g. a review), which stay plain strings (missing = "",
    whitespace-trimmed, cut at the schema's `max_length`) for training's TF-IDF step.

    The row id (`index_col`, kept as the DataFrame index) is part of the dedupe key:
    two *different* records that merely share every feature value (e.g. two 3rd-class
    22-year-old men on the same fare) are real, distinct rows — only an exact repeat of
    the same record is a duplicate. `display_columns` (e.g. a name) ride along for the
    UI and are never selected as model inputs (training reads `feature_columns` only).
    """
    columns = [*dataset.display_columns, *dataset.feature_columns, dataset.target]
    selected = df.loc[:, columns].copy()
    text_columns = dataset.text_columns()
    for column in text_columns:
        # Casting before dedupe/dropna: an empty review is still a valid record.
        max_length = dataset.feature_schema[column].max_length  # type: ignore[union-attr]
        selected[column] = selected[column].fillna("").astype(str).str.strip().str.slice(0, max_length)
    df = selected[~selected.reset_index().duplicated().to_numpy()].dropna()

    if len(df) > MAX_DATASET_ROWS:
        # Evenly-spaced, not a head/tail slice or random sample — keeps the row cap
        # deterministic and representative of the full range (same technique
        # prediction/core/dataset.py uses for its own row-limited endpoints).
        idx = np.linspace(0, len(df) - 1, MAX_DATASET_ROWS, dtype=int)
        df = df.iloc[idx]
        logger.info("Capped dataset at {} rows (was larger)", MAX_DATASET_ROWS)

    for column in dataset.feature_columns:
        if column in text_columns:
            continue
        is_numeric = pd.api.types.is_numeric_dtype(df[column]) and not pd.api.types.is_bool_dtype(df[column])
        if not is_numeric:
            # bool columns go through str first: a `category` dtype whose categories
            # are themselves bool doesn't survive a parquet round-trip — pyarrow reads
            # it back as plain `bool` (losing the category cast entirely), which then
            # breaks training's categorical-feature pipeline (SimpleImputer rejects
            # bool dtype outright).
            if pd.api.types.is_bool_dtype(df[column]):
                df[column] = df[column].astype(str)
            df[column] = df[column].astype("category")

    logger.info(
        "Cleaned dataset: {} rows, {} columns (dropped protected columns: {})",
        df.shape[0],
        df.shape[1],
        ", ".join(dataset.protected_features_excluded) or "none",
    )
    return df


def save_normalized(store: ObjectStore, org_id: str, df: pd.DataFrame) -> str:
    """Write the cleaned DataFrame to SeaweedFS as parquet; return the object key."""
    buffer = io.BytesIO()
    df.to_parquet(buffer)
    key = store.put(org_id, NORMALIZED_DATASET_KEY, buffer.getvalue())
    logger.success("Saved normalized dataset for org={} to {}", org_id, key)
    return key


def process_graph(store: ObjectStore, org_id: str, dataset: TabularDataset, scenario_dir: Path, rows: pd.Index) -> str:
    """The scenario's network (`dataset.graph`), bootstrapped like the dataset (tracked
    seed file -> the tenant's raw object, once), validated against the shared contract
    and restricted to the rows that survived `clean()`; return the output object key.

    Raises:
        pydantic.ValidationError: the raw graph breaks the contract (unknown node in an
            edge, too many nodes, ...) — nothing is written.
    """
    graph_config = dataset.graph
    assert graph_config is not None
    if not store.exists(org_id, graph_config.raw_object):
        seed_path = scenario_dir / graph_config.seed_file
        logger.warning(
            "No raw graph found for org={} at {} — bootstrapping from tracked seed file {} (demo convenience).",
            org_id,
            graph_config.raw_object,
            seed_path,
        )
        store.put(org_id, graph_config.raw_object, seed_path.read_bytes())
    graph = restrict_to_rows(parse_graph(store.get(org_id, graph_config.raw_object)), rows)
    key = store.put(org_id, GRAPH_KEY, graph.model_dump_json(exclude_none=True).encode())
    logger.success("Saved graph for org={} to {}: {} nodes, {} edges", org_id, key, len(graph.nodes), len(graph.edges))
    return key


def run_etl(store: ObjectStore, org_id: str, dataset: TabularDataset, scenario_dir: Path) -> str:
    """Run the full extract -> transform -> load pipeline; return the output object key."""
    ensure_raw_dataset(store, org_id, dataset, scenario_dir)
    df = load_raw(store, org_id, dataset)
    df = clean(df, dataset)
    key = save_normalized(store, org_id, df)
    if dataset.graph is not None:
        process_graph(store, org_id, dataset, scenario_dir, df.index)
    return key
