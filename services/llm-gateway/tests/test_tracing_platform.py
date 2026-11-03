"""The generic `otel` callback's spans (the platform's traces, ADR 0015): no content, no request metadata."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Iterator

import litellm
import pytest
from litellm.integrations.opentelemetry import OpenTelemetry, OpenTelemetryConfig
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from llm_gateway import tracing
from tests.conftest import LITELLM_LOOP

METADATA = {
    "trace_user_id": "org-1",
    "aicf_tenant_id": "org-1",
    "aicf_ai_system_id": "churn-v2",
    "aicf_scenario_slug": "churn",
    "requester_custom_headers": {"x-forwarded-for": "203.0.113.7"},
    "user_api_key_user_email": "someone@example.com",
}
_LOOP = LITELLM_LOOP  # shared with test_tracing — see conftest


@pytest.fixture(scope="module")
def exporter() -> Iterator[InMemorySpanExporter]:
    """One real generic `otel` logger exporting to memory, configured as app.py configures the proxy."""
    assert tracing.install_platform_span_policy()
    memory = InMemorySpanExporter()
    # An explicit provider (LiteLLM's test injection point): with an SDK provider already global from an
    # earlier module, the logger would reuse that one and never attach this exporter.
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    logger = OpenTelemetry(config=OpenTelemetryConfig(exporter=memory), callback_name="otel", tracer_provider=provider)
    logger.message_logging = False  # callback_settings.otel.message_logging: false
    litellm.logging_callback_manager._reset_all_callbacks()  # global and class-deduped — see test_tracing
    litellm.callbacks = [logger]
    yield memory
    litellm.logging_callback_manager._reset_all_callbacks()


def _attributes(exporter: InMemorySpanExporter) -> dict:
    exporter.clear()

    async def call() -> None:
        await litellm.acompletion(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "SECRET PROMPT"}],
            mock_response="OK-REPLY",
            user="org-1",
            metadata=copy.deepcopy(METADATA),
        )
        await asyncio.sleep(1.5)

    _LOOP.run_until_complete(call())
    attributes: dict = {}
    for span in exporter.get_finished_spans():
        attributes.update(span.attributes or {})
    return attributes


def test_platform_spans_carry_no_request_metadata_and_no_content(exporter: InMemorySpanExporter) -> None:
    attributes = _attributes(exporter)
    assert attributes, "the call produced spans"
    assert [key for key in attributes if key.startswith(tracing.PLATFORM_DROPPED_ATTRIBUTE_PREFIXES)] == []
    flat = repr(attributes)
    for secret in ("SECRET PROMPT", "OK-REPLY", "203.0.113.7", "someone@example.com"):
        assert secret not in flat, secret


def test_platform_spans_get_the_correlation_attributes(exporter: InMemorySpanExporter) -> None:
    attributes = _attributes(exporter)
    assert attributes["aicf.tenant.id"] == "org-1"
    assert attributes["aicf.ai_system.id"] == "churn-v2"
    assert attributes["gen_ai.request.model"] == "gpt-4o-mini"
    assert isinstance(attributes["gen_ai.usage.input_tokens"], int)


def test_the_platform_policy_is_idempotent() -> None:
    wrapped = OpenTelemetry.safe_set_attribute
    assert tracing.install_platform_span_policy()
    assert OpenTelemetry.safe_set_attribute is wrapped
