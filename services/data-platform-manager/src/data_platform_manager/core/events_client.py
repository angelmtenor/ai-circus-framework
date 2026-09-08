"""
- Title:    Process-wide Kafka producer
- Author:   ai-circus-framework contributors

ai_circus_shared.events.connect_producer() is a plain factory (no module-global
state of its own) — each consuming service holds onto the one producer it
creates at startup, same "init once at startup, not at import" shape as
core/cache_client.py. Consumers are NOT held process-wide here: GET
/events/pipeline-triggers (api.py) creates a fresh one per request with a
unique consumer-group id, so every call re-reads the topic from the beginning
rather than competing with itself across requests for a shared group's offsets
— see that endpoint's docstring.
"""

from __future__ import annotations

from ai_circus_shared.events import EventStreamConfig, connect_producer
from confluent_kafka import Producer

_producer: Producer | None = None


def init_producer(config: EventStreamConfig) -> Producer:
    """Create the process-wide Kafka producer. Call once, at startup."""
    global _producer
    _producer = connect_producer(config)
    return _producer


def get_producer() -> Producer:
    """FastAPI dependency: the initialized process-wide producer."""
    if _producer is None:
        raise RuntimeError("Kafka producer not initialized — call init_producer() at startup first.")
    return _producer
