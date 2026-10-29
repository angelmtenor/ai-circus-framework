"""
- Title:    ClickHouse target preparation (analytics tier)
- Author:   Angel Martinez-Tenor

Before dbt builds a data product on ClickHouse (docs/adr/0005), ClickHouse must be able to read
Postgres' silver: one database per data product with the PostgreSQL engine,
`<slug>_pg_silver`, pointing at schema `<slug>_silver` of the warehouse. The ClickHouse target's
silver models are columnar copies of those tables (pseudonymisation already applied in
Postgres — the key never reaches ClickHouse) and its gold marts are rebuilt from them.
"""

from __future__ import annotations

from typing import Any

from data_transform.core.products import DataProduct


def _quote(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def prepare(
    client: Any, product: DataProduct, *, pg_host: str, pg_port: str, pg_database: str, pg_user: str, pg_password: str
) -> None:
    """Create (once) the PostgreSQL-engine databases dbt-clickhouse reads Postgres through:
    `<slug>_pg_silver` (silver, copied columnar) and `<slug>_pg_gold` (gold models too heavy
    to recompute within ClickHouse's per-query memory, copied as built); dbt creates the rest.
    """
    for layer in ("silver", "gold"):
        database = f"{product.slug}_pg_{layer}"
        exists = client.command(f"EXISTS DATABASE `{database}`")
        if str(exists).strip() == "1":
            continue
        client.command(
            f"CREATE DATABASE IF NOT EXISTS `{database}` ENGINE = PostgreSQL("
            f"{_quote(f'{pg_host}:{pg_port}')}, {_quote(pg_database)}, {_quote(pg_user)}, {_quote(pg_password)}, "
            f"{_quote(f'{product.slug}_{layer}')})"
        )


def connect(host: str, user: str | None, password: str | None) -> Any:
    """An HTTP client to ClickHouse (dbt-clickhouse's own client library)."""
    import clickhouse_connect  # dbt-clickhouse's own client library

    return clickhouse_connect.get_client(host=host, port=8123, username=user or "default", password=password or "")
