"""
- Title:    Landing bucket → bronze, reconciled against each delivery's control file
- Author:   Angel Martinez-Tenor

A file source's sender drops one *delivery* per folder under
`tenant-<org>/landing/<slug>/<prefix><delivery>/`: a JSON-lines data file plus a control file
saying how many records it meant to send and what they add up to. The loader lands every line
through the same contract → bronze path as the stream (`source_kind = "file"`, into the bronze
table of the kafka source it `lands_in`), records the comparison in `ops_file_deliveries`
(`reconciled` / `gap` / `mismatch`), then moves the delivery to `processed/` so a re-drop is
picked up again and an old one never is.
"""

from __future__ import annotations

import json
import threading
from datetime import date
from typing import Any

from ai_circus_shared.contracts import get_path
from ai_circus_shared.data_product import FileSource
from ai_circus_shared.storage import ObjectStore
from sqlalchemy import Engine, text

from data_ingest.core.logger import get_logger
from data_ingest.core.pipeline_events import PipelineEvents
from data_ingest.core.products import DataProduct
from data_ingest.core.stream import StreamWorker

logger = get_logger(__name__)
INGEST_BATCH = 2000  # records per ingest call when landing a delivery


def landing_prefix(product: DataProduct, source: FileSource) -> str:
    """Object-store prefix (under the tenant) where a file source's deliveries are dropped."""
    return f"landing/{product.slug}/{source.prefix}"


def partition_of(delivery: str) -> dict[str, str]:
    """`date=2026-10-27/store=S019` -> {"date": "2026-10-27", "store": "S019"}."""
    return dict(part.split("=", 1) for part in delivery.split("/") if "=" in part)


def reconcile(
    expected_count: int | None, expected_sum: int | None, loaded_count: int, loaded_sum: int, quarantined: int
) -> str:
    """Compare a delivery with its control file: reconciled, gap (fewer records), mismatch or no_control."""
    if expected_count is None or expected_sum is None:
        return "no_control"
    if loaded_count < expected_count:
        return "gap"
    if loaded_count != expected_count or loaded_sum != expected_sum or quarantined:
        return "mismatch"
    return "reconciled"


