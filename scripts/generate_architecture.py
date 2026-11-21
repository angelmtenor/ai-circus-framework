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

from ai_circus_shared.architecture import (
    export,
    load_catalog,
    render_markdown,
    render_svg,
    scenario_components_markdown,
    split_markdown,
)
from ai_circus_shared.scenario_schema import load_all

ROOT = Path(__file__).resolve().parent.parent
# The architecture tutorial (scenarios/framework_architecture) searches this repository's own documentation: a
# snapshot cut into small pieces, rewritten with everything else here so it never drifts from the docs.
TUTORIAL_DOCS = ROOT / "scenarios" / "framework_architecture" / "sample_docs"
TUTORIAL_SOURCES = [
    "docs/architecture/README.md",
    "docs/architecture/components.md",
    "docs/architecture-details.md",
    "docs/mcp/README.md",
    "docs/data-platform/README.md",
    "docs/governance/README.md",
    "docs/adr/*.md",
    "docs/scenarios/*.md",
]


def adr_files(root: Path = ROOT) -> dict[int, str]:
    """ADR number -> file name, from docs/adr/."""
    out: dict[int, str] = {}
    for path in sorted((root / "docs" / "adr").glob("[0-9][0-9][0-9][0-9]-*.md")):
        match = re.match(r"(\d{4})-", path.name)
        if match:
            out[int(match.group(1))] = path.name
    return out


def tutorial_docs(root: Path = ROOT) -> dict[Path, str]:
    """The architecture tutorial's documents: every source cut into small pieces, plus what each scenario uses."""
    out: dict[Path, str] = {}
    folder = root / TUTORIAL_DOCS.relative_to(ROOT)
    sources = sorted({p for pattern in TUTORIAL_SOURCES for p in root.glob(pattern) if p.name != "README.md" or "adr" not in p.parts})
    for path in sources:
        rel = path.relative_to(root).as_posix()
        stem = rel.removesuffix(".md").replace("/", "-").lower()
        for i, piece in enumerate(split_markdown(rel, path.read_text(encoding="utf-8")), start=1):
            out[folder / f"{stem}--{i:02d}.md"] = piece
    components = scenario_components_markdown(load_all(root / "scenarios"))
    for i, piece in enumerate(split_markdown("generated from every scenario.yaml", components), start=1):
        out[folder / f"scenario-components--{i:02d}.md"] = piece
    return out


def expected(root: Path = ROOT) -> dict[Path, str]:
    """Every generated file and its content."""
    catalog = load_catalog()
    adrs = adr_files(root)
    return {
        **tutorial_docs(root),
        root / "docs" / "architecture" / "architecture.svg": render_svg(catalog),
        root / "docs" / "architecture" / "components.md": render_markdown(catalog, adrs),
        root / "ui-react" / "src" / "generated" / "architecture.json": json.dumps(export(catalog, adrs), indent=1, ensure_ascii=False) + "\n",
    }


def main() -> int:
    check = "--check" in sys.argv[1:]
    drifted = []
    files = expected()
    for path, content in files.items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == content:
            continue
        if check:
            drifted.append(path.relative_to(ROOT).as_posix())
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            print(f"wrote {path.relative_to(ROOT)}")
    # A piece that is no longer generated (a source shrank or was removed) is stale.
    stale = sorted(p for p in TUTORIAL_DOCS.glob("*.md") if p not in files)
    for path in stale:
        if check:
            drifted.append(path.relative_to(ROOT).as_posix())
        else:
            path.unlink()
            print(f"removed {path.relative_to(ROOT)}")
    if drifted:
        print("Out of date (run scripts/generate_architecture.py):", ", ".join(drifted))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
