"""
app.py
------

Entry point for crm-mcp: an MCP server built with mcp-kit in front of crm-api (docs/adr/0022). The kit's
guarded endpoint is mounted at `/mcp/`, next to `/healthz` and the platform's observability contract
(`/metrics`, OTLP traces when an endpoint is set, JSON logs).

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import sys
from pathlib import Path

import uvicorn
from ai_circus_shared.observability import configure_metrics
from ai_circus_shared.telemetry import configure_tracing
from fastapi import FastAPI
from mcp_kit import ExchangeSettings, McpKit, Policy, TokenExchanger, TokenVerifier, configure_audit_output
from pydantic import ValidationError

from crm_mcp import get_env_config
from crm_mcp.core.logger import configure_logger, get_logger
from crm_mcp.data_model import EnvConfig
from crm_mcp.server import INSTRUCTIONS, register_tools

logger = get_logger(__name__)


def create_app(kit: McpKit) -> FastAPI:
    """`/healthz`, `/metrics` and the kit's guarded MCP endpoint at `/mcp/`."""
    guard, lifespan = kit.endpoint()
    app = FastAPI(title="crm-mcp", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    configure_metrics(app)
    configure_tracing(app, service="crm-mcp")
    app.mount("/mcp", guard)
    return app


def build_kit(config: EnvConfig) -> McpKit:
    """The kit, wired from the service's configuration."""
    # Relative to the working directory: the service directory, locally and in the image (WORKDIR).
    policy_path = Path(config.POLICY_FILE)
    kit = McpKit(
        name="crm-mcp",
        instructions=INSTRUCTIONS,
        verifier=TokenVerifier.from_jwks(
            issuer=config.KEYCLOAK_ISSUER,
            audience=config.MCP_CLIENT_ID,
            jwks_url=config.KEYCLOAK_JWKS_URL,
        ),
        policy=Policy.from_file(policy_path),
        exchanger=TokenExchanger(
            ExchangeSettings(
                token_url=config.KEYCLOAK_TOKEN_URL,
                client_id=config.MCP_CLIENT_ID,
                client_secret=config.MCP_CLIENT_SECRET.get_secret_value(),
                api_audience=config.API_AUDIENCE,
            )
        ),
        api_base_url=config.API_BASE_URL,
    )
    register_tools(kit)
    return kit


def main() -> None:
    """Validate configuration, then serve the MCP server under uvicorn."""
    configure_logger()
    try:
        config = get_env_config()
    except ValidationError as e:
        logger.error("Configuration error: Mandatory environment variable(s) missing or invalid:")
        for error in e.errors():
            logger.error("  {}: {}", " -> ".join(str(loc) for loc in error["loc"]), error["msg"])
        sys.exit(1)

    configure_audit_output()  # one JSON line per tool call and per refused caller, on stdout
    app = create_app(build_kit(config))
    logger.success("crm-mcp starting on port {} (API {})", config.HTTP_PORT, config.API_BASE_URL)
    uvicorn.run(app, host="0.0.0.0", port=int(config.HTTP_PORT), log_level=config.LOG_LEVEL.lower())  # ruff: ignore[hardcoded-bind-all-interfaces]


if __name__ == "__main__":
    main()
