"""
- Title:    The data products this instance serves
- Author:   Angel Martinez-Tenor

Every `kind: data_product` scenario under SCENARIOS_DIR (or the SCENARIOS subset), with its
contracts compiled once. Read at start-up only — a new data product is a pod restart, the same
rule every other kind's service follows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from ai_circus_shared.contracts import Contract
from ai_circus_shared.data_product import DataProductConfig, KafkaSource
from ai_circus_shared.scenario_schema import ScenarioDefinition, resolve_scenarios

_SAFE_SLUG = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


@dataclass
class DataProduct:
    """One data product with its compiled contracts."""

    slug: str
    definition: ScenarioDefinition
    config: DataProductConfig
    scenario_dir: Path
    contracts: dict[str, Contract] = field(default_factory=dict)  # kafka source id -> contract

    @property
    def schema(self) -> str:
        """Its Postgres schema (the validated slug)."""
        return self.slug  # one Postgres schema per data product (validated safe below)

    def kafka_source_for_topic(self, topic: str) -> KafkaSource | None:
        """The kafka source publishing to `topic`, if any."""
        return next((s for s in self.config.kafka_sources() if s.topic == topic), None)


def load_products(scenarios_dir: Path, raw_scenarios: str) -> dict[str, DataProduct]:
    """Every served data product, contracts compiled — read once at start-up."""
    products = {}
    for slug, definition in resolve_scenarios(scenarios_dir, raw_scenarios, kind="data_product").items():
        if not _SAFE_SLUG.match(slug):
            raise ValueError(f"data_product slug {slug!r} is not a safe SQL identifier")
        config = definition.data_product
        assert config is not None  # guaranteed by kind="data_product"
        scenario_dir = scenarios_dir / slug
        product = DataProduct(slug=slug, definition=definition, config=config, scenario_dir=scenario_dir)
        product.contracts = {s.id: Contract.for_source(s, scenario_dir) for s in config.kafka_sources()}
        products[slug] = product
    return products
