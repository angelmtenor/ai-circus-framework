"""The RTVI handshake instrumentation logs message arguments, so it must be opt-in."""

from __future__ import annotations

import pytest
from pipecat.processors.frameworks.rtvi.processor import RTVIProcessor

from agui_voice.core import rtvi_debug


@pytest.mark.parametrize("value", [None, "", "false", "0", "yes"])
def test_install_is_a_no_op_unless_rtvi_debug_is_true(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    monkeypatch.setattr(rtvi_debug, "_PATCHED", False)
    if value is None:
        monkeypatch.delenv("RTVI_DEBUG", raising=False)
    else:
        monkeypatch.setenv("RTVI_DEBUG", value)
    before = RTVIProcessor._handle_message
    rtvi_debug.install()
    assert RTVIProcessor._handle_message is before
    assert rtvi_debug._PATCHED is False
