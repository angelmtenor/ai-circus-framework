"""
- Title:    Pipecat "LLM stage" that bridges to an existing AG-UI agent endpoint
- Author:   Angel Martinez-Tenor

`AgentBridgeProcessor` sits where an LLM stage normally goes in a Pipecat cascade
pipeline (STT -> [this] -> TTS). Instead of calling an LLM directly, it POSTs each
final user utterance to the *existing* `POST /agui/{scenario_slug}` SSE endpoint of
whichever of assistant/rag-agent/form-agent serves this scenario's kind, forwarding
the caller's own bearer token — so that service's `resolve_identity` entitlement
check runs exactly as it does for a typed chat turn, and no agent/LLM logic is
duplicated here. See root CLAUDE.md's "Tenancy & entitlements" and the plan at
`.claude/plans/cozy-dazzling-biscuit.md` for the full design rationale.

A spoken turn is a typed turn said aloud (docs/adr/0033): when the user presses to talk,
the browser sends what its chat would send with a typed question (an RTVI
`client-message` of type `voice-turn`: the chat's own tools, its readable context, its
forwarded props and its transcript), and the run carries them. A call to one of those
tools (a screen action: fill the form, draw a chart, open a page) goes back to the browser
as a `server-message` of type `frontend-tool-call`, which runs it as it would after a typed
turn; the agent then runs once more with the same answer the chat gives it ("Displayed to
the user."), so the user also hears what was done.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any
from uuid import uuid4

import httpx
from ag_ui.core import (
    AssistantMessage,
    Context,
    FunctionCall,
    Message,
    RunAgentInput,
    Tool,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from ai_circus_shared.spoken import spoken_props
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
from pipecat.processors.frameworks.rtvi.frames import RTVIClientMessageFrame, RTVIServerMessageFrame
from pipecat.utils.asyncio.task_manager import BaseTaskManager
from pydantic import TypeAdapter, ValidationError

TURN_MESSAGE = "voice-turn"
"""The RTVI `client-message` type (`t`) carrying the chat's tools, context, props and transcript; ui-react's
useVoiceSession.ts sends it on each press."""

FRONTEND_TOOL_CALL_MESSAGE = "frontend-tool-call"
"""The RTVI `server-message` type that hands one of the chat's own tool calls to the browser to carry out."""

FRONTEND_TOOL_ANSWER = "Displayed to the user."
"""What a screen action answers the model: ui-react's chatProvenance.ts gives a typed turn's calls the same text."""

MAX_RUNS_PER_TURN = 3
"""Agent runs in one spoken turn: the first, then one after each round of screen actions, so the answer is said."""

MAX_TURN_BYTES = 512_000
"""The largest `voice-turn` message (as JSON) taken; a bigger one is refused and the turn runs without it."""

MAX_TURN_TOOLS = 32
MAX_TURN_CONTEXT = 32
MAX_TURN_MESSAGES = 100

# Roles a chat transcript may carry here: no system or developer message from the browser.
_TRANSCRIPT_ROLES = frozenset({"user", "assistant", "tool"})
_TOOLS = TypeAdapter(list[Tool])
_CONTEXT = TypeAdapter(list[Context])
_MESSAGES = TypeAdapter(list[Message])


@dataclass(frozen=True)
class TurnInput:
    """What the chat sends with a spoken turn, validated: the same tools, context and props as a typed run.

    `messages` is the chat's transcript, or None to go on from the bridge's own history.
    """

    tools: list[Tool] = field(default_factory=list)
    context: list[Context] = field(default_factory=list)
    forwarded_props: dict[str, Any] = field(default_factory=dict)
    messages: list[Message] | None = None


def parse_turn_input(data: object) -> TurnInput | None:
    """The browser's `voice-turn` data as a `TurnInput`, or None when it is malformed or over a bound.

    Nothing here widens what a typed turn may do: the same browser sends the same tools, context, props and
    transcript to the same agent over HTTP, and the agent checks the caller and the conversation either way.
    """
    if not isinstance(data, Mapping):
        return None
    try:
        size = len(json.dumps(data))
    except TypeError, ValueError:
        return None
    props = data.get("forwardedProps") or {}
    raw_messages = data.get("messages")
    if size > MAX_TURN_BYTES or not isinstance(props, Mapping):
        return None
    try:
        tools = _TOOLS.validate_python(data.get("tools") or [])
        context = _CONTEXT.validate_python(data.get("context") or [])
        messages = None if raw_messages is None else _MESSAGES.validate_python(raw_messages)
    except ValidationError:
        return None
    if len(tools) > MAX_TURN_TOOLS or len(context) > MAX_TURN_CONTEXT:
        return None
    if messages is not None and (
        len(messages) > MAX_TURN_MESSAGES or any(m.role not in _TRANSCRIPT_ROLES for m in messages)
    ):
        return None
    return TurnInput(tools=tools, context=context, forwarded_props=dict(props), messages=messages)


