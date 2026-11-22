"""Tests for ai_circus_shared.spoken: how agui-voice marks a spoken turn and how the agents recognise it."""

from __future__ import annotations

import pytest

from ai_circus_shared.spoken import SPOKEN_PROP, SPOKEN_REPLY_INSTRUCTIONS, is_spoken, spoken_props


def test_the_props_agui_voice_sends_are_recognised() -> None:
    assert is_spoken(spoken_props())
    assert spoken_props() == {SPOKEN_PROP: True}


@pytest.mark.parametrize(
    "props",
    [None, {}, {"spoken": False}, {"spoken": "true"}, {"spoken": 1}, {"scope": "documents"}, "spoken", ["spoken"]],
)
def test_anything_else_is_a_typed_turn(props: object) -> None:
    """Only exactly `true` counts: a typed turn's props, or a malformed value, never shortens a reply."""
    assert not is_spoken(props)


def test_the_instructions_ask_for_a_short_plain_answer_and_keep_the_tools() -> None:
    text = SPOKEN_REPLY_INSTRUCTIONS
    assert text.startswith("\n\n")  # appended to a system prompt as its own paragraph
    assert "60 words" in text
    assert "no lists, tables" in text
    assert "tools exactly as you would" in text
    # The chat's screen actions reach a spoken turn too (agui-voice relays them): use them, and say what they did.
    assert "act on the screen included" in text
    assert "what is now on screen" in text
