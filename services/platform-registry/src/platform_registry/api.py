"""
- Title:    Entitlement & scenario-metadata API
- Author:   ai-circus-framework contributors

Its entitlement-mutation and /llm-settings/* routes are admin-only infrastructure,
called by other backend services or an operator, and are NOT exposed through Traefik.
The entitlement-*read* routes are different: ui-react calls GET /entitlements/{org_id}
directly from the browser (see this service's settings.yaml header) to render the
scenario picker, so `require_org_match` below gives them their own auth — the same
identity resolution every other service uses (see ai_circus_shared.auth) — rather than
trusting the caller to have validated the org_id in the URL against anything.
"""

from __future__ import annotations

from ai_circus_shared.auth import (
    AuthSettingsAdapter,
    TokenValidationError,
    is_admin_bearer_token,
    resolve_org_identity,
)
from ai_circus_shared.entitlements import ScenarioSummary
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from platform_registry import get_env_config
from platform_registry.core import llm_settings
from platform_registry.core.db import get_session
from platform_registry.core.models import Entitlement, LlmSetting, Scenario

router = APIRouter()


def require_admin(authorization: str | None = Header(default=None)) -> None:
    """Gate /llm-settings/* and entitlement mutations on the same admin bearer token
    the other services' admin-key login bypass already uses — these are shared
    gateway-wide infrastructure/mutations, not a per-tenant entitlement read.
    """
    config = get_env_config()
    if not is_admin_bearer_token(authorization, config.ADMIN_API_KEY.get_secret_value()):
        raise HTTPException(status_code=401, detail="Admin bearer token required.")


def require_org_match(org_id: str, authorization: str | None = Header(default=None)) -> None:
    """Gate GET /entitlements/{org_id}[/...] on the caller actually being that org.

    `org_id` is injected from the route's path parameter of the same name (FastAPI
    resolves path params into a `Depends()` sub-dependency by parameter name,
    regardless of which function in the dependency tree declares them — the same
    pattern prediction/assistant/rag-agent's `resolve_identity` already relies on for
    `scenario_slug`). Without this, any caller could enumerate any other tenant's
    scenario catalog by changing `org_id` in the URL — the whole point of this check.

    Deliberately uses `resolve_org_identity`, not `resolve_caller_identity`: these
    endpoints ARE the entitlement check, so calling through the latter would have
    platform-registry call back into its own API.
    """
    config = get_env_config()
    settings = AuthSettingsAdapter(
        AUTH_DISABLED=config.AUTH_DISABLED,
        DEV_ORG_ID=config.DEV_ORG_ID,
        LOGTO_ISSUER=config.LOGTO_ISSUER,
        LOGTO_API_RESOURCE_INDICATOR=config.LOGTO_API_RESOURCE_INDICATOR,
        LOGTO_JWKS_URL=config.LOGTO_JWKS_URL,
        ADMIN_API_KEY=config.ADMIN_API_KEY.get_secret_value(),
        ENGINEERING_DEMO_API_KEY=(
            config.ENGINEERING_DEMO_API_KEY.get_secret_value() if config.ENGINEERING_DEMO_API_KEY else None
        ),
        PLATFORM_REGISTRY_URL="",  # unused by resolve_org_identity — no entitlement check here
    )
    try:
        identity = resolve_org_identity(authorization=authorization, settings=settings)
    except TokenValidationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if identity.org_id != org_id:
        raise HTTPException(status_code=403, detail=f"Not authorized to read org {org_id!r}'s entitlements.")


class LlmProviderModelOut(BaseModel):
    """One of a provider's models' live routing status, per llm_settings.list_providers."""

    model_name: str
    label: str
    route_exists: bool
    model: str | None
    api_base: str | None


class LlmProviderOut(BaseModel):
    """One provider's (one API key's) live routing status — its models, per
    llm_settings.list_providers.
    """

    provider: str
    label: str
    needs_key: bool
    needs_base: bool
    env_vars: list[str]
    hint: str
    models: list[LlmProviderModelOut]


