"""
- Title:    Pipecat "LLM stage" that bridges to an existing AG-UI agent endpoint
- Author:   ai-circus-framework contributors

`AgentBridgeProcessor` sits where an LLM stage normally goes in a Pipecat cascade
pipeline (STT -> [this] -> TTS). Instead of calling an LLM directly, it POSTs each
final user utterance to the *existing* `POST /agui/{scenario_slug}` SSE endpoint of
whichever of assistant/rag-agent/form-agent serves this scenario's kind, forwarding
the caller's own bearer token — so that service's `resolve_identity` entitlement
check runs exactly as it does for a typed chat turn, and no agent/LLM logic is
duplicated here. See root CLAUDE.md's "Tenancy & entitlements" and the plan at
`.claude/plans/cozy-dazzling-biscuit.md` for the full design rationale.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any
from uuid import uuid4

import httpx
from ag_ui.core import AssistantMessage, Message, RunAgentInput, UserMessage
from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InterruptionFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    ManuallySwitchServiceFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.utils.asyncio.task_manager import BaseTaskManager


class AgentBridgeProcessor(FrameProcessor):
    """Bridges STT transcripts to an upstream `/agui/{scenario_slug}` SSE endpoint.

    Conversation history is held in-memory for the lifetime of this processor (one
    per live voice session) and resent in full on every turn — the upstream agent
    services are stateless per request, the same client-side responsibility
    `ui-react`'s `HttpAgent` already has (see `useScenarioAgent.ts`).
    """

    def __init__(
        self,
        *,
        upstream_url: str,
        scenario_slug: str,
        authorization: str,
        http_client: httpx.AsyncClient,
        tts_language_switch: Mapping[str, FrameProcessor] | None = None,
        **kwargs: Any,
    ) -> None:
        """Bind this processor to one upstream agent endpoint and caller identity for the life of the session.

        `tts_language_switch` maps a detected STT language code (e.g. "es") to the
        already-loaded `FrameProcessor` in a downstream `ServiceSwitcher` that speaks
        that language — see `core/providers.py`'s `build_tts_service`. `None`/`{}`
        when the configured TTS provider doesn't support switching (only Piper does).
        """
        super().__init__(**kwargs)
        self._upstream_url = upstream_url
        self._scenario_slug = scenario_slug
        self._authorization = authorization
        self._client = http_client
        self._tts_language_switch = tts_language_switch or {}
        self._current_tts_language: str | None = None
        self._messages: list[Message] = []
        self._inflight: asyncio.Task | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Route STT transcripts to `_run_agent`, forward everything else, and cancel on barge-in."""
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame) and frame.text.strip():
            await self._maybe_switch_tts_language(frame)
            self._messages.append(UserMessage(id=str(uuid4()), content=frame.text))
            task_manager: BaseTaskManager = self.task_manager
            self._inflight = task_manager.create_task(self._run_agent(), name=f"agent-bridge-{self._scenario_slug}")
        elif isinstance(frame, InterruptionFrame):
            if self._inflight is not None:
                task_manager: BaseTaskManager = self.task_manager
                await task_manager.cancel_task(self._inflight)
                self._inflight = None
            await self.push_frame(frame, direction)
        else:
            await self.push_frame(frame, direction)

    async def _maybe_switch_tts_language(self, frame: TranscriptionFrame) -> None:
        """Switch the downstream TTS `ServiceSwitcher` to match this utterance's
        detected language, if it changed and that language is one we have a voice
        for — a WhisperSTTService with `language=None` (see providers.py) detects
        the spoken language per utterance rather than assuming it's always English.
        """
        if not self._tts_language_switch or frame.language is None:
            return
        language = frame.language.value
        if language == self._current_tts_language or language not in self._tts_language_switch:
            return
        self._current_tts_language = language
        await self.push_frame(ManuallySwitchServiceFrame(service=self._tts_language_switch[language]))

    async def _run_agent(self) -> None:
        """POST the growing transcript upstream, streaming the SSE reply back downstream as TTS input."""
        payload = RunAgentInput(
            thread_id=self._scenario_slug,
            run_id=str(uuid4()),
            state=None,
            messages=self._messages,
            tools=[],
            context=[],
            forwarded_props=None,
        )
        reply = ""
        await self.push_frame(LLMFullResponseStartFrame())
        try:
            async with self._client.stream(
                "POST",
                self._upstream_url,
                content=payload.model_dump_json(by_alias=True),
                headers={
                    "Authorization": self._authorization,
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                },
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    event = json.loads(line.removeprefix("data: "))
                    if event.get("type") == "TEXT_MESSAGE_CONTENT":
                        delta = event.get("delta", "")
                        reply += delta
                        if delta:
                            await self.push_frame(LLMTextFrame(text=delta))
                    elif event.get("type") == "RUN_ERROR":
                        logger.error(
                            "upstream agent run failed for {!r}: {}",
                            self._scenario_slug,
                            event.get("message"),
                        )
        except Exception:
            logger.exception("agent bridge call failed for scenario={!r}", self._scenario_slug)
        finally:
            if reply:
                self._messages.append(AssistantMessage(id=str(uuid4()), content=reply))
            self._inflight = None
            await self.push_frame(LLMFullResponseEndFrame())
