"""
- Title:    After a run: dbt artifacts to the object store, stage events to every tenant
- Author:   Angel Martinez-Tenor
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from ai_circus_shared.data_product import PIPELINE_EVENTS_TOPIC
from ai_circus_shared.events import tenant_topic
from ai_circus_shared.storage import ObjectStore
from confluent_kafka import Producer

from data_transform.core.logger import get_logger
from data_transform.core.runner import PLATFORM_ORG, RunResult

logger = get_logger(__name__)
ARTIFACTS = ("manifest.json", "catalog.json", "run_results.json")


def upload_artifacts(store: ObjectStore, artifacts_root: Path, result: RunResult) -> list[str]:
    """manifest/catalog/run_results under `tenant-platform/dbt/<slug>/<target>/` — what the
    metadata catalogue (OpenMetadata's dbt ingestion) and the lineage view read.
    """
    target_dir = artifacts_root / result.slug / result.target / "target"
    keys = []
    for name in ARTIFACTS:
        path = target_dir / name
        if path.exists():
            keys.append(store.put(PLATFORM_ORG, f"dbt/{result.slug}/{result.target}/{name}", path.read_bytes()))
    return keys


def publish_events(producer: Producer, tenants: list[str], result: RunResult) -> None:
    """Tell every tenant with data that a build finished: a 'transformed' and a 'gold' stage event."""
    detail = {
        "run_id": result.run_id,
        "target": result.target,
        "models": result.models_ok,
        "tests_passed": result.tests_passed,
        "tests_failed": result.tests_failed,
        "watermark": result.watermark.isoformat() if result.watermark else None,
        "message": result.message,
    }
    now = datetime.now(UTC).isoformat()
    ok = result.status == "success"
    for org in tenants:
        for stage, status in (("transformed", "ok" if ok else "failed"), ("gold", "curated" if ok else "stale")):
            event = {
                "slug": result.slug,
                "stage": stage,
                "status": status,
                "entity": "batch",
                "record_id": "",
                "source": "dbt",
                "ts": now,
                "latency_ms": None,
                "filter": None,
                "detail": detail,
            }
            producer.produce(tenant_topic(org, PIPELINE_EVENTS_TOPIC), json.dumps(event).encode())
    producer.flush(10)
