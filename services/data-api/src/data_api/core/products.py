"""
- Title:    The data products this instance serves
- Author:   Angel Martinez-Tenor
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from ai_circus_shared.data_product import DataProductConfig
from ai_circus_shared.scenario_schema import ScenarioDefinition, resolve_scenarios

_SAFE_SLUG = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


@dataclass(frozen=True)
class DataProduct:
    """One served data product: its definition, config and scenario directory."""

    slug: str
    definition: ScenarioDefinition
    config: DataProductConfig
    scenario_dir: Path


def load_products(scenarios_dir: Path, raw_scenarios: str) -> dict[str, DataProduct]:
    """Every served data product — read once at start-up."""
    products = {}
    for slug, definition in resolve_scenarios(scenarios_dir, raw_scenarios, kind="data_product").items():
        if not _SAFE_SLUG.match(slug):
            raise ValueError(f"data_product slug {slug!r} is not a safe SQL identifier")
        assert definition.data_product is not None  # guaranteed by kind="data_product"
        products[slug] = DataProduct(slug, definition, definition.data_product, scenarios_dir / slug)
    return products
