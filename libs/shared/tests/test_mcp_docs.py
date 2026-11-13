"""
- Title:    Guard: the MCP guide links to files that exist, and the README points at it
- Author:   Angel Martinez-Tenor
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_GUIDE = _REPO / "docs" / "mcp" / "README.md"
# Markdown links and HTML images, both relative to the page.
_LINK = re.compile(r"\]\((?!https?://|mailto:|#)([^)#\s]+)(?:#[^)\s]*)?\)")
_IMG = re.compile(r'<img src="(?!https?://|data:)([^"]+)"')


def _broken(page: Path) -> list[str]:
    text = page.read_text(encoding="utf-8")
    return [t for t in _LINK.findall(text) + _IMG.findall(text) if not (page.parent / t).resolve().exists()]


def test_every_link_and_image_of_the_mcp_guide_resolves() -> None:
    assert _broken(_GUIDE) == [], f"docs/mcp/README.md links to missing files: {_broken(_GUIDE)}"


def test_the_guide_cites_every_mcp_decision() -> None:
    adrs = {p.name[:4] for p in (_REPO / "docs" / "adr").glob("00*.md")}
    cited = set(re.findall(r"\.\./adr/(00\d\d)-", _GUIDE.read_text(encoding="utf-8")))
    assert {f"00{n}" for n in range(19, 30)} <= adrs, "ADRs 0019-0029 exist"
    assert {f"00{n}" for n in range(19, 30)} <= cited, f"the guide cites ADRs {sorted(cited)}"


def test_the_readme_section_points_at_the_guide() -> None:
    readme = (_REPO / "README.md").read_text(encoding="utf-8")
    section = readme[readme.index("### MCP Lite Stack") :]
    section = section[: section.index("\n---\n")]
    assert "(docs/mcp/README.md)" in section
    missing = [t for t in _IMG.findall(section) + _LINK.findall(section) if not (_REPO / t).exists()]
    assert missing == [], f"the README's MCP section links to missing files: {missing}"
