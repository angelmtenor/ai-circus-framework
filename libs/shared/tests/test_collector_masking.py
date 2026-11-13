"""
test_collector_masking.py
-------------------------

The OpenTelemetry Collector masks tokens and email addresses in every log line before it is stored
(observability/collector/otel-collector.yaml, docs/adr/0028): the framework's services never log them, but the
third-party ones it runs may (a chat UI logs the signed-in user's email). These tests keep the masking in the logs
pipeline and check its patterns against real lines, so a config edit cannot silently drop it.

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
CONFIG = yaml.safe_load((REPO / "observability" / "collector" / "otel-collector.yaml").read_text())
MASK = "transform/ai-circus-mask-logs"


def masking_rules() -> list[tuple[str, str]]:
    """(regex, replacement) of each body rule, unescaped as the collector's statement language reads them."""
    rules = []
    for group in CONFIG["processors"][MASK]["log_statements"]:
        for statement in group["statements"]:
            match = re.fullmatch(r'replace_pattern\(body, "(.+)", "(<\w+>)"\)', statement)
            if match:
                rules.append((match.group(1).replace("\\\\", "\\"), match.group(2)))
    return rules


def mask(line: str) -> str:
    for pattern, replacement in masking_rules():
        line = re.sub(pattern, replacement, line)
    return line


def test_every_log_line_is_masked_before_it_is_stored() -> None:
    processors = CONFIG["service"]["pipelines"]["logs"]["processors"]
    assert MASK in processors
    assert processors.index(MASK) < processors.index("batch"), "masked before batching and export"
    statements = [s for group in CONFIG["processors"][MASK]["log_statements"] for s in group["statements"]]
    for what in ("<token>", "<email>"):
        assert any(s.startswith("replace_pattern(body") and what in s for s in statements), what
        assert any(s.startswith("replace_all_patterns(attributes") and what in s for s in statements), what


@pytest.mark.parametrize(
    ("line", "masked"),
    [
        ("login success email: manager@lite-demo.local", "login success email: <email>"),
        ("to a.b+c@mail.example.co.uk today", "to <email> today"),
        ("Bearer eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.c2lnbmF0dXJl", "Bearer <token>"),
        ("react@18.3.1 and librechat@0.8.8 stay", "react@18.3.1 and librechat@0.8.8 stay"),
        ("tool=search_customers outcome=ok", "tool=search_customers outcome=ok"),
    ],
)
def test_tokens_and_emails_are_masked_and_nothing_else(line: str, masked: str) -> None:
    assert mask(line) == masked
