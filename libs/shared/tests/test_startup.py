"""Tests for ai_circus_shared.startup.wait_for."""

from __future__ import annotations

import pytest

from ai_circus_shared.startup import wait_for


def test_wait_for_retries_transient_errors_until_success() -> None:
    calls: list[int] = []
    sleeps: list[float] = []

    def attempt() -> str:
        calls.append(1)
        if len(calls) < 4:
            raise ConnectionError("refused")
        return "up"

    result = wait_for(
        attempt, what="dep", retryable=lambda e: isinstance(e, ConnectionError), timeout_seconds=60, sleep=sleeps.append
    )
    assert result == "up"
    assert len(calls) == 4
    assert sleeps == [0.5, 1.0, 2.0]  # capped exponential backoff


def test_wait_for_raises_non_retryable_errors_immediately() -> None:
    sleeps: list[float] = []
    with pytest.raises(ValueError, match="bad credentials"):
        wait_for(
            lambda: (_ for _ in ()).throw(ValueError("bad credentials")),
            what="dep",
            retryable=lambda e: isinstance(e, ConnectionError),
            sleep=sleeps.append,
        )
    assert sleeps == []


def test_wait_for_gives_up_after_the_deadline() -> None:
    def attempt() -> None:
        raise ConnectionError("still down")

    with pytest.raises(ConnectionError, match="still down"):
        wait_for(attempt, what="dep", retryable=lambda e: True, timeout_seconds=0, sleep=lambda _s: None)
