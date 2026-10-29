"""
- Title:    data-ingest runtime — the four workers and their shared clients
- Author:   Angel Martinez-Tenor

Built once in the app's lifespan: databases + per-product tables bootstrapped, then the stream,
landing, CDC and lake workers started as daemon threads. The API reads their counters.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path

from ai_circus_shared.data_product import DATA_BUCKET
from ai_circus_shared.db import connect_engine, database_url
from ai_circus_shared.startup import wait_for
from ai_circus_shared.storage import ObjectStore
from confluent_kafka import Consumer, Producer
from sqlalchemy import Engine, create_engine
from sqlalchemy.exc import OperationalError

from data_ingest.core.cdc_capture import CdcWorker
from data_ingest.core.files import FileLoader
from data_ingest.core.lake import LakeArchiver, lake_catalog
from data_ingest.core.logger import get_logger
from data_ingest.core.pipeline_events import PipelineEvents
from data_ingest.core.products import DataProduct, load_products
from data_ingest.core.stream import StreamWorker
from data_ingest.core.warehouse import bootstrap_product, ensure_databases
from data_ingest.data_model import EnvConfig

logger = get_logger(__name__)


@dataclass
class Runtime:
    """The data products, engines and the four workers of one data-ingest process."""

    products: dict[str, DataProduct]
    warehouse: Engine
    sources: Engine
    stream: StreamWorker
    files: FileLoader
    cdc: CdcWorker
    lake: LakeArchiver
    threads: list[threading.Thread] = field(default_factory=list)

    def start(self) -> None:
        """Start every worker as a daemon thread."""
        for name, worker in (("stream", self.stream), ("landing", self.files), ("cdc", self.cdc), ("lake", self.lake)):
            thread = threading.Thread(target=worker.run, name=f"data-ingest-{name}", daemon=True)
            thread.start()
            self.threads.append(thread)

    def stop(self) -> None:
        """Ask every worker to stop."""
        for worker in (self.stream, self.files, self.cdc, self.lake):
            worker.stop()

    def alive(self) -> dict[str, bool]:
        """Whether each worker thread is still running (the health check)."""
        return {t.name.removeprefix("data-ingest-"): t.is_alive() for t in self.threads}


def build_runtime(config: EnvConfig) -> Runtime:
    """Bootstrap databases and per-product tables, connect every client, build the workers."""
    products = load_products(Path(config.SCENARIOS_DIR), config.SCENARIOS or "")
    url = database_url(config)
    wait_for(
        lambda: ensure_databases(url, [config.POSTGRES_DB, config.SOURCES_DB]),
        what="Postgres",
        retryable=lambda exc: isinstance(exc, OperationalError),
    )
    warehouse = connect_engine(config)
    sources = create_engine(url.set(database=config.SOURCES_DB), pool_pre_ping=True, pool_size=2)
    for product in products.values():
        bootstrap_product(warehouse, product)
    secret = config.OBJECT_STORE_SECRET_KEY.get_secret_value()
    store = ObjectStore.connect(
        bucket=DATA_BUCKET,
        endpoint_url=config.OBJECT_STORE_ENDPOINT,
        access_key=config.OBJECT_STORE_ACCESS_KEY,
        secret_key=secret,
    )
    kafka = config.KAFKA_BOOTSTRAP_SERVERS
    producer = Producer({
        "bootstrap.servers": kafka,
        "linger.ms": 50,
        "queue.buffering.max.messages": 20_000,
        "queue.buffering.max.kbytes": 16_384,
    })
    consumer = Consumer({
        "bootstrap.servers": kafka,
        "group.id": "data-ingest",
        "auto.offset.reset": "earliest",
        "enable.auto.commit": False,
        # New tenants' topics match the regex subscription within seconds, not the 5-minute default.
        "topic.metadata.refresh.interval.ms": 10_000,
        # librdkafka prefetches up to 64 MB *per partition* by default — with a topic per source
        # and tenant that alone outgrew the pod's memory limit during a backfill.
        "queued.max.messages.kbytes": 8_192,
        "fetch.max.bytes": 8_388_608,
    })
    events = PipelineEvents(producer)
    stream = StreamWorker(consumer, warehouse, products, events)
    files = FileLoader(store, warehouse, products, stream, events, float(config.LANDING_SCAN_SECONDS))
    cdc = CdcWorker(sources, warehouse, products, events, float(config.CDC_POLL_SECONDS))
    lake = LakeArchiver(
        lambda: lake_catalog(url, config.OBJECT_STORE_ENDPOINT, config.OBJECT_STORE_ACCESS_KEY, secret),
        warehouse,
        products,
        events,
        float(config.LAKE_ARCHIVE_SECONDS),
    )
    logger.info("data-ingest serving {} data product(s): {}", len(products), sorted(products))
    return Runtime(products, warehouse, sources, stream, files, cdc, lake)