class FileLoader:
    """Scans the landing bucket and loads + reconciles each complete delivery."""

    def __init__(
        self,
        store: ObjectStore,
        engine: Engine,
        products: dict[str, DataProduct],
        stream: StreamWorker,
        events: PipelineEvents,
        interval_seconds: float,
    ) -> None:
        """Bind the loader to its store, warehouse and the stream worker it lands records through."""
        self._store, self._engine, self._products = store, engine, products
        self._stream, self._events, self._interval = stream, events, interval_seconds
        self._stop = threading.Event()
        self._wake = threading.Event()

    def stop(self) -> None:
        """Ask the loop to exit (and wake it if it is waiting)."""
        self._stop.set()
        self._wake.set()

    def trigger(self) -> None:
        """Wake the loop for an immediate scan."""
        self._wake.set()

    def run(self) -> None:
        """Scan every interval (or when triggered) until stopped."""
        while not self._stop.is_set():
            try:
                self.scan()
            except Exception:
                logger.exception("Landing scan failed")
            self._wake.wait(self._interval)
            self._wake.clear()

    def scan(self) -> int:
        """Load every delivery whose control file has arrived, for every tenant; returns how many."""
        loaded = 0
        sources = [(p, s) for p in self._products.values() for s in p.config.file_sources()]
        if not sources:
            return 0
        for org_id in self._store.tenants():
            for product, source in sources:
                prefix = landing_prefix(product, source)
                keys = self._store.list(org_id, prefix)
                controls = sorted(k for k in keys if k.endswith(f"/{source.control.file_name}"))
                for control_key in controls:
                    delivery = control_key.removeprefix(prefix).rsplit("/", 1)[0]
                    if self._stop.is_set():
                        return loaded
                    self.load(org_id, product, source, delivery)
                    loaded += 1
        return loaded

    def load(self, org_id: str, product: DataProduct, source: FileSource, delivery: str) -> str:
        """Land one delivery, record its reconciliation and move it to processed/; returns its status."""
        folder = f"{landing_prefix(product, source)}{delivery}"
        control: dict[str, Any] = json.loads(self._store.get(org_id, f"{folder}/{source.control.file_name}"))
        data_key = f"{folder}/{source.data_file}"
        lines = (
            self._store.get(org_id, data_key).decode("utf-8", "replace").splitlines()
            if self._store.exists(org_id, data_key)
            else []
        )
        target = product.config.source(source.lands_in)
        # Landed in batches: a delivery may be a month of history, and one ingest call holds its
        # whole batch (validation, bronze upsert, lake rows) in memory.
        items: list[tuple[str, Any, str | None]] = []
        loaded_sum, loaded_count, new, corrected, quarantined = 0, 0, 0, 0, 0
        for line in (line for line in lines if line.strip()):
            try:
                payload = json.loads(line)
                items.append((org_id, payload, None))
                loaded_sum += int(sum(v for v in get_path(payload, source.control.sum_path) if isinstance(v, int)))
            except ValueError:
                items.append((org_id, None, line))
            if len(items) >= INGEST_BATCH:
                n, c, q = self._stream.ingest(product, target, items, source_kind="file", delivery=delivery)
                new, corrected, quarantined, loaded_count = (
                    new + n,
                    corrected + c,
                    quarantined + q,
                    loaded_count + len(items),
                )
                items = []
        if items:
            n, c, q = self._stream.ingest(product, target, items, source_kind="file", delivery=delivery)
            new, corrected, quarantined, loaded_count = (
                new + n,
                corrected + c,
                quarantined + q,
                loaded_count + len(items),
            )
        del lines
        expected_count = control.get(source.control.count_field)
        expected_sum = control.get(source.control.sum_field)
        status = reconcile(expected_count, expected_sum, loaded_count, loaded_sum, quarantined)
        partition = partition_of(delivery)
        try:
            business_date = date.fromisoformat(partition.get("date", ""))
        except ValueError:
            business_date = None
        with self._engine.begin() as conn:
            conn.execute(
                text(f"""INSERT INTO "{product.schema}".ops_file_deliveries (org_id, source_id, delivery,
                        business_date, partition, expected_count, loaded_count, quarantined_count, expected_sum,
                        loaded_sum, status, loaded_at)
                    VALUES (:org_id, :source_id, :delivery, :business_date, CAST(:partition AS jsonb), :ec, :lc,
                        :qc, :es, :ls, :status, now())
                    ON CONFLICT (org_id, source_id, delivery) DO UPDATE SET expected_count = EXCLUDED.expected_count,
                        loaded_count = EXCLUDED.loaded_count, quarantined_count = EXCLUDED.quarantined_count,
                        expected_sum = EXCLUDED.expected_sum, loaded_sum = EXCLUDED.loaded_sum,
                        status = EXCLUDED.status, loaded_at = now()"""),
                {
                    "org_id": org_id,
                    "source_id": source.id,
                    "delivery": delivery,
                    "business_date": business_date,
                    "partition": json.dumps(partition),
                    "ec": expected_count,
                    "lc": loaded_count,
                    "qc": quarantined,
                    "es": expected_sum,
                    "ls": loaded_sum,
                    "status": status,
                },
            )
        processed = f"landing/{product.slug}/processed/{source.prefix}{delivery}"
        for name in (source.data_file, source.control.file_name):
            key = f"{folder}/{name}"
            if self._store.exists(org_id, key):
                self._store.put(org_id, f"{processed}/{name}", self._store.get(org_id, key))
                self._store.delete(org_id, key)
        self._events.record(
            org_id,
            product.slug,
            "validated",
            status,
            entity="file",
            record_id=delivery,
            source=source.id,
            detail={
                "expected": expected_count,
                "loaded": loaded_count,
                "new": new,
                "corrected": corrected,
                "quarantined": quarantined,
            },
        )
        self._events.flush()
        if status != "reconciled":
            logger.warning("{} delivery {} for {}: {}", product.slug, delivery, org_id, status)
        return status
