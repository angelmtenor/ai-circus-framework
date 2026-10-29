"""
- Title:    Pipeline stage events (`platform.pipeline.events.v1`)
- Author:   Angel Martinez-Tenor

What makes the live data-flow view possible: each stage a record passes reports
`{slug, stage, status, entity, record_id, source, ts, latency_ms, detail}` on the tenant's
pipeline-events topic (plus the record's row-filter value, so data-api can show a filtered
role only its own rows' events), which data-api relays to the browser over SSE. Per-record events are
rate-limited (a 60-day backfill must not become 300k UI events): above the budget, a stage's
records are summarised into one `batch` event per flush.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from ai_circus_shared.contracts import parse_timestamp
from ai_circus_shared.data_product import PIPELINE_EVENTS_TOPIC
from ai_circus_shared.events import tenant_topic
from confluent_kafka import Producer

PER_RECORD_BUDGET_PER_SECOND = 15


class PipelineEvents:
    """Rate-limited publisher of per-record and batch stage events."""

    def __init__(self, producer: Producer) -> None:
        """Wrap the process-wide producer."""
        self._producer = producer
        self._window = 0
        self._sent = 0
        self._batched: dict[tuple[str, str, str, str], int] = defaultdict(int)

    def _publish(self, org_id: str, event: dict[str, Any]) -> None:
        try:
            self._producer.produce(tenant_topic(org_id, PIPELINE_EVENTS_TOPIC), json.dumps(event).encode())
        except BufferError:
            self._producer.poll(0.5)  # queue full: let it drain once, then drop this event (best-effort)

    def record(
        self,
        org_id: str,
        slug: str,
        stage: str,
        status: str,
        *,
        entity: str,
        record_id: str,
        source: str,
        event_ts: str | None = None,
        detail: dict[str, Any] | None = None,
        filter_value: str | None = None,
    ) -> None:
        """One record's stage event — summarised into a batch event when over the per-second budget."""
        now = time.time()
        if int(now) != self._window:
            self._window, self._sent = int(now), 0
        if self._sent >= PER_RECORD_BUDGET_PER_SECOND:
            self._batched[org_id, slug, stage, status] += 1
            return
        self._sent += 1
        stamp = parse_timestamp(event_ts)
        latency = round((now - stamp.timestamp()) * 1000) if stamp else None
        self._publish(
            org_id,
            {
                "slug": slug,
                "stage": stage,
                "status": status,
                "entity": entity,
                "record_id": record_id,
                "source": source,
                "ts": datetime.now(UTC).isoformat(),
                "latency_ms": latency,
                "filter": filter_value,
                "detail": detail or {},
            },
        )

    def batch(
        self, org_id: str, slug: str, stage: str, status: str, count: int, detail: dict[str, Any] | None = None
    ) -> None:
        """One event for many records of a stage (backfills, CDC bursts, dbt runs)."""
        if count:
            self._publish(
                org_id,
                {
                    "slug": slug,
                    "stage": stage,
                    "status": status,
                    "entity": "batch",
                    "record_id": "",
                    "source": "",
                    "ts": datetime.now(UTC).isoformat(),
                    "latency_ms": None,
                    "filter": None,
                    "detail": {"count": count, **(detail or {})},
                },
            )

    def flush(self) -> None:
        """Emit the summarised batch events and serve the producer's callbacks."""
        for (org_id, slug, stage, status), count in self._batched.items():
            self.batch(org_id, slug, stage, status, count, {"summarised": True})
        self._batched.clear()
        self._producer.poll(0)
