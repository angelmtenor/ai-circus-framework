"""
- Title:    Kafka → bronze stream worker
- Author:   Angel Martinez-Tenor

One consumer (group `data-ingest`) subscribed by regex to every tenant's topic of every kafka
source (`^tenant-<org>.<topic>$`, see ai_circus_shared.events): the tenant comes from the topic
a record was published to, never from its payload. Offsets are committed only after the batch
is durably in Postgres — at-least-once delivery, made exactly-once in bronze by the key/hash
upsert.
"""

from __future__ import annotations

import json
import re
import threading
from collections import defaultdict
from typing import Any

from ai_circus_shared.data_product import KafkaSource
from confluent_kafka import Consumer, KafkaError, Message
from sqlalchemy import Engine

from data_ingest.core.bronze import (
    Accepted,
    ReferenceCache,
    Rejected,
    apply_hot,
    classify,
    hot_increments,
    insert_quarantine,
    upsert_bronze,
)
from data_ingest.core.logger import get_logger
from data_ingest.core.pipeline_events import PipelineEvents
from data_ingest.core.products import DataProduct

logger = get_logger(__name__)

_TOPIC = re.compile(r"^tenant-(?P<org>[A-Za-z0-9_-]+)\.(?P<topic>.+)$")
BATCH_SIZE = 1000


def topic_patterns(products: dict[str, DataProduct]) -> list[str]:
    """One anchored regex per kafka source matching every tenant's topic of it."""
    return [
        f"^tenant-[A-Za-z0-9_-]+\\.{re.escape(source.topic)}$"
        for product in products.values()
        for source in product.config.kafka_sources()
    ]


class StreamWorker:
    """Consumes every kafka source's tenant topics into bronze, quarantine and the hot path."""

    def __init__(
        self, consumer: Consumer, engine: Engine, products: dict[str, DataProduct], events: PipelineEvents
    ) -> None:
        """Route topics to (data product, source); one reference cache per data product."""
        self._consumer, self._engine, self._products, self._events = consumer, engine, products, events
        self._routes: dict[str, tuple[DataProduct, KafkaSource]] = {
            source.topic: (product, source)
            for product in products.values()
            for source in product.config.kafka_sources()
        }
        self._references = {slug: ReferenceCache(engine, p) for slug, p in products.items()}
        self.stats: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self._stop = threading.Event()

    def stop(self) -> None:
        """Ask the consume loop to exit after its current batch."""
        self._stop.set()

    def run(self) -> None:
        """Consume batches until stopped; commit only after a batch is durably landed."""
        patterns = topic_patterns(self._products)
        if not patterns:
            return
        self._consumer.subscribe(patterns)
        logger.info("Stream worker subscribed to {} topic pattern(s)", len(patterns))
        while not self._stop.is_set():
            try:
                messages = self._consumer.consume(num_messages=BATCH_SIZE, timeout=1.0)
                if messages:
                    self._process(messages)
                    self._consumer.commit(asynchronous=False)
                self._events.flush()
            except Exception:
                logger.exception("Stream batch failed; will retry from the last committed offset")
                self._rewind()
                self._stop.wait(5)
        self._consumer.close()

    def _rewind(self) -> None:
        """Seek every assigned partition back to its committed offset, so a failed batch is
        re-read rather than skipped.
        """
        try:
            assignment = self._consumer.assignment()
            for tp in self._consumer.committed(assignment, timeout=10):
                if tp.offset >= 0:
                    self._consumer.seek(tp)
        except Exception:
            logger.exception("Could not rewind the consumer")

    def _process(self, messages: list[Message]) -> None:
        groups: dict[tuple[str, str], list[tuple[str, Any, str | None]]] = defaultdict(list)
        for message in messages:
            error = message.error()
            if error is not None:
                if error.code() != KafkaError._PARTITION_EOF:
                    logger.warning("Kafka error: {}", error)
                continue
            match = _TOPIC.match(message.topic() or "")
            route = self._routes.get(match.group("topic")) if match else None
            if match is None or route is None:
                continue
            raw = message.value() or b""
            try:
                payload: Any = json.loads(raw)
                raw_text = None
            except ValueError, UnicodeDecodeError:
                payload, raw_text = None, raw.decode("utf-8", "replace")
            groups[route[0].slug, route[1].id].append((match.group("org"), payload, raw_text))
        for (slug, source_id), items in groups.items():
            product = self._products[slug]
            self.ingest(product, product.config.source(source_id), items, source_kind="stream")

    def ingest(
        self,
        product: DataProduct,
        source: KafkaSource,
        items: list[tuple[str, Any, str | None]],
        *,
        source_kind: str,
        delivery: str | None = None,
    ) -> tuple[int, int, int]:
        """Validate and land one source's records; returns (new, corrected, quarantined)."""
        references = self._references[product.slug]
        accepted: list[Accepted] = []
        rejected: list[Rejected] = []
        for org_id, payload, raw in items:
            outcome = classify(product, source, org_id, payload, raw=raw, lookup=references.for_org(org_id))
            if isinstance(outcome, Accepted):
                accepted.append(outcome)
            else:
                rejected.append(outcome)
        with self._engine.begin() as conn:
            result = upsert_bronze(conn, product, source, accepted, source_kind=source_kind, delivery=delivery)
            insert_quarantine(conn, product, source.id, rejected, source_kind=source_kind, delivery=delivery)
            apply_hot(conn, product, hot_increments(product, source, result.inserted))
        stats = self.stats[f"{product.slug}.{source.id}"]
        stats["received"] += len(items)
        stats["new"] += len(result.inserted)
        stats["corrected"] += result.corrected
        stats["duplicates"] += result.duplicates
        stats["quarantined"] += len(rejected)
        self._report(product, source, accepted, result.inserted, rejected)
        return len(result.inserted), result.corrected, len(rejected)

    def _report(
        self,
        product: DataProduct,
        source: KafkaSource,
        accepted: list[Accepted],
        inserted: list[Accepted],
        rejected: list[Rejected],
    ) -> None:
        entity = next((r.entity for r in product.config.records if r.source == source.id), source.id)
        new_keys = {(r.org_id, r.key, r.version) for r in inserted}
        has_hot = any(source.id in agg.sources for agg in product.config.hot_path)
        for record in accepted:
            status = "ok" if (record.org_id, record.key, record.version) in new_keys else "duplicate"
            detail = {"version": record.version} if source.version_field else {}
            for stage in ("event_log", "validated", "bronze"):
                self._events.record(
                    record.org_id,
                    product.slug,
                    stage,
                    status,
                    entity=entity,
                    record_id=record.key,
                    source=source.id,
                    event_ts=record.event_ts,
                    detail=detail,
                    filter_value=record.filter_value,
                )
            if has_hot and status == "ok" and (not source.version_field or record.version == 1):
                self._events.record(
                    record.org_id,
                    product.slug,
                    "hot",
                    "approximate",
                    entity=entity,
                    record_id=record.key,
                    source=source.id,
                    event_ts=record.event_ts,
                    filter_value=record.filter_value,
                )
        for record in rejected:
            detail = {"violations": record.violations[:5]}
            self._events.record(
                record.org_id,
                product.slug,
                "event_log",
                "ok",
                entity=entity,
                record_id=record.key or "?",
                source=source.id,
                filter_value=record.filter_value,
            )
            self._events.record(
                record.org_id,
                product.slug,
                "validated",
                "quarantined",
                entity=entity,
                record_id=record.key or "?",
                source=source.id,
                detail=detail,
                filter_value=record.filter_value,
            )
