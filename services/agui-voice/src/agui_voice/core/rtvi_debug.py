"""
- Title:    Temporary RTVI handshake diagnostics
- Author:   ai-circus-framework contributors

TEMPORARY instrumentation for a live incident: the real browser client (via
`@pipecat-ai/client-js`) reaches agui-voice, sends `client-ready`, and the server
appears to process it (visible in logs), but the client never receives `bot-ready`
and hangs on "Connecting…" indefinitely. Every hand-rolled test client used to probe
this manually *does* get `bot-ready` back, so the break is somewhere in the real
client's exact handshake path that a synthetic client isn't reproducing. Wraps the
handshake's key methods to log entry/exit/exceptions without changing behavior, so
the next real reproduction shows exactly where the chain stops — remove once
root-caused (see the branch's PR/commit history for context).
"""

from __future__ import annotations

import functools

from loguru import logger
from pipecat.processors.frameworks.rtvi.processor import RTVIProcessor

_PATCHED = False


def _wrap(name: str) -> None:
    original = getattr(RTVIProcessor, name)

    @functools.wraps(original)
    async def wrapped(self: RTVIProcessor, *args: object, **kwargs: object) -> object:
        logger.info("RTVI DEBUG: entering {} args={!r} kwargs={!r}", name, args, kwargs)
        try:
            result = await original(self, *args, **kwargs)
            logger.info("RTVI DEBUG: {} returned {!r}", name, result)
            return result
        except Exception:
            logger.exception("RTVI DEBUG: {} raised", name)
            raise

    setattr(RTVIProcessor, name, wrapped)


def install() -> None:
    """Patch RTVIProcessor's handshake methods with logging wrappers, once per process."""
    global _PATCHED
    if _PATCHED:
        return
    for name in ("_handle_message", "set_client_ready", "set_bot_ready", "_send_bot_ready"):
        _wrap(name)
    _PATCHED = True
    logger.warning("RTVI DEBUG: handshake instrumentation installed — remove core/rtvi_debug.py once root-caused")
