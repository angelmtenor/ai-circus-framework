#!/usr/bin/env bash
# Removes the template's generic "check_service" example tool from a freshly
# generated service (every real service replaces it with its own actual logic).
# Usage: ./scripts/strip_example_tool.sh <service-name>
set -euo pipefail

SERVICE_NAME="${1:?usage: strip_example_tool.sh <service-name>}"
PACKAGE_NAME="${SERVICE_NAME//-/_}"
DIR="services/$SERVICE_NAME"

rm -f "$DIR/src/$PACKAGE_NAME/tools/check_service.py" "$DIR/tests/test_check_service.py"

cat > "$DIR/src/$PACKAGE_NAME/tools/__init__.py" <<PY
"""Tools module for $SERVICE_NAME.

This module provides command-line tools and utilities:
- hello_world: Basic demonstration tool
"""

from __future__ import annotations

from .hello_world import main as hello_world

__all__ = [
    "hello_world",
]
PY

python3 - "$DIR/pyproject.toml" "$SERVICE_NAME" <<'PY'
import sys

path, service_name = sys.argv[1], sys.argv[2]
text = open(path).read()  # noqa: SIM115, PTH123
text = text.replace(f"{service_name}-check-service = \"{service_name.replace('-', '_')}.tools.check_service:main\"\n", "")
open(path, "w").write(text)  # noqa: SIM115, PTH123
PY

python3 - "$DIR/Makefile" <<'PY'
import re
import sys

path = sys.argv[1]
text = open(path).read()  # noqa: SIM115, PTH123
text = text.replace(" check-service", "")
text = re.sub(r"\ncheck-service:.*\n\t.*\n", "\n", text)
open(path, "w").write(text)  # noqa: SIM115, PTH123
PY

echo "✓ stripped example check_service tool from $DIR"
