"""
- Title:    Feature marts → the raw datasets of the tabular_ml scenarios they train
- Author:   Angel Martinez-Tenor

A data product's `ml_exports` name a gold feature mart and the `tabular_ml` scenario it trains.
After a successful Postgres build, each mart is written — that scenario's index, feature,
display and target columns, rows with a target only — as the scenario's raw CSV for every
tenant with data (`tenant-<org>/<raw_object>` in the scenario's bucket). etl-tabular,
training and prediction then work exactly as for a scenario shipped with a CSV.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from pathlib import Path

from ai_circus_shared.scenario_schema import ScenarioDefinition, resolve_scenarios
from ai_circus_shared.storage import ObjectStore
from sqlalchemy import Engine, text

from data_transform.core.logger import get_logger
from data_transform.core.products import DataProduct

logger = get_logger(__name__)
MAX_ROWS = 500_000  # a feature mart is aggregated; anything bigger is a modelling mistake


def _children(scenarios_dir: Path) -> dict[str, ScenarioDefinition]:
    # Only tabular_ml files are validated: another kind's newer schema never breaks this service.
    return resolve_scenarios(scenarios_dir, "", kind="tabular_ml")


def export_features(
    engine: Engine,
    store_for: Callable[[str], ObjectStore],
    product: DataProduct,
    scenarios_dir: Path,
    tenants: list[str],
) -> list[str]:
    """Write every ml_export of `product` for every tenant; returns the object keys written."""
    if not product.config.ml_exports:
        return []
    children = _children(scenarios_dir)
    keys: list[str] = []
    for export in product.config.ml_exports:
        child = children.get(export.scenario)
        if child is None or child.dataset is None:
            logger.warning("ml_export {} → {}: no such tabular_ml scenario", export.dataset, export.scenario)
            continue
        ds = child.dataset
        columns = list(dict.fromkeys([ds.index_col, *ds.display_columns, *ds.feature_columns, ds.target]))
        quoted = ", ".join(f'"{c}"' for c in columns)
        store = store_for(ds.bucket)
        for org in tenants:
            with engine.connect() as conn:
                result = conn.execute(
                    text(
                        f'SELECT {quoted} FROM "{product.slug}_gold"."{export.dataset}" '
                        f'WHERE org_id = :org AND "{ds.target}" IS NOT NULL ORDER BY "{ds.index_col}" LIMIT :cap'
                    ),
                    {"org": org, "cap": MAX_ROWS},
                )
                buffer = io.StringIO()
                writer = csv.writer(buffer)
                writer.writerow(columns)
                n = 0
                for row in result:
                    writer.writerow(row)
                    n += 1
            if n == 0:
                continue
            keys.append(store.put(org, ds.raw_object, buffer.getvalue().encode()))
            logger.info("ml_export {} → {} for {}: {} rows", export.dataset, export.scenario, org, n)
    return keys
