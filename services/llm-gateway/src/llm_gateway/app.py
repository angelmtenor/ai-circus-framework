"""
app.py
------

Entry point for llm-gateway: validates configuration, then execs the real `litellm`
proxy CLI against litellm_config.yaml. Not a custom FastAPI app — the
batteries-included LiteLLM proxy (OpenAI-compatible API, model routing, master-key
auth) is the actual server. Persistent spend-tracking is not enabled — see
litellm_config.yaml for why.

Author: ai-circus-framework contributors
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


def resolve_config_path(config_path: Path, env: dict[str, str]) -> Path:
    """Return the litellm config to run — with Langfuse tracing appended when it's configured.

    litellm_config.yaml is static and a `langfuse_otel` entry in its `callbacks` list is
    unconditional: without LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY litellm silently falls
    back to a console span exporter that dumps every completion to stdout. So the callback
    is added here, at start-up, only when both keys are present (LANGFUSE_HOST then picks
    the in-cluster instance over Langfuse Cloud) — the committed file is untouched and
    `make run` without Langfuse keeps working. The merged copy lives in a temp file; the
    exec'd litellm process reads it once at boot.
    """
    if not (env.get("LANGFUSE_PUBLIC_KEY") and env.get("LANGFUSE_SECRET_KEY")):
        return config_path

    config = yaml.safe_load(config_path.read_text()) or {}
    litellm_settings = config.setdefault("litellm_settings", {})
    callbacks = litellm_settings.setdefault("callbacks", [])
    if LANGFUSE_CALLBACK not in callbacks:
        callbacks.append(LANGFUSE_CALLBACK)

    fd, merged = tempfile.mkstemp(prefix="litellm_config.", suffix=".yaml")
    with os.fdopen(fd, "w") as handle:
        yaml.safe_dump(config, handle, sort_keys=False)
    logger.info(
        "Langfuse tracing enabled (host={}) — running merged config {}", env.get("LANGFUSE_HOST") or "cloud", merged
    )
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

    env = {**os.environ, "LITELLM_MASTER_KEY": config.LITELLM_MASTER_KEY.get_secret_value()}
    config_path = resolve_config_path(Path(config.LITELLM_CONFIG_PATH).resolve(), env)
    argv = ["litellm", "--config", str(config_path), "--port", config.HTTP_PORT, "--host", "0.0.0.0"]  # ruff: ignore[hardcoded-bind-all-interfaces]

    logger.success("llm-gateway starting on port {} (config={})", config.HTTP_PORT, config_path)
    launch(argv, env)


if __name__ == "__main__":
    main()
