"""
- Title:    data-api runtime — shared clients built once at start-up
- Author:   Angel Martinez-Tenor
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
from ai_circus_shared.data_product import DATA_BUCKET
from ai_circus_shared.db import connect_engine, database_url
from ai_circus_shared.storage import ObjectStore
from confluent_kafka import Producer
from sqlalchemy import create_engine

from data_api.core.events_relay import EventsRelay
from data_api.core.logger import get_logger
from data_api.core.products import DataProduct, load_products
from data_api.core.simulation import SimulationManager
from data_api.core.warehouse import ClickHouseReader, Warehouse
from data_api.data_model import EnvConfig

logger = get_logger(__name__)
TIER_PROBE_TTL = 15.0


@dataclass
class Runtime:
    """The served data products and the clients every route shares."""

    products: dict[str, DataProduct]
    warehouse: Warehouse
    simulation: SimulationManager
    relay: EventsRelay
    tier_urls: dict[str, str]
    store: ObjectStore | None = None
    _tiers: tuple[float, dict[str, bool]] = field(default=(0.0, {}))

    def tiers(self) -> dict[str, bool]:
        """Which optional tiers answer (cached): core is this API itself."""
        stamp, cached = self._tiers
        if time.monotonic() - stamp < TIER_PROBE_TTL:
            return cached
        result = {"core": True}
        for name, url in self.tier_urls.items():
            try:
                result[name] = httpx.get(url, timeout=1.5).status_code < 500
            except httpx.HTTPError:
                result[name] = False
        self._tiers = (time.monotonic(), result)
        return result


def parse_tier_urls(raw: str | None) -> dict[str, str]:
    """`analytics=http://…,bi=http://…` -> {name: url}."""
    pairs = (part.split("=", 1) for part in (raw or "").split(",") if "=" in part)
    return {name.strip(): url.strip() for name, url in pairs if name.strip() and url.strip()}


def build_runtime(config: EnvConfig) -> Runtime:
    """Connect Postgres, ClickHouse (analytics tier), SeaweedFS and Kafka; start the events relay."""
    products = load_products(Path(config.SCENARIOS_DIR), config.SCENARIOS or "")
    engine = connect_engine(config)
    clickhouse = None
    marts_engine = config.MARTS_ENGINE or "postgres"
    if marts_engine == "clickhouse" and config.CLICKHOUSE_URL:
        password = config.CLICKHOUSE_PASSWORD.get_secret_value() if config.CLICKHOUSE_PASSWORD else None
        clickhouse = ClickHouseReader(config.CLICKHOUSE_URL, config.CLICKHOUSE_USER, password)
    warehouse = Warehouse(engine, marts_engine, clickhouse)
    sources = create_engine(database_url(config).set(database=config.SOURCES_DB), pool_pre_ping=True, pool_size=3)
    store = ObjectStore.connect(
        bucket=DATA_BUCKET,
        endpoint_url=config.OBJECT_STORE_ENDPOINT,
        access_key=config.OBJECT_STORE_ACCESS_KEY,
        secret_key=config.OBJECT_STORE_SECRET_KEY.get_secret_value(),
    )
    producer = Producer({
        "bootstrap.servers": config.KAFKA_BOOTSTRAP_SERVERS,
        "linger.ms": 20,
        "queue.buffering.max.messages": 100_000,
        "compression.type": "lz4",
    })
    relay = EventsRelay(config.KAFKA_BOOTSTRAP_SERVERS)
    relay.start()
    logger.info(
        "data-api serving {} data product(s) from {} marts: {}", len(products), warehouse.marts_engine, sorted(products)
    )
    return Runtime(
        products,
        warehouse,
        SimulationManager(products, producer, store, sources),
        relay,
        parse_tier_urls(config.DATA_TIER_URLS),
        store=store,
    )


def describe_product(product: DataProduct) -> dict[str, Any]:
    """The parts of the data product block the workspace needs in /overview."""
    config = product.config
    return {
        "slug": product.slug,
        "sources": [s.model_dump() for s in config.sources],
        "hot_path": [
            {
                "name": a.name,
                "label": a.label,
                "dimensions": list(a.dimensions),
                "measures": [m.name for m in a.measures],
            }
            for a in config.hot_path
        ],
        "stage_labels": config.stage_labels,
    }
