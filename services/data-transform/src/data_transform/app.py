"""
app.py
------

Entry point for data-transform. `python -m data_transform.app run` builds every data product's
dbt project once on each TRANSFORM_TARGETS target (the core tier's Job/CronJob) and exits
non-zero if any build failed; `create-database <name>` creates a data tool's metadata database
(the analytics/bi/governance tiers' init containers — Dagster itself loads
`data_transform.dagster_defs`). See core/runner.py and docs/adr/0006.

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_circus_shared.data_product import DATA_BUCKET
from ai_circus_shared.db import connect_engine
from ai_circus_shared.storage import ObjectStore
from confluent_kafka import Producer
from pydantic import ValidationError
from sqlalchemy import Engine

from data_transform import get_env_config
from data_transform.core import clickhouse
from data_transform.core.logger import configure_logger, get_logger
from data_transform.core.ml_export import export_features
from data_transform.core.products import DataProduct, load_products
from data_transform.core.publish import publish_events, upload_artifacts
from data_transform.core.runner import (
    RunResult,
    ensure_runs_table,
    env_for_dbt,
    install_pseudonymisation,
    tenants_with_data,
    transform,
    write_profiles,
)
from data_transform.data_model import EnvConfig

logger = get_logger(__name__)


@dataclass
class Environment:
    """Clients every build needs, created once per process (CronJob run or Dagster)."""

    products: dict[str, DataProduct]
    engine: Engine
    profiles: Path
    artifacts: Path
    store: ObjectStore
    producer: Producer
    targets: list[str]
    clickhouse: Any | None
    store_for: Callable[[str], ObjectStore] | None = None  # another bucket, same object store
    scenarios_dir: Path | None = None


def prepare_environment(config: EnvConfig) -> Environment:
    """Load the data products, install pseudonymisation, write dbt profiles, connect every client."""
    products = load_products(Path(config.SCENARIOS_DIR), config.SCENARIOS or "")
    engine = connect_engine(config)
    install_pseudonymisation(engine, config.DATA_PSEUDONYM_KEY.get_secret_value())
    clickhouse_password = config.CLICKHOUSE_PASSWORD.get_secret_value() if config.CLICKHOUSE_PASSWORD else None
    env_for_dbt(config.POSTGRES_PASSWORD.get_secret_value(), clickhouse_password)
    artifacts = Path(config.ARTIFACTS_DIR)
    profiles = write_profiles(
        artifacts / "profiles",
        host=config.POSTGRES_HOST,
        port=config.POSTGRES_PORT,
        user=config.POSTGRES_USER,
        database=config.POSTGRES_DB,
        threads=int(config.DBT_THREADS),
        clickhouse_host=config.CLICKHOUSE_HOST,
        clickhouse_user=config.CLICKHOUSE_USER,
    ).parent

    def store_for(bucket: str) -> ObjectStore:
        return ObjectStore.connect(
            bucket=bucket,
            endpoint_url=config.OBJECT_STORE_ENDPOINT,
            access_key=config.OBJECT_STORE_ACCESS_KEY,
            secret_key=config.OBJECT_STORE_SECRET_KEY.get_secret_value(),
        )

    store = store_for(DATA_BUCKET)
    producer = Producer({"bootstrap.servers": config.KAFKA_BOOTSTRAP_SERVERS, "linger.ms": 20})
    targets = (config.TRANSFORM_TARGETS or "postgres").split(",")
    client = None
    if "clickhouse" in targets:
        if config.CLICKHOUSE_HOST:
            client = clickhouse.connect(config.CLICKHOUSE_HOST, config.CLICKHOUSE_USER, clickhouse_password)
        else:
            logger.warning("TRANSFORM_TARGETS asks for clickhouse but CLICKHOUSE_HOST is not set; skipped")
            targets.remove("clickhouse")
    for product in products.values():
        ensure_runs_table(engine, product)
        if client is not None:
            clickhouse.prepare(
                client,
                product,
                pg_host=config.POSTGRES_HOST,
                pg_port=config.POSTGRES_PORT,
                pg_database=config.POSTGRES_DB,
                pg_user=config.POSTGRES_USER,
                pg_password=config.POSTGRES_PASSWORD.get_secret_value(),
            )
    return Environment(
        products, engine, profiles, artifacts, store, producer, targets, client, store_for, Path(config.SCENARIOS_DIR)
    )


def after_run(env: Environment, product: DataProduct, result: RunResult) -> None:
    """Log the outcome, publish the artifacts for the catalogue and tell the live view."""
    logger.info("{} on {}: {} — {}", product.slug, result.target, result.status, result.message)
    for failure in result.failures[:10]:
        logger.warning("  {} {}: {}", failure["status"], failure["node"], failure["message"])
    try:
        upload_artifacts(env.store, env.artifacts, result)
        publish_events(env.producer, tenants_with_data(env.engine, product), result)
    except Exception:
        logger.exception("Could not publish the artifacts/events of {}", product.slug)
    # Feature marts → the raw datasets of the tabular_ml scenarios they train (Postgres is the
    # warehouse of record; a ClickHouse build of the same marts adds nothing).
    if result.status == "success" and result.target == "postgres" and env.store_for and env.scenarios_dir:
        try:
            export_features(
                env.engine, env.store_for, product, env.scenarios_dir, tenants_with_data(env.engine, product)
            )
        except Exception:
            logger.exception("Could not export the ML feature marts of {}", product.slug)


def run_once(config: EnvConfig) -> int:
    """Build every data product on every configured target; returns the number of failures."""
    env = prepare_environment(config)
    failures = 0
    for product in env.products.values():
        for target in env.targets:
            result = transform(env.engine, product, target, env.profiles, env.artifacts)
            if result.status == "skipped":
                logger.info("{} on {}: skipped — {}", product.slug, target, result.message)
                continue
            failures += result.status != "success"
            after_run(env, product, result)
    return failures


TOOL_DATABASES = ("dagster", "superset", "openmetadata")


def ensure_database(config: EnvConfig, name: str) -> None:
    """A data tool's own metadata database (Dagster's runs, Superset's, OpenMetadata's) — created once."""
    if name not in TOOL_DATABASES:
        raise ValueError(f"not a data tool database: {name!r}")
    from ai_circus_shared.db import database_url
    from sqlalchemy import create_engine, text

    engine = create_engine(database_url(config).set(database="postgres"), isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        if conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}).first() is None:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    engine.dispose()


def main() -> None:
    """Validate configuration, then run the requested mode (default: run)."""
    configure_logger()
    try:
        config = get_env_config()
    except ValidationError as e:
        logger.error("Configuration error: Mandatory environment variable(s) missing or invalid:")
        for error in e.errors():
            logger.error("  {}: {}", " -> ".join(str(loc) for loc in error["loc"]), error["msg"])
        sys.exit(1)
    mode = sys.argv[1] if len(sys.argv) > 1 else "run"
    if mode == "create-database" and len(sys.argv) > 2:
        ensure_database(config, sys.argv[2])
        return
    if mode != "run":
        logger.error("Unknown mode {!r} (expected: run, create-database <dagster|superset|openmetadata>)", mode)
        sys.exit(2)
    sys.exit(1 if run_once(config) else 0)


if __name__ == "__main__":
    main()
