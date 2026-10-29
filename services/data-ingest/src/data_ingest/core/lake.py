"""
- Title:    Bronze → Iceberg lake archive
- Author:   Angel Martinez-Tenor

Every minute, appends the bronze rows not archived yet to one Apache Iceberg table per kafka
source — `<slug>_bronze.<source>` in a PyIceberg SQL catalog stored in the warehouse database,
data as Parquet in bucket `ai-circus-data` (docs/adr/0004), partitioned by event day. It is the
open, engine-independent copy of everything ever received: DuckDB, Spark or Trino read it
without the platform, and a full rebuild can start from it.

Best-effort by design: an object-store hiccup leaves `archived_at` NULL and the rows are picked
up next round; it never blocks or loses a bronze write. A correction re-arms `archived_at`, so
the lake also keeps every version a record ever had (append-only, newest wins by received_at).
The table holds every tenant's rows (an `org_id` column, like the warehouse tables) — it is an
admin/engineering surface; tenants read data through data-api only.
"""

from __future__ import annotations

import threading
from typing import Any

import pyarrow as pa
from ai_circus_shared.data_product import DATA_BUCKET, bronze_table
from pyiceberg.catalog import Catalog
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.transforms import DayTransform
from pyiceberg.types import IntegerType, NestedField, StringType, TimestamptzType
from sqlalchemy import Engine, text
from sqlalchemy.engine import URL

from data_ingest.core.logger import get_logger
from data_ingest.core.pipeline_events import PipelineEvents
from data_ingest.core.products import DataProduct

logger = get_logger(__name__)

MAX_ROWS_PER_APPEND = 10_000  # each append holds the rows ~3x (rows, Arrow, Parquet buffers)
ICEBERG_SCHEMA = Schema(
    NestedField(1, "org_id", StringType(), required=True),
    NestedField(2, "business_key", StringType(), required=True),
    NestedField(3, "version", IntegerType(), required=True),
    NestedField(4, "event_ts", TimestamptzType(), required=False),
    NestedField(5, "received_at", TimestamptzType(), required=True),
    NestedField(6, "source_kind", StringType(), required=True),
    NestedField(7, "delivery", StringType(), required=False),
    NestedField(8, "payload", StringType(), required=True),
)
ARROW_SCHEMA = pa.schema([
    pa.field("org_id", pa.string(), nullable=False),
    pa.field("business_key", pa.string(), nullable=False),
    pa.field("version", pa.int32(), nullable=False),
    pa.field("event_ts", pa.timestamp("us", tz="UTC")),
    pa.field("received_at", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("source_kind", pa.string(), nullable=False),
    pa.field("delivery", pa.string()),
    pa.field("payload", pa.string(), nullable=False),
])
PARTITION_SPEC = PartitionSpec(PartitionField(source_id=4, field_id=1000, transform=DayTransform(), name="event_day"))


def lake_catalog(postgres_url: URL, endpoint: str, access_key: str, secret_key: str) -> Catalog:
    """The PyIceberg SQL catalog (metadata in Postgres, data in the data bucket)."""
    return SqlCatalog(
        "data_platform",
        uri=postgres_url.render_as_string(hide_password=False),
        warehouse=f"s3://{DATA_BUCKET}/lake",
        **{
            "s3.endpoint": endpoint,
            "s3.access-key-id": access_key,
            "s3.secret-access-key": secret_key,
            "s3.path-style-access": "true",
            "s3.region": "us-east-1",
        },
    )


def lake_namespace(product: DataProduct) -> str:
    """Iceberg namespace of a data product's bronze archive."""
    return f"{product.slug}_bronze"


class LakeArchiver:
    """Appends not-yet-archived bronze rows to the Iceberg lake every interval."""

    def __init__(
        self,
        catalog_factory: Any,
        engine: Engine,
        products: dict[str, DataProduct],
        events: PipelineEvents,
        interval_seconds: float,
    ) -> None:
        """Bind the archiver to its catalog factory and the warehouse."""
        self._catalog_factory, self._engine, self._products = catalog_factory, engine, products
        self._events, self._interval = events, interval_seconds
        self._stop = threading.Event()
        self.archived = 0
        self.last_error: str | None = None

    def stop(self) -> None:
        """Ask the loop to exit after its current round."""
        self._stop.set()

    def run(self) -> None:
        """Archive every interval until stopped (first round after a short delay)."""
        self._stop.wait(15)  # let the stream land something first
        while not self._stop.is_set():
            try:
                self.archive_all()
                self.last_error = None
            except Exception as exc:
                self.last_error = str(exc)[:300]
                logger.exception("Lake archive round failed")
            self._stop.wait(self._interval)

    def archive_all(self) -> int:
        """One round over every data product and source; returns the rows archived."""
        catalog = self._catalog_factory()
        total = 0
        for product in self._products.values():
            catalog.create_namespace_if_not_exists(lake_namespace(product))
            for source in product.config.kafka_sources():
                while not self._stop.is_set():
                    n = self._archive(catalog, product, source.id)
                    total += n
                    if n < MAX_ROWS_PER_APPEND:
                        break
        self.archived += total
        return total

    def _archive(self, catalog: Catalog, product: DataProduct, source_id: str) -> int:
        bronze = f'"{product.schema}"."{bronze_table(source_id)}"'
        with self._engine.begin() as conn:
            rows = conn.execute(
                text(f"""SELECT org_id, business_key, version, event_ts, received_at, source_kind, delivery,
                                payload::text
                         FROM {bronze} WHERE archived_at IS NULL ORDER BY received_at LIMIT :n
                         FOR UPDATE SKIP LOCKED"""),
                {"n": MAX_ROWS_PER_APPEND},
            ).all()
            if not rows:
                return 0
            columns = list(zip(*rows, strict=True))
            batch = pa.Table.from_arrays(
                [pa.array(list(c), type=f.type) for c, f in zip(columns, ARROW_SCHEMA, strict=True)],
                schema=ARROW_SCHEMA,
            )
            identifier = f"{lake_namespace(product)}.{source_id}"
            table = catalog.create_table_if_not_exists(identifier, schema=ICEBERG_SCHEMA, partition_spec=PARTITION_SPEC)
            table.append(batch)
            # Marked in the same transaction that locked the rows: a failed append rolls back
            # and they are retried; a crash after the append re-appends (the lake is
            # append-only and readers take the newest received_at per key).
            conn.execute(
                text(f"""UPDATE {bronze} SET archived_at = now()
                         WHERE (org_id, business_key, version) IN (
                             SELECT * FROM unnest(CAST(:o AS text[]), CAST(:k AS text[]), CAST(:v AS int[])))"""),
                {"o": list(columns[0]), "k": list(columns[1]), "v": list(columns[2])},
            )
        for org_id in sorted(set(columns[0])):
            self._events.batch(
                org_id, product.slug, "lake", "ok", sum(1 for o in columns[0] if o == org_id), {"table": identifier}
            )
        self._events.flush()
        return len(rows)
