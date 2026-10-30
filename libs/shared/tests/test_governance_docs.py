"""
- Title:    Guard: the governance docs link to files that exist and cover every published schema
- Author:   Angel Martinez-Tenor
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_DOCS = Path(__file__).resolve().parents[3] / "docs" / "governance"
_LINK = re.compile(r"\]\((?!https?://|mailto:|#)([^)#\s]+)(?:#[^)\s]*)?\)")
_PAGES = sorted(_DOCS.glob("*.md"))


def test_the_governance_pages_exist() -> None:
    assert {p.name for p in _PAGES} >= {"README.md", "policy.md", "events-and-audit.md", "telemetry.md", "inventory.md"}


@pytest.mark.parametrize("page", _PAGES, ids=lambda p: p.name)
def test_every_relative_link_resolves(page: Path) -> None:
    broken = [
        target
        for target in _LINK.findall(page.read_text(encoding="utf-8"))
        if not (page.parent / target).resolve().exists()
    ]
    assert broken == [], f"{page.name} links to missing files: {broken}"


def test_every_published_schema_is_mentioned_in_the_docs() -> None:
    text = "\n".join(page.read_text(encoding="utf-8") for page in _PAGES)
    unmentioned = [
        schema.name
        for schema in sorted((_DOCS / "schemas").glob("*.json"))
        if schema.name not in text and not schema.name.startswith("audit-verification")
    ]
    assert unmentioned == [], f"schemas no doc points at: {unmentioned}"
