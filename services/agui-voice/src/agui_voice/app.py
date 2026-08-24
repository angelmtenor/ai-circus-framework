"""
app.py
------

Entry point for agui-voice: a long-running FastAPI service hosting a Pipecat
real-time voice pipeline (`POST /ws/{scenario_slug}`, one per active call) and a
one-shot `POST /tts/{scenario_slug}` endpoint, serving every scenario kind — see
`api/ws.py`'s module docstring for why it calls the existing assistant/rag-agent/
form-agent `/agui/{scenario_slug}` endpoint as its "LLM stage" instead of running an
LLM itself.

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from ai_circus_shared.deployment_guard import enforce_safe_for_public_deployment
from ai_circus_shared.observability import configure_metrics
from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from agui_voice import get_env_config
from agui_voice.api.providers import router as providers_router
from agui_voice.api.tts import router as tts_router
from agui_voice.api.ws import router as ws_router
from agui_voice.core.logger import configure_logger, get_logger
from agui_voice.core.providers import build_stt_service, build_tts_service, build_vad_analyzer

logger = get_logger(__name__)


async def _prewarm_models() -> None:
    """Load the STT/TTS model weights once, right after boot, instead of on the
    first real caller's connection. Model loading is real, multi-second CPU-bound
    work cached process-wide once done (see providers.py's module docstring) — this
    just moves *when* that one-time cost is paid from "whoever connects first" to
    "container startup," which is otherwise idle time anyway. Best-effort: a bad
    provider config (e.g. a misconfigured cloud API key) logs and gives up here
    rather than crashing the app — the first real connection will surface the same
    error properly through its own request/response cycle.
    """
    try:
        config = get_env_config()
        await run_in_threadpool(build_vad_analyzer)
        await run_in_threadpool(build_stt_service, config)
        await run_in_threadpool(build_tts_service, config)
        logger.success("agui-voice: STT/TTS models pre-warmed")
    except Exception:
        logger.exception("agui-voice: pre-warming STT/TTS models failed — the first real connection will load them")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Kick off model pre-warming in the background so `/healthz` responds immediately
    rather than the app only becoming "up" once warming finishes.
    """
    asyncio.create_task(_prewarm_models())  # ruff: ignore[asyncio-dangling-task] (fire-and-forget by design)
    yield


# FastAPI/Swagger UI docs are only useful for local iteration — staging/production
# traffic goes through Traefik and shouldn't expose an interactive API explorer.
_DOCS_ENABLED = os.getenv("APP_ENVIRONMENT", "local") in {"local", "docker"}

app = FastAPI(
    title="agui-voice",
    lifespan=lifespan,
    docs_url="/docs" if _DOCS_ENABLED else None,
    redoc_url="/redoc" if _DOCS_ENABLED else None,
    openapi_url="/openapi.json" if _DOCS_ENABLED else None,
)
app.include_router(ws_router)
app.include_router(tts_router)
app.include_router(providers_router)
configure_metrics(app)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness check."""
    return {"status": "ok"}


def main() -> None:
    """Validate configuration, then run the FastAPI app under uvicorn."""
    configure_logger()

    try:
        config = get_env_config()
    except ValidationError as e:
        logger.error("Configuration error: Mandatory environment variable(s) missing or invalid:")
        for error in e.errors():
            logger.error("  {}: {}", " -> ".join(str(loc) for loc in error["loc"]), error["msg"])
        sys.exit(1)

    try:
        enforce_safe_for_public_deployment(
            admin_api_key=config.ADMIN_API_KEY.get_secret_value(),
            engineering_demo_api_key=(
                config.ENGINEERING_DEMO_API_KEY.get_secret_value() if config.ENGINEERING_DEMO_API_KEY else None
            ),
            auth_disabled=config.AUTH_DISABLED,
        )
    except RuntimeError as e:
        logger.error(str(e))
        sys.exit(1)

    # ui-react calls this API directly from the browser — never via cookies, always a
    # Bearer token (or, for the WebSocket, a ?token= query param — see api/ws.py), so
    # no CSRF risk — still scoped to CORS_ALLOWED_ORIGINS rather than "*".
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[origin.strip() for origin in config.CORS_ALLOWED_ORIGINS.split(",") if origin.strip()],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    logger.success("agui-voice starting on port {}", config.HTTP_PORT)
    uvicorn.run(app, host="0.0.0.0", port=int(config.HTTP_PORT), log_level=config.LOG_LEVEL.lower())  # ruff: ignore[hardcoded-bind-all-interfaces]


if __name__ == "__main__":
    main()
