#!/usr/bin/env python3
"""Generate the markdown plans of a threshold rulebook from its YAML (the single source of truth).

    libs/shared/.venv/bin/python scripts/generate_rulebook.py scenarios/water_reservoirs_drought/rulebook [--check]

Writes plan_<lang>.md next to rulebook.yaml. `--check` writes nothing and exits 1 if a generated file differs, so
CI and a test can catch an edit made to the markdown instead of the YAML.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ai_circus_shared.rulebook import expected_files, load


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("directory", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files = expected_files(load(args.directory / "rulebook.yaml"), args.directory)
    stale = [p for p, text in files.items() if not p.exists() or p.read_text(encoding="utf-8") != text]
    if args.check:
        print("rulebook markdown is up to date" if not stale else f"out of date: {[p.name for p in stale]}")
        return 1 if stale else 0
    for path, text in files.items():
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
