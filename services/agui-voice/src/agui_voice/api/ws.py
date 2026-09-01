"""
- Title:    Live voice WebSocket endpoint
- Author:   ai-circus-framework contributors

One Pipecat pipeline per connection: browser mic audio -> VAD -> STT ->
`AgentBridgeProcessor` (calls the existing `/agui/{scenario_slug}` agent for this
scenario's kind) -> TTS -> browser playback. Barge-in comes from Pipecat's own
turn-tracking (`PipelineWorker(enable_turn_tracking=True)`, the default) reacting to
the VAD stage — no bespoke interruption logic needed beyond what `AgentBridgeProcessor`
does to cancel its own in-flight upstream call (see core/agent_bridge.py).

Auth: browsers can't set a WebSocket `Authorization` header, so the caller's bearer
token travels as a `?token=` query parameter instead — the same pattern
`@pipecat-ai/client-js`'s own `connect({wsUrl})` step uses for non-Daily transports.
The handshake is accepted first, authenticated second, and closed with a 4401/4403/4404
policy-violation code on failure, before any pipeline is built.
"""

from __future__ import annotations

import httpx
from ai_circus_shared.auth import TokenValidationError
from ai_circus_shared.entitlements import EntitlementDeniedError, PlatformRegistryClient
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from loguru import logger
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.serializers.protobuf import ProtobufFrameSerializer
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport
from pipecat.workers.runner import WorkerRunner

from agui_voice import get_env_config
from agui_voice.core import rtvi_debug
from agui_voice.core.agent_bridge import AgentBridgeProcessor
from agui_voice.core.identity import resolve_identity_from_token
from agui_voice.core.providers import (
    build_stt_service,
    build_tts_service,
    build_vad_analyzer,
    resolve_active_providers,
)

rtvi_debug.install()  # TEMPORARY — see core/rtvi_debug.py's module docstring.

router = APIRouter()

# One consolidated instance serves every scenario kind — CLAUDE.md's "Scenario-driven,
# not per-feature code" pattern, generalized one level beyond assistant/rag-agent/form-agent.
_UPSTREAM_ENV_BY_KIND = {
    "tabular_ml": "ASSISTANT_SERVICE_URL",
    "conversational_rag": "RAG_AGENT_SERVICE_URL",
    "assisted_form": "FORM_AGENT_SERVICE_URL",
}


@router.websocket("/ws/{scenario_slug}")
async def voice_ws(websocket: WebSocket, scenario_slug: str, token: str | None = None) -> None:
    """Accept, authenticate, then run one live voice session for `scenario_slug`."""
    await websocket.accept()
    config = get_env_config()
    authorization = f"Bearer {token}" if token else None

    try:
        # Both resolve_identity_from_token (a real Keycloak token means a synchronous
        # JWKS-fetch-and-verify round trip, not just the AUTH_DISABLED/ADMIN_API_KEY
        # bypass) and list_scenarios below are plain blocking calls — unlike
        # api/tts.py's `Depends(resolve_identity)` (FastAPI runs sync *dependencies*
        # in a threadpool automatically), nothing does that for a plain call inside a
        # websocket handler, so left as-is either one stalls this process's entire
        # event loop — every other concurrent connection along with this one — for
        # however long the network round trip takes.
        identity = await run_in_threadpool(resolve_identity_from_token, scenario_slug, authorization)
    except TokenValidationError as exc:
        logger.warning("voice ws auth failed for scenario={!r}: {}", scenario_slug, exc)
        await websocket.close(code=4401, reason="unauthorized")
        return
    except EntitlementDeniedError as exc:
        logger.warning("voice ws entitlement denied for scenario={!r}: {}", scenario_slug, exc)
        await websocket.close(code=4403, reason="forbidden")
        return

    assert identity.org_id is not None  # resolve_identity_from_token() already guarantees this

    registry = PlatformRegistryClient(base_url=config.PLATFORM_REGISTRY_URL)
    # platform-registry gates this route with require_org_match — forward the
    # caller's own bearer token so it can verify the caller really is identity.org_id
    # (see PlatformRegistryClient.list_scenarios' docstring).
    scenario_list = await run_in_threadpool(
        registry.list_scenarios, org_id=identity.org_id, authorization=authorization
    )
    scenarios = {s.slug: s for s in scenario_list}
    summary = scenarios.get(scenario_slug)
    if summary is None or summary.kind not in _UPSTREAM_ENV_BY_KIND:
        logger.warning("voice ws: no servable kind for scenario={!r}", scenario_slug)
        await websocket.close(code=4404, reason="scenario not found")
        return

    upstream_base = getattr(config, _UPSTREAM_ENV_BY_KIND[summary.kind])
    upstream_url = f"{upstream_base}/agui/{scenario_slug}"

    transport = FastAPIWebsocketTransport(
        websocket,
        FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            serializer=ProtobufFrameSerializer(),
        ),
    )

    async with httpx.AsyncClient(timeout=60.0) as http_client:
        # Constructing these does real, blocking work — loading a local model into
        # memory, or (cold start) downloading its weights first — which would
        # otherwise stall this process's single event loop for the whole duration,
        # delaying the WebSocket handshake response and every other concurrent
        # connection along with it. Off the event loop explicitly, same reasoning as
        # assistant/api.py's prompt_cache.get() call.
        # The admin Settings page's live choice (persisted in platform-registry),
        # falling back to this instance's static STT_PROVIDER/TTS_PROVIDER — see
        # resolve_active_providers' docstring.
        stt_provider, tts_provider = await run_in_threadpool(resolve_active_providers, config)
        vad_analyzer = await run_in_threadpool(build_vad_analyzer)
        stt_service = await run_in_threadpool(build_stt_service, config, provider=stt_provider)
        tts_service, tts_language_switch = await run_in_threadpool(build_tts_service, config, provider=tts_provider)
        bridge = AgentBridgeProcessor(
            upstream_url=upstream_url,
            scenario_slug=scenario_slug,
            authorization=authorization or "",
            http_client=http_client,
            tts_language_switch=tts_language_switch,
        )
        pipeline = Pipeline([
            transport.input(),
            VADProcessor(vad_analyzer=vad_analyzer),
            stt_service,
            bridge,
            tts_service,
            transport.output(),
        ])
        worker = PipelineWorker(pipeline, params=PipelineParams(enable_metrics=True))

        @transport.event_handler("on_client_disconnected")
        async def _on_disconnected(_transport: FastAPIWebsocketTransport, _client: WebSocket) -> None:
            await worker.cancel()

        runner = WorkerRunner(handle_sigint=False)
        await runner.add_workers(worker)
        try:
            await runner.run()
        except WebSocketDisconnect:
            pass
        except Exception:
            logger.exception("voice pipeline crashed for scenario={!r}", scenario_slug)
