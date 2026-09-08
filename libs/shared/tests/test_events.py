"""Tests for ai_circus_shared.events — topic-prefixing is pure (no I/O) and
tested the same way storage.py's _key/cache.py's _key are; publish/poll
behavior is exercised against a minimal fake standing in for confluent_kafka's
Producer/Consumer (both are C-extension classes with no in-process fake
available, unlike fakeredis for cache.py — so this fakes the two methods
EventProducer/EventConsumer actually call, the same boundary-mocking style
platform-registry's test_llm_settings.py uses for httpx.Client).
"""

from __future__ import annotations

import json

import pytest

from ai_circus_shared.events import EventConsumer, EventProducer, KafkaException, _topic


def test_topic_is_tenant_prefixed() -> None:
    assert _topic("org-1", "pipeline-triggers") == "tenant-org-1.pipeline-triggers"


@pytest.mark.parametrize("org_id", ["../org", "org/1", "org id", "org;drop", ""])
def test_topic_rejects_invalid_tenant_org_id(org_id: str) -> None:
    with pytest.raises(ValueError, match="tenant_org_id"):
        _topic(org_id, "pipeline-triggers")


@pytest.mark.parametrize("org_id", ["admin", "engineering-demo", "org_1", "ORG-123"])
def test_topic_accepts_expected_org_id_shapes(org_id: str) -> None:
    assert _topic(org_id, "x") == f"tenant-{org_id}.x"


@pytest.mark.parametrize("topic", ["../etc", "a/b", "a b", "a;b"])
def test_topic_rejects_invalid_topic_names(topic: str) -> None:
    with pytest.raises(ValueError, match="topic"):
        _topic("org-1", topic)


class _FakeMessage:
    def __init__(self, value: bytes, error: object = None) -> None:
        self._value = value
        self._error = error

    def value(self) -> bytes:
        return self._value

    def error(self) -> object:
        return self._error


class _FakeProducerClient:
    """Minimal stand-in for confluent_kafka.Producer — records produce() calls."""

    def __init__(self) -> None:
        self.produced: list[tuple[str, bytes]] = []
        self.poll_calls = 0

    def produce(self, topic: str, value: bytes) -> None:
        self.produced.append((topic, value))

    def poll(self, timeout: float) -> None:
        self.poll_calls += 1


def test_publish_sends_json_encoded_event_to_the_tenant_scoped_topic() -> None:
    fake = _FakeProducerClient()
    producer = EventProducer(_client=fake)

    producer.publish("org-1", "pipeline-triggers", {"job": "etl-tabular"})

    assert len(fake.produced) == 1
    topic, value = fake.produced[0]
    assert topic == "tenant-org-1.pipeline-triggers"
    assert json.loads(value) == {"job": "etl-tabular"}


def test_publish_polls_to_serve_delivery_callbacks_without_blocking() -> None:
    fake = _FakeProducerClient()
    producer = EventProducer(_client=fake)

    producer.publish("org-1", "pipeline-triggers", {"job": "etl-tabular"})

    assert fake.poll_calls == 1


class _FakeConsumerClient:
    """Minimal stand-in for confluent_kafka.Consumer — returns queued fake messages."""

    def __init__(self, messages: list[_FakeMessage | None]) -> None:
        self._messages = list(messages)
        self.subscribed_topics: list[str] | None = None
        self.closed = False

    def subscribe(self, topics: list[str]) -> None:
        self.subscribed_topics = topics

    def poll(self, timeout: float) -> _FakeMessage | None:
        return self._messages.pop(0) if self._messages else None

    def close(self) -> None:
        self.closed = True


def test_subscribe_subscribes_to_the_tenant_scoped_topic() -> None:
    fake = _FakeConsumerClient([])
    consumer = EventConsumer(_client=fake)

    consumer.subscribe("org-1", "pipeline-triggers")

    assert fake.subscribed_topics == ["tenant-org-1.pipeline-triggers"]


def test_poll_returns_none_when_nothing_arrived() -> None:
    fake = _FakeConsumerClient([None])
    consumer = EventConsumer(_client=fake)

    assert consumer.poll(timeout_seconds=0.1) is None


def test_poll_decodes_a_json_event() -> None:
    fake = _FakeConsumerClient([_FakeMessage(json.dumps({"job": "training"}).encode("utf-8"))])
    consumer = EventConsumer(_client=fake)

    assert consumer.poll() == {"job": "training"}


def test_poll_raises_kafka_exception_on_a_message_error() -> None:
    fake = _FakeConsumerClient([_FakeMessage(b"", error="boom")])
    consumer = EventConsumer(_client=fake)

    with pytest.raises(KafkaException):
        consumer.poll()


def test_close_closes_the_underlying_client() -> None:
    fake = _FakeConsumerClient([])
    consumer = EventConsumer(_client=fake)

    consumer.close()

    assert fake.closed is True
