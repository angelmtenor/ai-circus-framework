"""
app.py
------

Entry point for llm-gateway: validates configuration, then execs the real `litellm`
proxy CLI against litellm_config.yaml. Not a custom FastAPI app — the
batteries-included LiteLLM proxy (OpenAI-compatible API, model routing, master-key
auth) is the actual server. Persistent spend-tracking is not enabled — see
litellm_config.yaml for why.

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import yaml
from pydantic import ValidationError

from llm_gateway import get_env_config
from llm_gateway.core.logger import configure_logger, get_logger

logger = get_logger(__name__)


def launch(argv: list[str], env: dict[str, str]) -> None:
    """Replace the current process with the LiteLLM proxy (correct PID-1 signal handling)."""
    os.execvpe(argv[0], argv, env)  # ruff: ignore[start-process-with-no-shell]


LANGFUSE_CALLBACK = "langfuse_otel"
OTLP_CALLBACK = "otel"
# The platform's collector (ADR 0015). Only the generic variable: LiteLLM's `otel` callback reads
# OTEL_EXPORTER_OTLP_ENDPOINT itself (appending /v1/traces), not the per-signal TRACES variant.
OTLP_ENDPOINT_VAR = "OTEL_EXPORTER_OTLP_ENDPOINT"


def otlp_enabled(env: dict[str, str]) -> bool:
    """Whether the platform gave a collector endpoint and did not switch the OTel SDK off."""
    return (
        bool(env.get(OTLP_ENDPOINT_VAR, "").strip())
        and env.get("OTEL_SDK_DISABLED", "").strip().lower() != "true"
        and env.get("OTEL_TRACES_EXPORTER", "").strip().lower() != "none"
    )


def launch_env(env: dict[str, str]) -> dict[str, str]:
    """The exec'd proxy's environment: its JSON logs and OTel service name follow the platform's."""
    merged = dict(env)
    if merged.get("LOG_FORMAT", "").strip().lower() == "json":
        merged["JSON_LOGS"] = "true"  # LiteLLM's own one-object-per-line logging
    merged.setdefault("OTEL_SERVICE_NAME", "llm-gateway")
    return merged


def env_flag(value: str, *, default: bool) -> bool:
    """A true/false env string; an empty or unrecognised value keeps `default`."""
    lowered = value.strip().lower()
    if lowered in {"1", "true", "yes", "on"}:
        return True
    if lowered in {"0", "false", "no", "off"}:
        return False
    return default


def resolve_config_path(config_path: Path, env: dict[str, str]) -> Path:
    """Return the litellm config to run — merged with whatever the environment switches on.

    litellm_config.yaml is static, so anything conditional on the environment is merged in here,
    at start-up, into a temp copy (the committed file is untouched; the exec'd litellm process reads
    the copy once at boot). With nothing to merge the committed file is returned as-is.

    **Langfuse tracing**: a `langfuse_otel` entry in `callbacks` would be unconditional, and without
    LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY litellm silently falls back to a console span exporter
    that dumps every completion to stdout — so the callback is added only when both keys are present
    (LANGFUSE_HOST then picks the in-cluster instance over Langfuse Cloud).

    **Trace content** (`TRACE_CAPTURE_CONTENT`): prompts and completions are part of the Langfuse span
    today — a deliberate feature of that monitor — so the default is unchanged. Set it to a false value
    and litellm's `turn_off_message_logging` replaces the message text with `redacted-by-litellm` in
    every logging callback, while tenant, model, token counts and the `gen_ai.*` / `aicf.*` attributes
    stay (see docs/governance/telemetry.md).

    **Platform traces** (`OTEL_EXPORTER_OTLP_ENDPOINT`, ADR 0015): LiteLLM's generic `otel` callback
    exports each call to the platform's collector, always without message content
    (`callback_settings.otel.message_logging: false` — prompts stay in Langfuse alone, whatever
    TRACE_CAPTURE_CONTENT says). It continues the caller's `traceparent`, so the span joins the agent's
    trace in the trace store under the same trace id Langfuse records. Langfuse's callback keeps a
    private tracer provider, so its content-bearing spans never reach this exporter.
    """
    tracing_on = bool(env.get("LANGFUSE_PUBLIC_KEY") and env.get("LANGFUSE_SECRET_KEY"))
    redact = "TRACE_CAPTURE_CONTENT" in env and not env_flag(env["TRACE_CAPTURE_CONTENT"], default=True)
    otlp_on = otlp_enabled(env)
    if not (tracing_on or redact or otlp_on):
        return config_path

    config = yaml.safe_load(config_path.read_text()) or {}
    litellm_settings = config.setdefault("litellm_settings", {})
    if tracing_on or otlp_on:
        callbacks = litellm_settings.setdefault("callbacks", [])
        for callback, wanted in ((LANGFUSE_CALLBACK, tracing_on), (OTLP_CALLBACK, otlp_on)):
            if wanted and callback not in callbacks:
                callbacks.append(callback)
    if otlp_on:
        config.setdefault("callback_settings", {}).setdefault(OTLP_CALLBACK, {})["message_logging"] = False
    if redact:
        litellm_settings["turn_off_message_logging"] = True

    fd, merged = tempfile.mkstemp(prefix="litellm_config.", suffix=".yaml")
    with os.fdopen(fd, "w") as handle:
        yaml.safe_dump(config, handle, sort_keys=False)
    if tracing_on:
        logger.info(
            "Langfuse tracing enabled (host={}) — running merged config {}", env.get("LANGFUSE_HOST") or "cloud", merged
        )
    if redact:
        logger.info("TRACE_CAPTURE_CONTENT is off — prompts and completions are redacted from traces and logs")
    if otlp_on:
        logger.info("Platform tracing enabled (OTLP {}, no message content)", env[OTLP_ENDPOINT_VAR].strip())
    return Path(merged)


def main() -> None:
    """Validate configuration, then exec the LiteLLM proxy CLI."""
    configure_logger()

    try:
        config = get_env_config()
    except ValidationError as e:
        logger.error("Configuration error: Mandatory environment variable(s) missing or invalid:")
        for error in e.errors():
            logger.error("  {}: {}", " -> ".join(str(loc) for loc in error["loc"]), error["msg"])
        sys.exit(1)

    env = launch_env({**os.environ, "LITELLM_MASTER_KEY": config.LITELLM_MASTER_KEY.get_secret_value()})
    config_path = resolve_config_path(Path(config.LITELLM_CONFIG_PATH).resolve(), env)
    argv = ["litellm", "--config", str(config_path), "--port", config.HTTP_PORT, "--host", "0.0.0.0"]  # ruff: ignore[hardcoded-bind-all-interfaces]

    logger.success("llm-gateway starting on port {} (config={})", config.HTTP_PORT, config_path)
    launch(argv, env)


if __name__ == "__main__":
    main()
