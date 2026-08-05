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
from pathlib import Path

from pydantic import ValidationError

from llm_gateway import get_env_config
from llm_gateway.core.logger import configure_logger, get_logger

logger = get_logger(__name__)


def launch(argv: list[str], env: dict[str, str]) -> None:
    """Replace the current process with the LiteLLM proxy (correct PID-1 signal handling)."""
    os.execvpe(argv[0], argv, env)  # ruff: ignore[start-process-with-no-shell]


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

    config_path = Path(config.LITELLM_CONFIG_PATH).resolve()
    env = {**os.environ, "LITELLM_MASTER_KEY": config.LITELLM_MASTER_KEY.get_secret_value()}
    argv = ["litellm", "--config", str(config_path), "--port", config.HTTP_PORT, "--host", "0.0.0.0"]  # ruff: ignore[hardcoded-bind-all-interfaces]

    logger.success("llm-gateway starting on port {} (config={})", config.HTTP_PORT, config_path)
    launch(argv, env)


if __name__ == "__main__":
    main()
