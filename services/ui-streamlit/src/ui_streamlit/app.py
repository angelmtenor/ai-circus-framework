"""
app.py
------

Entry point for ui-streamlit: validates configuration, then execs `streamlit run
streamlit_app.py` — the actual Streamlit UI script that gets rerun on every
interaction. Same "validate then exec" pattern as llm-gateway.

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from pydantic import ValidationError

from ui_streamlit import get_env_config
from ui_streamlit.core.logger import configure_logger, get_logger

logger = get_logger(__name__)


def launch(argv: list[str], env: dict[str, str]) -> None:
    """Replace the current process with the Streamlit server (correct PID-1 signal handling)."""
    os.execvpe(argv[0], argv, env)  # ruff: ignore[start-process-with-no-shell]


def main() -> None:
    """Validate configuration, then exec the Streamlit CLI against streamlit_app.py."""
    configure_logger()

    try:
        config = get_env_config()
    except ValidationError as e:
        logger.error("Configuration error: Mandatory environment variable(s) missing or invalid:")
        for error in e.errors():
            logger.error("  {}: {}", " -> ".join(str(loc) for loc in error["loc"]), error["msg"])
        sys.exit(1)

    script_path = Path(__file__).parent / "streamlit_app.py"
    argv = [
        "streamlit",
        "run",
        str(script_path),
        "--server.port",
        config.HTTP_PORT,
        "--server.address",
        "0.0.0.0",  # ruff: ignore[hardcoded-bind-all-interfaces]
        "--server.headless",
        "true",
    ]

    logger.success("ui-streamlit starting on port {}", config.HTTP_PORT)
    launch(argv, dict(os.environ))


if __name__ == "__main__":
    main()
