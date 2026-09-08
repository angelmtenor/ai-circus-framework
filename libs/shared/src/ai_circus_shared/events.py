"""
- Title:    Tenant-scoped event streaming (Kafka-protocol)
- Author:   ai-circus-framework contributors

The unified data layer's real-time companion to ai_circus_shared.document_store:
an append-only, fan-out event stream for state changes other parts of the
platform — or a future ingestion/analytics pipeline — want to react to as they
happen, not just query on demand. Part of the Data Platform optional profile
(see docker-compose.yml's "data-platform" profile / k8s/data-platform/): the
broker only runs when that profile is enabled, so every call here is
best-effort by design — a service publishing an event must never let a broker
outage break the request that triggered it (see EventProducer.publish's
docstring for the exact contract).

Every topic is tenant-namespaced the same way ai_circus_shared.storage/cache
prefix their keys/paths, so one tenant's events can never be consumed by a
caller scoped to another's.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

from confluent_kafka import Consumer, KafkaException, Producer

# Mirrors ai_circus_shared.storage/cache's own _SAFE_ORG_ID guard.
_SAFE_ORG_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_SAFE_TOPIC = re.compile(r"^[A-Za-z0-9._-]+$")


class EventStreamConfig(Protocol):
    """Shape a service's own EnvConfig must satisfy to call connect_producer()/
    connect_consumer() — one bootstrap-servers URL, same one-field-config
    convention as ai_circus_shared.cache.CacheConfig.
    """

    KAFKA_BOOTSTRAP_SERVERS: str


def _topic(tenant_org_id: str, topic: str) -> str:
    if not _SAFE_ORG_ID.match(tenant_org_id):
        raise ValueError(f"Invalid tenant_org_id {tenant_org_id!r}: must match {_SAFE_ORG_ID.pattern}.")
    if not _SAFE_TOPIC.match(topic):
        raise ValueError(f"Invalid topic {topic!r}: must match {_SAFE_TOPIC.pattern}.")
    return f"tenant-{tenant_org_id}.{topic}"


def connect_producer(config: EventStreamConfig) -> Producer:
    """Create the process-wide Kafka producer. Call once, at startup."""
    return Producer({"bootstrap.servers": config.KAFKA_BOOTSTRAP_SERVERS})


def connect_consumer(config: EventStreamConfig, group_id: str) -> Consumer:
    """Create a Kafka consumer bound to one consumer group. Call once per
    distinct group a service needs to read as — a second call with the same
    group_id joins the same group (shares partitions) rather than re-reading
    everything, standard Kafka consumer-group semantics.
    """
    return Consumer(
        {
            "bootstrap.servers": config.KAFKA_BOOTSTRAP_SERVERS,
            "group.id": group_id,
            "auto.offset.reset": "earliest",
        }
    )


@dataclass(frozen=True)
class EventProducer:
    """A producer client bound to one process-wide connection, tenant-scoping
    every topic.
    """

    _client: Producer

    def publish(self, tenant_org_id: str, topic: str, event: dict[str, Any]) -> None:
        """Enqueue one JSON event for async delivery. This does NOT block on
        broker acknowledgement or raise if the broker is unreachable — librdkafka
        buffers and retries internally, only surfacing failures via the delivery
        callback this method doesn't register. Callers that publish from a
        request path (e.g. data-platform-manager's pipeline-trigger endpoint)
        should treat this as fire-and-forget: the durable record of what
        happened belongs in a document/relational store, this is a secondary,
        best-effort fan-out for whoever wants to react in real time.
        """
        self._client.produce(_topic(tenant_org_id, topic), value=json.dumps(event).encode("utf-8"))
        self._client.poll(0)  # serve any pending delivery-report callbacks without blocking


@dataclass(frozen=True)
class EventConsumer:
    """A consumer client bound to one process-wide connection, tenant-scoping
    every topic.
    """

    _client: Consumer

    def subscribe(self, tenant_org_id: str, topic: str) -> None:
        """Subscribe to one tenant-scoped topic. Call once per (tenant, topic)
        this consumer should read — a second call replaces the prior
        subscription (confluent-kafka's own semantics), not adds to it.
        """
        self._client.subscribe([_topic(tenant_org_id, topic)])

    def poll(self, timeout_seconds: float = 1.0) -> dict[str, Any] | None:
        """One decoded event, or None if nothing arrived within timeout_seconds."""
        msg = self._client.poll(timeout_seconds)
        if msg is None:
            return None
        if msg.error():
            raise KafkaException(msg.error())
        return json.loads(msg.value().decode("utf-8"))

    def close(self) -> None:
        """Leave the consumer group and release the underlying connection —
        call when done with a short-lived consumer (e.g. a per-request one),
        never for a process-wide one you intend to keep polling.
        """
        self._client.close()