class LlmProviderTestOut(BaseModel):
    """Result of a real round-trip completion call against one provider."""

    ok: bool
    error: str | None = None
    latency_ms: float | None = None
    reply: str | None = None


class ActiveLlmModelOut(BaseModel):
    """The litellm_config.yaml model_name assistant/rag-agent should use right now."""

    model_name: str


class ActiveLlmModelIn(BaseModel):
    """Body for PUT /llm-settings/active-model."""

    model_name: str


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness check."""
    return {"status": "ok"}


@router.get("/auth/verify-engineering-demo-key")
def verify_engineering_demo_key(authorization: str | None = Header(default=None)) -> dict[str, bool]:
    """Confirms a bearer token matches ENGINEERING_DEMO_API_KEY — mirrors require_admin's
    ADMIN_API_KEY check. ui-react's login screen calls this before committing to the
    engineering-demo login, the same way it already confirms an admin key via
    /llm-settings/active-model (see apiClient.verifyAdminKey's docstring): /entitlements
    itself has no auth check, so without this a bad key would still land on the scenario
    picker and only fail once a scenario's own predict/chat call 401s.
    """
    config = get_env_config()
    demo_key = config.ENGINEERING_DEMO_API_KEY.get_secret_value() if config.ENGINEERING_DEMO_API_KEY else None
    if not demo_key or not is_admin_bearer_token(authorization, demo_key):
        raise HTTPException(status_code=401, detail="Invalid engineering demo key.")
    return {"valid": True}


@router.get("/entitlements/{org_id}", response_model=list[ScenarioSummary], dependencies=[Depends(require_org_match)])
def list_entitled_scenarios(org_id: str, session: Session = Depends(get_session)) -> list[Scenario]:
    """Return the scenarios the given tenant (Logto Organization) is entitled to."""
    stmt = select(Scenario).join(Entitlement).where(Entitlement.org_id == org_id)
    return list(session.scalars(stmt))


@router.get("/entitlements/{org_id}/{scenario_slug}")
def check_entitlement(org_id: str, scenario_slug: str, session: Session = Depends(get_session)) -> dict[str, bool]:
    """Return 200 if the tenant is entitled to the scenario, 404 otherwise.

    Deliberately NOT gated by `require_org_match`, unlike `list_entitled_scenarios`
    above: this one is called server-to-server by every other backend service's
    `resolve_caller_identity` (see `ai_circus_shared.entitlements.
    PlatformRegistryClient.check_entitlement`) *after* that service has already
    validated the end user's token itself — adding an org-match check here would
    require those internal calls to also carry a bearer token, which they don't
    (and don't need to: this route isn't reachable from a browser — see module
    docstring — only from the trusted internal docker network).
    """
    stmt = select(Entitlement).where(Entitlement.org_id == org_id, Entitlement.scenario_slug == scenario_slug)
    if session.scalars(stmt).first() is None:
        raise HTTPException(status_code=404, detail=f"Org {org_id!r} is not entitled to scenario {scenario_slug!r}.")
    return {"entitled": True}


@router.put("/entitlements/{org_id}/{scenario_slug}", status_code=204, dependencies=[Depends(require_admin)])
def grant_entitlement(org_id: str, scenario_slug: str, session: Session = Depends(get_session)) -> None:
    """Grant a tenant access to a scenario (idempotent). Mirrors a Logto role assignment."""
    if session.get(Scenario, scenario_slug) is None:
        raise HTTPException(status_code=404, detail=f"Unknown scenario {scenario_slug!r}.")

    stmt = select(Entitlement).where(Entitlement.org_id == org_id, Entitlement.scenario_slug == scenario_slug)
    if session.scalars(stmt).first() is None:
        session.add(Entitlement(org_id=org_id, scenario_slug=scenario_slug))
        session.commit()


@router.delete("/entitlements/{org_id}/{scenario_slug}", status_code=204, dependencies=[Depends(require_admin)])
def revoke_entitlement(org_id: str, scenario_slug: str, session: Session = Depends(get_session)) -> None:
    """Revoke a tenant's access to a scenario (idempotent)."""
    stmt = select(Entitlement).where(Entitlement.org_id == org_id, Entitlement.scenario_slug == scenario_slug)
    entitlement = session.scalars(stmt).first()
    if entitlement is not None:
        session.delete(entitlement)
        session.commit()


@router.get("/llm-settings/providers", response_model=list[LlmProviderOut], dependencies=[Depends(require_admin)])
def list_llm_providers() -> list[dict[str, object]]:
    """Live status of every supported LLM provider on llm-gateway — never a raw key
    (litellm itself redacts api_key in its admin API for env-substituted values; see
    core/llm_settings.py's module docstring for why there's no "save" endpoint here).
    """
    config = get_env_config()
    try:
        return llm_settings.list_providers(config.LLM_GATEWAY_URL, config.LLM_GATEWAY_API_KEY.get_secret_value())
    except llm_settings.LlmGatewayError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post(
    "/llm-settings/providers/{provider}/models/{model_name}/test",
    response_model=LlmProviderTestOut,
    dependencies=[Depends(require_admin)],
)
def test_llm_provider(provider: str, model_name: str) -> dict[str, object]:
    """Round-trip a minimal real completion through one of this provider's models —
    `model_name` picks among `ProviderSpec.models` (most providers have exactly one;
    see llm_settings.PROVIDERS["groq"] for one with more).
    """
    config = get_env_config()
    if provider not in llm_settings.PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Unknown provider {provider!r}.")
    try:
        return llm_settings.test_provider(
            config.LLM_GATEWAY_URL, config.LLM_GATEWAY_API_KEY.get_secret_value(), provider, model_name
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/llm-settings/providers/test-all",
    response_model=dict[str, dict[str, LlmProviderTestOut]],
    dependencies=[Depends(require_admin)],
)
def test_all_llm_providers() -> dict[str, dict[str, dict[str, object]]]:
    """Round-trip every model of every provider concurrently — the Settings page's
    "Test All" button. Returns `{provider: {model_name: result}}`.
    """
    config = get_env_config()
    return llm_settings.test_all_providers(config.LLM_GATEWAY_URL, config.LLM_GATEWAY_API_KEY.get_secret_value())


@router.get(
    "/llm-settings/active-model",
    response_model=ActiveLlmModelOut,
    dependencies=[Depends(require_admin)],
)
def get_active_llm_model(session: Session = Depends(get_session)) -> ActiveLlmModelOut:
    """Which model_name assistant/rag-agent should use for their very next chat request.

    Called by those services on every chat request (see ai_circus_shared.entitlements.
    PlatformRegistryClient.get_active_llm_model) — no restart needed to switch models.
    """
    setting = session.get(LlmSetting, 1)
    if setting is None:
        raise HTTPException(status_code=404, detail="No active LLM model set yet.")
    return ActiveLlmModelOut(model_name=setting.model_name)


@router.put(
    "/llm-settings/active-model",
    response_model=ActiveLlmModelOut,
    dependencies=[Depends(require_admin)],
)
def set_active_llm_model(body: ActiveLlmModelIn, session: Session = Depends(get_session)) -> ActiveLlmModelOut:
    """Switch which model assistant/rag-agent use — the Settings page's model picker.

    Only accepts a `model_name` already routed in litellm_config.yaml (i.e. one of
    llm_settings.PROVIDERS' models' model_names) — this can't add a new route, only
    choose among the ones an operator already configured there.
    """
    valid_model_names = {model.model_name for spec in llm_settings.PROVIDERS.values() for model in spec.models}
    if body.model_name not in valid_model_names:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown model_name {body.model_name!r}. Valid: {sorted(valid_model_names)}.",
        )

    setting = session.get(LlmSetting, 1)
    if setting is None:
        setting = LlmSetting(id=1, model_name=body.model_name)
        session.add(setting)
    else:
        setting.model_name = body.model_name
    session.commit()
    return ActiveLlmModelOut(model_name=setting.model_name)