def _snapshot_tool_calls(event: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Every tool call in a MESSAGES_SNAPSHOT event, as (id, arguments); anything malformed is skipped."""
    found: list[tuple[str, str]] = []
    messages = event.get("messages")
    for message in messages if isinstance(messages, list) else []:
        tool_calls = message.get("toolCalls") if isinstance(message, Mapping) else None
        for tool_call in tool_calls if isinstance(tool_calls, list) else []:
            if not isinstance(tool_call, Mapping):
                continue
            call_id, function = tool_call.get("id"), tool_call.get("function")
            arguments = function.get("arguments") if isinstance(function, Mapping) else None
            if isinstance(call_id, str) and isinstance(arguments, str):
                found.append((call_id, arguments))
    return found


@dataclass
class _ScreenCall:
    """One call to a chat tool, as its events stream in."""

    name: str
    arguments: list[str] = field(default_factory=list)
    ended: bool = False
    sent: bool = False


class AgentBridgeProcessor(FrameProcessor):
    """Bridges STT transcripts to an upstream `/agui/{scenario_slug}` SSE endpoint.

    The upstream agent services are stateless per request, so every run carries the whole
    transcript, as `ui-react`'s `HttpAgent` does for a typed turn (see `useScenarioAgent.ts`):
    the chat's own, sent with each press, and otherwise the history this processor keeps
    for the life of the voice session.
    """

    def __init__(
        self,
        *,
        upstream_url: str,
        scenario_slug: str,
        authorization: str,
        http_client: httpx.AsyncClient,
        tts_language_switch: Mapping[str, FrameProcessor] | None = None,
        thread_id: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Bind this processor to one upstream agent endpoint and caller identity for the life of the session.

        `thread_id` is the chat's open conversation: the upstream agents serve a run only
        for a conversation the caller owns, and store the spoken turn in it next to the
        typed ones. It falls back to the scenario slug, which they refuse.

        `tts_language_switch` maps a detected STT language code (e.g. "es") to the
        already-loaded `FrameProcessor` in a downstream `ServiceSwitcher` that speaks
        that language — see `core/providers.py`'s `build_tts_service`. `None`/`{}`
        when the configured TTS provider doesn't support switching (only Piper does).
        """
        super().__init__(**kwargs)
        self._upstream_url = upstream_url
        self._scenario_slug = scenario_slug
        self._thread_id = thread_id or scenario_slug
        self._authorization = authorization
        self._client = http_client
        self._tts_language_switch = tts_language_switch or {}
        self._current_tts_language: str | None = None
        self._messages: list[Message] = []
        self._turn = TurnInput()
        self._inflight: asyncio.Task | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        """Route STT transcripts to `_run_agent`, keep the chat's turn input, forward the rest, cancel on barge-in."""
        await super().process_frame(frame, direction)

        if isinstance(frame, TranscriptionFrame) and frame.text.strip():
            await self._maybe_switch_tts_language(frame)
            if self._turn.messages is not None:
                # The chat's transcript replaces this session's own, once: a second utterance goes on from here.
                self._messages = list(self._turn.messages)
                self._turn = replace(self._turn, messages=None)
            self._messages.append(UserMessage(id=str(uuid4()), content=frame.text))
            task_manager: BaseTaskManager = self.task_manager
            self._inflight = task_manager.create_task(self._run_agent(), name=f"agent-bridge-{self._scenario_slug}")
        elif isinstance(frame, RTVIClientMessageFrame) and frame.type == TURN_MESSAGE:
            turn = parse_turn_input(frame.data)
            if turn is None:
                # Never its content: the message carries the user's transcript and screen.
                logger.warning(
                    "voice turn input refused for scenario={!r}: malformed or over a bound", self._scenario_slug
                )
            self._turn = turn or TurnInput()
            await self.push_frame(frame, direction)
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
        """Answer one spoken turn: run the agent, and again after each round of screen actions, as one response."""
        await self.push_frame(LLMFullResponseStartFrame())
        try:
            for _ in range(MAX_RUNS_PER_TURN):
                if not await self._run_once():
                    break
        except Exception:
            logger.exception("agent bridge call failed for scenario={!r}", self._scenario_slug)
        finally:
            self._inflight = None
            await self.push_frame(LLMFullResponseEndFrame())

    async def _run_once(self) -> bool:
        """POST the transcript upstream and stream the reply to TTS; True when it ended on screen actions.

        Each call to one of the chat's tools goes to the browser as soon as its arguments are
        known, and is answered in the transcript as the chat answers it, so the next run picks
        up from it. The agents do not always stream a call's arguments (with Gemini, LangGraph's
        AG-UI adapter sends a call's start and end only); the run's closing `MESSAGES_SNAPSHOT`,
        which the browser's HttpAgent also applies, has them.
        """
        payload = RunAgentInput(
            thread_id=self._thread_id,
            run_id=str(uuid4()),
            state=None,
            messages=self._messages,
            tools=self._turn.tools,
            context=self._turn.context,
            # The agent answers for the ear: a few spoken sentences, no lists or tables (ai_circus_shared.spoken).
            forwarded_props={**self._turn.forwarded_props, **spoken_props()},
        )
        screen_tools = {tool.name for tool in self._turn.tools}
        reply = ""
        calls: dict[str, _ScreenCall] = {}
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
                    kind = event.get("type")
                    if kind == "TEXT_MESSAGE_CONTENT":
                        delta = event.get("delta", "")
                        reply += delta
                        if delta:
                            await self.push_frame(LLMTextFrame(text=delta))
                    elif kind in ("TOOL_CALL_START", "TOOL_CALL_ARGS", "TOOL_CALL_END"):
                        await self._on_tool_call_event(event, screen_tools, calls)
                    elif kind == "MESSAGES_SNAPSHOT":
                        await self._on_messages_snapshot(event, calls)
                    elif kind == "RUN_ERROR":
                        logger.error(
                            "upstream agent run failed for {!r}: {}",
                            self._scenario_slug,
                            event.get("message"),
                        )
            unsent = sum(1 for call in calls.values() if call.ended and not call.sent)
            if unsent:
                logger.warning("{} screen action(s) for {!r} came without arguments", unsent, self._scenario_slug)
        finally:
            sent = [
                ToolCall(id=call_id, function=FunctionCall(name=call.name, arguments="".join(call.arguments)))
                for call_id, call in calls.items()
                if call.sent
            ]
            if reply or sent:
                self._messages.append(AssistantMessage(id=str(uuid4()), content=reply or None, tool_calls=sent or None))
            for tool_call in sent:
                self._messages.append(
                    ToolMessage(id=str(uuid4()), tool_call_id=tool_call.id, content=FRONTEND_TOOL_ANSWER)
                )
        return bool(sent)

    async def _on_tool_call_event(
        self, event: Mapping[str, Any], screen_tools: set[str], calls: dict[str, _ScreenCall]
    ) -> None:
        """Follow a call to one of the chat's tools (the agent's own tools run upstream); send it once it has ended
        with its arguments, or leave it for the run's closing snapshot to complete.
        """
        call_id = event.get("toolCallId")
        if not isinstance(call_id, str):
            return
        call = calls.get(call_id)
        if event["type"] == "TOOL_CALL_START":
            name = event.get("toolCallName")
            if call is None and name in screen_tools:
                calls[call_id] = _ScreenCall(name=name)
        elif call is None or call.ended:
            return  # not a screen action, or one already ended (a stream may repeat a call's events)
        elif event["type"] == "TOOL_CALL_ARGS":
            call.arguments.append(str(event.get("delta", "")))
        else:
            call.ended = True
            if "".join(call.arguments).strip():
                await self._send_screen_call(call_id, call)

    async def _on_messages_snapshot(self, event: Mapping[str, Any], calls: dict[str, _ScreenCall]) -> None:
        """Complete each ended screen call whose arguments were not streamed from the snapshot, and send it."""
        for call_id, arguments in _snapshot_tool_calls(event):
            call = calls.get(call_id)
            if call is None or not call.ended or call.sent or not arguments.strip():
                continue
            call.arguments = [arguments]
            await self._send_screen_call(call_id, call)

    async def _send_screen_call(self, call_id: str, call: _ScreenCall) -> None:
        """Hand one screen action to the browser, which carries it out as after a typed turn."""
        call.sent = True
        await self.push_frame(
            RTVIServerMessageFrame(
                data={
                    "type": FRONTEND_TOOL_CALL_MESSAGE,
                    "id": call_id,
                    "name": call.name,
                    "arguments": "".join(call.arguments),
                }
            )
        )
