"""Tool "Hello World" for etl-vectorize.
Author: ai-circus-framework contributors
"""

from __future__ import annotations

from etl_vectorize import get_env_config
from etl_vectorize.core.info import info_system
from etl_vectorize.core.logger import configure_logger, get_logger

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
