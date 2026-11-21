#!/usr/bin/env python3
"""Regenerate everything drawn from the architecture catalog (libs/shared/src/ai_circus_shared/architecture_catalog.yaml):

- docs/architecture/architecture.svg   the layered map (README and the architecture guide embed it)
- docs/architecture/components.md      every component by layer and every request flow, step by step
- ui-react/src/generated/architecture.json   the app's Architecture page and each scenario's "How it's built"

Run from the repository root with the shared library's environment, after editing the catalog:

    libs/shared/.venv/bin/python scripts/generate_architecture.py [--check]

`--check` writes nothing and fails when a committed file differs (the libs/shared tests run the same comparison).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from ai_circus_shared.architecture import export, load_catalog, render_markdown, render_svg

ROOT = Path(__file__).resolve().parent.parent


def adr_files(root: Path = ROOT) -> dict[int, str]:
    """ADR number -> file name, from docs/adr/."""
    out: dict[int, str] = {}
    for path in sorted((root / "docs" / "adr").glob("[0-9][0-9][0-9][0-9]-*.md")):
        match = re.match(r"(\d{4})-", path.name)
        if match:
            out[int(match.group(1))] = path.name
    return out


def expected(root: Path = ROOT) -> dict[Path, str]:
    """Every generated file and its content."""
    catalog = load_catalog()
    adrs = adr_files(root)
    return {
        root / "docs" / "architecture" / "architecture.svg": render_svg(catalog),
        root / "docs" / "architecture" / "components.md": render_markdown(catalog, adrs),
        root / "ui-react" / "src" / "generated" / "architecture.json": json.dumps(export(catalog, adrs), indent=1, ensure_ascii=False) + "\n",
    }


def main() -> int:
    check = "--check" in sys.argv[1:]
    drifted = []
    for path, content in expected().items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == content:
            continue
        if check:
            drifted.append(path.relative_to(ROOT).as_posix())
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            print(f"wrote {path.relative_to(ROOT)}")
    if drifted:
        print("Out of date (run scripts/generate_architecture.py):", ", ".join(drifted))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
