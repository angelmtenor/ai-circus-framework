"""Tool "Hello World" for agui-voice.
Author: ai-circus-framework contributors
"""

from __future__ import annotations

from agui_voice import get_env_config
from agui_voice.core.info import info_system
from agui_voice.core.logger import configure_logger, get_logger

logger = get_logger(__name__)


class SimpleClass:
    """A simple class for demonstration purposes."""

    def __init__(self, name: str) -> None:
        """Initialize the class with a name."""
        self.name = name

    def greet(self) -> None:
        """Print a greeting message."""
        logger.info(f"Hello, {self.name}!")


def main() -> None:
    """Main function to demonstrate the functionality of the module."""
    configure_logger()
    info_system()
    config = get_env_config()
    simple = SimpleClass(config.LOG_LEVEL)
    simple.greet()


if __name__ == "__main__":
    main()
