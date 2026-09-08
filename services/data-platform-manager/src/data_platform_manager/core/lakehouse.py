"""
- Title:    Lakehouse table format (Apache Iceberg)
- Author:   ai-circus-framework contributors

Versioned, ACID tables over the *existing* object store — no new stateful
container. The catalog (table metadata: names, schemas, current snapshot) is
a PyIceberg `SqlCatalog` living in this service's own Postgres database
(reusing ai_circus_shared.document_store's same POSTGRES_* connection, just a
different logical concern — PyIceberg creates and owns its own
`iceberg_tables`/`iceberg_namespace_properties` tables there); the actual data
(Parquet files + JSON table-metadata files) is Iceberg's own `S3FileIO`
pointed at this repo's own SeaweedFS, the same S3-compatible endpoint every
other service already writes datasets/models to (see
ai_circus_shared.storage) — just a different bucket
(`ai-circus-lakehouse`), auto-created on first use exactly like
ai_circus_shared.storage.ObjectStore.connect() creates its own.

The demo source is the same `documents` table CDC reads from
(ai_circus_shared.document_store) — `ingest()` below snapshots one
tenant/collection's current rows into an Iceberg table, so every ingest run
after the first shows up as a genuinely new, queryable snapshot (see
`table_info`'s snapshot count), not just an overwrite.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Protocol

import pyarrow as pa
from ai_circus_shared.document_store import DocumentStore
from pyiceberg.catalog import Catalog
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.exceptions import NoSuchTableError

_NAMESPACE = "lakehouse"
_WAREHOUSE_BUCKET = "ai-circus-lakehouse"

_SCHEMA = pa.schema([
    ("org_id", pa.string()),
    ("collection", pa.string()),
    ("doc_id", pa.string()),
    ("content", pa.string()),  # JSON-encoded — Iceberg/Arrow don't need to know the document's own shape
    ("snapshotted_at", pa.string()),
])


class LakehouseConfig(Protocol):
    """Shape a service's own EnvConfig must satisfy to call get_catalog()."""

    POSTGRES_HOST: str
    POSTGRES_PORT: str
    POSTGRES_DB: str
    POSTGRES_USER: str
    POSTGRES_PASSWORD: Any  # pydantic.SecretStr
    OBJECT_STORE_ENDPOINT: str
    OBJECT_STORE_ACCESS_KEY: str
    OBJECT_STORE_SECRET_KEY: Any  # pydantic.SecretStr


def get_catalog(config: LakehouseConfig) -> Catalog:
    """Create the PyIceberg catalog client. Cheap to call per-request (unlike
    ai_circus_shared.document_store's engine, this isn't pooled/process-wide) —
    PyIceberg's SqlCatalog opens its own short-lived SQLAlchemy engine per call.
    """
    password = config.POSTGRES_PASSWORD.get_secret_value()
    postgres_uri = (
        f"postgresql+psycopg://{config.POSTGRES_USER}:{password}"
        f"@{config.POSTGRES_HOST}:{config.POSTGRES_PORT}/{config.POSTGRES_DB}"
    )
    return SqlCatalog(
        "default",
        **{
            "uri": postgres_uri,
            "warehouse": f"s3://{_WAREHOUSE_BUCKET}/iceberg-warehouse",
            "s3.endpoint": config.OBJECT_STORE_ENDPOINT,
            "s3.access-key-id": config.OBJECT_STORE_ACCESS_KEY,
            "s3.secret-access-key": config.OBJECT_STORE_SECRET_KEY.get_secret_value(),
            "s3.path-style-access": "true",
        },
    )


def _table_identifier(table_name: str) -> str:
    return f"{_NAMESPACE}.{table_name}"


def ingest(
    catalog: Catalog, document_store: DocumentStore, org_id: str, collection: str, table_name: str
) -> dict[str, Any]:
    """Snapshot one tenant/collection's current documents into an Iceberg
    table — creating it on first call, appending a new snapshot on every call
    after that (the table's row count and snapshot count both grow; nothing
    is overwritten). Returns a summary the caller can render directly.
    """
    documents = document_store.list(org_id, collection)
    snapshotted_at = datetime.now(UTC).isoformat()
    rows = pa.table(
        {
            "org_id": [org_id] * len(documents),
            "collection": [collection] * len(documents),
            "doc_id": [doc.doc_id for doc in documents],
            "content": [json.dumps(doc.content) for doc in documents],
            "snapshotted_at": [snapshotted_at] * len(documents),
        },
        schema=_SCHEMA,
    )

    catalog.create_namespace_if_not_exists(_NAMESPACE)
    identifier = _table_identifier(table_name)
    table = catalog.create_table_if_not_exists(identifier, schema=_SCHEMA)
    table.append(rows)

    refreshed = catalog.load_table(identifier)
    return {
        "table": identifier,
        "rows_ingested": len(documents),
        "total_rows": refreshed.scan().to_arrow().num_rows,
        "snapshot_count": len(list(refreshed.snapshots())),
    }


def list_tables(catalog: Catalog) -> list[str]:
    """Every Iceberg table under the lakehouse namespace, or [] if that
    namespace doesn't exist yet (nothing has been ingested).
    """
    try:
        return [".".join(identifier) for identifier in catalog.list_tables(_NAMESPACE)]
    except Exception:
        return []


def load_arrow(catalog: Catalog, table_name: str) -> pa.Table:
    """Full contents of one lakehouse table as an in-memory Arrow table — the
    hand-off point for query engines like DuckDB (see core/semantic.py). An
    empty, correctly-typed table if nothing has been ingested yet, not an error.
    """
    try:
        table = catalog.load_table(_table_identifier(table_name))
    except NoSuchTableError:
        return _SCHEMA.empty_table()
    return table.scan().to_arrow()


def table_info(catalog: Catalog, table_name: str) -> dict[str, Any] | None:
    """Row/snapshot counts for one table, or None if it doesn't exist yet."""
    try:
        table = catalog.load_table(_table_identifier(table_name))
    except NoSuchTableError:
        return None
    return {
        "table": _table_identifier(table_name),
        "total_rows": table.scan().to_arrow().num_rows,
        "snapshot_count": len(list(table.snapshots())),
    }
