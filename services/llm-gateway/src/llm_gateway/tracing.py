"""
- Title:    Standard GenAI + framework correlation attributes on the gateway's trace spans
- Author:   Angel Martinez-Tenor

Every model call already becomes an OpenTelemetry span through LiteLLM's `langfuse_otel`
callback — but that span carries OpenInference `llm.*` and Langfuse attributes, not the OpenTelemetry
GenAI semantic conventions, and nothing that lets a governance tool join a span to a tenant, an
AI system or a policy decision. This module adds both to the *same* span (it does not add a second
exporter), by wrapping `LangfuseOtelLogger.set_langfuse_otel_attributes`:

- `gen_ai.*` — OpenTelemetry GenAI semantic conventions, pinned to **v1.37.0** (`SEMCONV_VERSION`):
  `gen_ai.operation.name`, `gen_ai.provider.name` (and the older `gen_ai.system`, kept for
  consumers that predate 1.37), `gen_ai.request.model`, `gen_ai.response.model`,
  `gen_ai.response.id`, `gen_ai.response.finish_reasons`, `gen_ai.usage.input_tokens`,
  `gen_ai.usage.output_tokens`. These conventions are still marked *Development* upstream, so the
  version is pinned here and in docs/governance/telemetry.md rather than tracked.
- `aicf.*` — the framework's namespace: `aicf.tenant.id`, `aicf.ai_system.id`, `aicf.scenario.slug`,
  and, when a policy decision point is active, `aicf.policy.version` and `aicf.policy.decision`.
  They come from the request metadata the agent services send (`langfuse_request_metadata`) and
  from the policy hook.

Content-free: only ids, model names, token counts and decisions. Whether prompts and completions
appear in the *Langfuse* span (an existing feature of that monitor) is a separate switch,
`TRACE_CAPTURE_CONTENT` — see app.resolve_config_path.

The wrapper is defensive on purpose: it depends on a LiteLLM internal, so any failure (an upgrade
that moves it, an odd payload) is swallowed and the original attributes are still set.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from llm_gateway.core.logger import get_logger

logger = get_logger(__name__)

SEMCONV_VERSION = "1.37.0"

# The attributes this module can add — the single list docs/governance/telemetry.md must document
# (a test keeps the two in step).
GENAI_ATTRIBUTES = (
    "gen_ai.operation.name",
    "gen_ai.provider.name",
    "gen_ai.system",
    "gen_ai.request.model",
    "gen_ai.response.model",
    "gen_ai.response.id",
    "gen_ai.response.finish_reasons",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
)
AICF_ATTRIBUTES = (
    "aicf.tenant.id",
    "aicf.ai_system.id",
    "aicf.scenario.slug",
    "aicf.policy.version",
    "aicf.policy.decision",
)

_OPERATIONS = {
    "completion": "chat",
    "acompletion": "chat",
    "responses": "chat",
    "aresponses": "chat",
    "text_completion": "text_completion",
    "atext_completion": "text_completion",
    "embedding": "embeddings",
    "aembedding": "embeddings",
}


def _metadata(kwargs: dict) -> dict[str, Any]:
    metadata = (kwargs.get("litellm_params") or {}).get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def _first_str(*values: object) -> str | None:
    return next((v for v in values if isinstance(v, str) and v), None)


def aicf_attributes(kwargs: dict) -> dict[str, str]:
    """The framework's correlation attributes for one call (only those that are known)."""
    metadata = _metadata(kwargs)
    trace = metadata.get("trace_metadata")
    trace = trace if isinstance(trace, dict) else {}
    scenario = _first_str(metadata.get("aicf_scenario_slug"), trace.get("scenario_slug"))
    attributes = {
        "aicf.tenant.id": _first_str(metadata.get("aicf_tenant_id"), kwargs.get("user"), metadata.get("trace_user_id")),
        "aicf.ai_system.id": _first_str(metadata.get("aicf_ai_system_id"), scenario),
        "aicf.scenario.slug": scenario,
        "aicf.policy.version": _first_str(metadata.get("aicf_policy_version")),
        "aicf.policy.decision": _first_str(metadata.get("aicf_policy_decision")),
    }
    return {key: value for key, value in attributes.items() if value is not None}


def genai_attributes(kwargs: dict, response_obj: Any) -> dict[str, Any]:
    """OpenTelemetry GenAI semantic-convention attributes (v1.37.0) for one call."""
    provider = _first_str((kwargs.get("litellm_params") or {}).get("custom_llm_provider"))
    usage = getattr(response_obj, "usage", None)
    choices = getattr(response_obj, "choices", None) or []
    reasons = [r for r in (getattr(c, "finish_reason", None) for c in choices) if isinstance(r, str)]
    attributes: dict[str, Any] = {
        "gen_ai.operation.name": _OPERATIONS.get(str(kwargs.get("call_type", "")), "chat"),
        "gen_ai.provider.name": provider,
        "gen_ai.system": provider,
        "gen_ai.request.model": _first_str(kwargs.get("model")),
        "gen_ai.response.model": _first_str(getattr(response_obj, "model", None)),
        "gen_ai.response.id": _first_str(getattr(response_obj, "id", None)),
        "gen_ai.response.finish_reasons": reasons or None,
        "gen_ai.usage.input_tokens": getattr(usage, "prompt_tokens", None),
        "gen_ai.usage.output_tokens": getattr(usage, "completion_tokens", None),
    }
    return {key: value for key, value in attributes.items() if value is not None}


def set_governance_attributes(span: Any, kwargs: dict, response_obj: Any) -> None:
    """Add the `gen_ai.*` and `aicf.*` attributes to `span`; never raises."""
    try:
        for key, value in (genai_attributes(kwargs, response_obj) | aicf_attributes(kwargs)).items():
            span.set_attribute(key, value)
    except Exception:  # tracing must never break a model call
        logger.opt(exception=True).warning("could not add governance attributes to a trace span")


def install() -> bool:
    """Wrap LiteLLM's Langfuse span-attribute setter (idempotent). Returns whether it is in place.

    Inert when `langfuse_otel` is not configured — the wrapped function is simply never called.
    """
    try:
        from litellm.integrations.langfuse.langfuse_otel import LangfuseOtelLogger
    except Exception:
        logger.warning("LiteLLM's langfuse_otel integration not importable — gen_ai.*/aicf.* span attributes are off")
        return False
    original: Callable[..., Any] = LangfuseOtelLogger.set_langfuse_otel_attributes
    if getattr(original, "_aicf_wrapped", False):
        return True

    def wrapped(span: Any, kwargs: dict, response_obj: Any) -> None:
        original(span, kwargs, response_obj)
        set_governance_attributes(span, kwargs, response_obj)

    wrapped._aicf_wrapped = True  # type: ignore[attr-defined]
    LangfuseOtelLogger.set_langfuse_otel_attributes = staticmethod(wrapped)  # type: ignore[method-assign]
    return True
