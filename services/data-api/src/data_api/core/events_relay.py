"""
- Title:    Pipeline events relay — Kafka → per-tenant ring buffers → SSE subscribers
- Author:   Angel Martinez-Tenor

One consumer per data-api pod (its own consumer group, newest events only) reads every
tenant's `platform.pipeline.events.v1` topic. Events are kept in a small ring buffer per
(tenant, data product) — the live view's first paint — and pushed to each open SSE stream of
that tenant and data product. A tenant only ever receives events from its own topic.
Bounded everywhere: buffer length, subscriber count, each subscriber's queue (a slow browser
drops events instead of growing memory).
"""

from __future__ import annotations

import asyncio
import json
import re
import socket
import threading
from collections import deque
from dataclasses import dataclass
from typing import Any

from ai_circus_shared.data_product import PIPELINE_EVENTS_TOPIC
from confluent_kafka import Consumer

from data_api.core.logger import get_logger

logger = get_logger(__name__)

BUFFER_PER_STREAM = 300
MAX_SUBSCRIBERS = 64
SUBSCRIBER_QUEUE = 500
_TOPIC = re.compile(r"^tenant-(?P<org>[A-Za-z0-9_-]+)\." + re.escape(PIPELINE_EVENTS_TOPIC) + "$")


@dataclass(eq=False)
class Subscriber:
    """One open SSE stream: its tenant, data product and bounded queue."""

    org_id: str
    slug: str
    queue: asyncio.Queue[dict[str, Any]]
    loop: asyncio.AbstractEventLoop


class EventsRelay:
    """Fans every tenant's pipeline events out to that tenant's open streams."""

    def __init__(self, bootstrap_servers: str) -> None:
        """Empty buffers and subscribers; the consumer starts with start()."""
        self._bootstrap = bootstrap_servers
        self._buffers: dict[tuple[str, str], deque[dict[str, Any]]] = {}
        self._subscribers: list[Subscriber] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.connected = False

    def start(self) -> None:
        """Run the Kafka consumer in a daemon thread."""
        threading.Thread(target=self._run, name="events-relay", daemon=True).start()

    def stop(self) -> None:
        """Ask the consumer loop to exit."""
        self._stop.set()

    def recent(self, org_id: str, slug: str, limit: int = 100) -> list[dict[str, Any]]:
        """The last `limit` buffered events of one tenant and data product."""
        with self._lock:
            buffer = self._buffers.get((org_id, slug))
            return list(buffer)[-limit:] if buffer else []

    def subscribe(self, org_id: str, slug: str) -> Subscriber:
        """Open a stream for one tenant and data product (bounded in number)."""
        with self._lock:
            if len(self._subscribers) >= MAX_SUBSCRIBERS:
                raise RuntimeError("Too many live event streams open.")
            sub = Subscriber(org_id, slug, asyncio.Queue(maxsize=SUBSCRIBER_QUEUE), asyncio.get_running_loop())
            self._subscribers.append(sub)
            return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        """Close a stream."""
        with self._lock:
            if sub in self._subscribers:
                self._subscribers.remove(sub)

    def publish_local(self, org_id: str, event: dict[str, Any]) -> None:
        """Deliver an event that did not come through Kafka (e.g. 'served' by this API)."""
        self._deliver(org_id, event)

    def _deliver(self, org_id: str, event: dict[str, Any]) -> None:
        slug = str(event.get("slug", ""))
        with self._lock:
            buffer = self._buffers.setdefault((org_id, slug), deque(maxlen=BUFFER_PER_STREAM))
            if len(self._buffers) > 2000:  # one buffer per (tenant, data product): bounded anyway
                self._buffers.pop(next(iter(self._buffers)))
            buffer.append(event)
            targets = [s for s in self._subscribers if s.org_id == org_id and s.slug == slug]
        for sub in targets:
            sub.loop.call_soon_threadsafe(_offer, sub.queue, event)

    def _run(self) -> None:
        consumer = Consumer({
            "bootstrap.servers": self._bootstrap,
            "group.id": f"data-api-relay-{socket.gethostname()}",
            "auto.offset.reset": "latest",
            "enable.auto.commit": True,
            "topic.metadata.refresh.interval.ms": 10_000,
        })
        consumer.subscribe([f"^tenant-[A-Za-z0-9_-]+\\.{re.escape(PIPELINE_EVENTS_TOPIC)}$"])
        while not self._stop.is_set():
            try:
                for message in consumer.consume(num_messages=500, timeout=1.0):
                    if message.error() is not None:
                        continue
                    self.connected = True
                    match = _TOPIC.match(message.topic() or "")
                    if match is None:
                        continue
                    try:
                        event = json.loads(message.value() or b"{}")
                    except ValueError:
                        continue
                    if isinstance(event, dict):
                        self._deliver(match.group("org"), event)
            except Exception:
                logger.exception("Events relay poll failed")
                self._stop.wait(5)
        consumer.close()


def _offer(queue: asyncio.Queue[dict[str, Any]], event: dict[str, Any]) -> None:
    if not queue.full():
        queue.put_nowait(event)
