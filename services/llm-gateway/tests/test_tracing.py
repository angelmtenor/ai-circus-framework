"""Tests for the gen_ai.* / aicf.* span attributes and the TRACE_CAPTURE_CONTENT switch."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import litellm
import pytest
import yaml
from litellm.integrations.langfuse.langfuse_otel import LangfuseOtelLogger
from litellm.integrations.opentelemetry import OpenTelemetryConfig
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from llm_gateway import app, tracing

METADATA = {
    "trace_user_id": "org-1",
    "trace_name": "assistant/churn",
    "trace_metadata": {"scenario_slug": "churn"},
    "aicf_tenant_id": "org-1",
    "aicf_ai_system_id": "churn-v2",
    "aicf_scenario_slug": "churn",
    "aicf_policy_version": "v9",
    "aicf_policy_decision": "degrade",
}


@pytest.fixture(scope="module")
def _memory_exporter() -> Iterator[InMemorySpanExporter]:
    """One real langfuse_otel logger exporting to memory for the whole module: LiteLLM registers a
    callback once per class, so a logger built per test would never receive the later calls.
    """
    assert tracing.install()
    memory = InMemorySpanExporter()
    logger = LangfuseOtelLogger(config=OpenTelemetryConfig(exporter=memory))
    logger.callback_name = "langfuse_otel"
    previous = litellm.callbacks
    litellm.callbacks = [logger]
    yield memory
    litellm.callbacks = previous
    litellm.turn_off_message_logging = False


_LOOP = asyncio.new_event_loop()  # LiteLLM's logging worker binds to the first loop it sees — keep one


@pytest.fixture
def exporter(_memory_exporter: InMemorySpanExporter) -> Iterator[InMemorySpanExporter]:
    """The shared in-memory exporter, emptied, with message logging back on."""
    _memory_exporter.clear()
    litellm.turn_off_message_logging = False
    yield _memory_exporter
    litellm.turn_off_message_logging = False


def _run_call(metadata: dict | None = METADATA) -> dict:
    async def call() -> None:
        await litellm.acompletion(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": "SECRET PROMPT"}],
            mock_response="OK-REPLY",
            user="org-1",
            metadata=copy.deepcopy(metadata),  # litellm mutates it (call ids) — never share one
        )
        await asyncio.sleep(1.5)

    _LOOP.run_until_complete(call())
    return {}


def _span(exporter: InMemorySpanExporter) -> dict:
    (span,) = [s for s in exporter.get_finished_spans() if s.name == "litellm_request"]
    return dict(span.attributes)


def test_a_real_call_gets_standard_genai_attributes(exporter: InMemorySpanExporter) -> None:
    _run_call()
    attrs = _span(exporter)
    assert attrs["gen_ai.operation.name"] == "chat"
    assert attrs["gen_ai.provider.name"] == "openai" and attrs["gen_ai.system"] == "openai"
    assert attrs["gen_ai.request.model"] == "gpt-4o-mini" and attrs["gen_ai.response.model"] == "gpt-4o-mini"
    assert attrs["gen_ai.response.id"].startswith("chatcmpl-")
    assert tuple(attrs["gen_ai.response.finish_reasons"]) == ("stop",)
    assert isinstance(attrs["gen_ai.usage.input_tokens"], int) and isinstance(attrs["gen_ai.usage.output_tokens"], int)


def test_a_real_call_gets_the_framework_correlation_attributes(exporter: InMemorySpanExporter) -> None:
    _run_call()
    attrs = _span(exporter)
    assert attrs["aicf.tenant.id"] == "org-1"
    assert attrs["aicf.ai_system.id"] == "churn-v2"
    assert attrs["aicf.scenario.slug"] == "churn"
    assert attrs["aicf.policy.version"] == "v9"
    assert attrs["aicf.policy.decision"] == "degrade"


def test_the_existing_langfuse_attributes_are_untouched(exporter: InMemorySpanExporter) -> None:
    _run_call()
    attrs = _span(exporter)
    assert attrs["langfuse.trace.name"] == "assistant/churn"
    assert attrs["langfuse.observation.type"] == "generation"
    assert attrs["user.id"] == "org-1"


def test_without_policy_the_policy_attributes_are_simply_absent(exporter: InMemorySpanExporter) -> None:
    plain = {k: v for k, v in METADATA.items() if not k.startswith("aicf_policy")}
    _run_call(plain)
    attrs = _span(exporter)
    assert "aicf.policy.decision" not in attrs and "aicf.policy.version" not in attrs
    assert attrs["aicf.ai_system.id"] == "churn-v2"


def test_ai_system_falls_back_to_the_scenario_slug(exporter: InMemorySpanExporter) -> None:
    _run_call({"trace_user_id": "org-1", "trace_metadata": {"scenario_slug": "titanic"}})
    attrs = _span(exporter)
    assert attrs["aicf.ai_system.id"] == "titanic" and attrs["aicf.scenario.slug"] == "titanic"
    assert attrs["aicf.tenant.id"] == "org-1"


def test_prompts_are_in_the_span_by_default_and_redacted_when_switched_off(exporter: InMemorySpanExporter) -> None:
    _run_call()
    assert "SECRET PROMPT" in _span(exporter)["langfuse.observation.input"]

    exporter.clear()
    litellm.turn_off_message_logging = True  # what TRACE_CAPTURE_CONTENT=false sets in the proxy config
    _run_call()
    attrs = _span(exporter)
    assert "SECRET PROMPT" not in str(attrs) and "OK-REPLY" not in str(attrs)
    assert attrs["aicf.tenant.id"] == "org-1" and "gen_ai.usage.input_tokens" in attrs


def test_the_patch_is_idempotent_and_a_failure_never_breaks_the_original_attributes() -> None:
    assert tracing.install() and tracing.install()
    setter = LangfuseOtelLogger.set_langfuse_otel_attributes
    assert getattr(setter, "_aicf_wrapped", False)

    class ExplodingSpan:
        def __init__(self) -> None:
            self.set: dict = {}

        def set_attribute(self, key: str, value: object) -> None:
            if key.startswith("aicf."):
                raise RuntimeError("boom")
            self.set[key] = value

    span = ExplodingSpan()
    tracing.set_governance_attributes(span, {"model": "m", "call_type": "acompletion"}, SimpleNamespace(model="m"))
    assert span.set["gen_ai.request.model"] == "m"


@pytest.mark.parametrize(
    "call_type,expected",
    [("acompletion", "chat"), ("aembedding", "embeddings"), ("atext_completion", "text_completion"), ("weird", "chat")],
)
def test_operation_names(call_type: str, expected: str) -> None:
    assert tracing.genai_attributes({"call_type": call_type}, None)["gen_ai.operation.name"] == expected


def test_the_semconv_version_is_pinned() -> None:
    assert tracing.SEMCONV_VERSION == "1.37.0"


# --- TRACE_CAPTURE_CONTENT (app.resolve_config_path) -------------------------------------------


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "litellm_config.yaml"
    path.write_text("litellm_settings:\n  callbacks: [llm_gateway.budget_hook.instance]\n")
    return path


@pytest.mark.parametrize(
    "env", [{}, {"TRACE_CAPTURE_CONTENT": "true"}, {"TRACE_CAPTURE_CONTENT": ""}, {"TRACE_CAPTURE_CONTENT": "maybe"}]
)
def test_content_is_captured_by_default_and_the_config_is_used_verbatim(tmp_path: Path, env: dict) -> None:
    config = _config(tmp_path)
    assert app.resolve_config_path(config, env) == config


@pytest.mark.parametrize("value", ["false", "FALSE", "0", "no", "off"])
def test_switching_content_capture_off_turns_off_message_logging(tmp_path: Path, value: str) -> None:
    config = _config(tmp_path)
    merged = app.resolve_config_path(config, {"TRACE_CAPTURE_CONTENT": value})
    loaded = yaml.safe_load(merged.read_text())
    assert loaded["litellm_settings"]["turn_off_message_logging"] is True
    assert loaded["litellm_settings"]["callbacks"] == ["llm_gateway.budget_hook.instance"]
    assert "turn_off_message_logging" not in yaml.safe_load(config.read_text())["litellm_settings"]


def test_redaction_and_langfuse_tracing_combine(tmp_path: Path) -> None:
    merged = app.resolve_config_path(
        _config(tmp_path),
        {"TRACE_CAPTURE_CONTENT": "false", "LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk"},
    )
    settings = yaml.safe_load(merged.read_text())["litellm_settings"]
    assert settings["turn_off_message_logging"] is True and "langfuse_otel" in settings["callbacks"]


def test_every_attribute_the_gateway_can_add_is_documented() -> None:
    doc = (Path(__file__).resolve().parents[3] / "docs" / "governance" / "telemetry.md").read_text()
    missing = [name for name in (*tracing.GENAI_ATTRIBUTES, *tracing.AICF_ATTRIBUTES) if f"`{name}`" not in doc]
    assert missing == []
    assert f"v{tracing.SEMCONV_VERSION}" in doc, "the pinned semantic-convention version is stated in the doc"


def test_a_full_call_produces_exactly_the_documented_attribute_names(exporter: InMemorySpanExporter) -> None:
    _run_call()
    emitted = {k for k in _span(exporter) if k.startswith(("gen_ai.", "aicf."))}
    assert emitted <= {*tracing.GENAI_ATTRIBUTES, *tracing.AICF_ATTRIBUTES}
    assert {"gen_ai.request.model", "aicf.tenant.id", "aicf.ai_system.id"} <= emitted
