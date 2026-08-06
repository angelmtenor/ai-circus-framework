"""
- Title:    Entitlement & scenario-metadata API
- Author:   ai-circus-framework contributors

Not exposed through Traefik — only other backend services on the docker network call
this. Each of those services is responsible for validating the end user's Logto token
(see ai_circus_shared.auth) *before* calling here to confirm the tenant is entitled.
"""

from __future__ import annotations

from typing import Any

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
    """Gate /llm-settings/* on the same admin bearer token the other services'
    admin-key login bypass already uses — these settings are shared gateway-wide
    infrastructure, not a per-tenant entitlement.
    """
    config = get_env_config()
    expected = f"Bearer {config.ADMIN_API_KEY.get_secret_value()}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="Admin bearer token required.")


class LlmProviderOut(BaseModel):
    """One provider's live routing status, per llm_settings.list_providers."""

    provider: str
    label: str
    model_name: str
    route_exists: bool
    model: str | None
    api_base: str | None
    needs_key: bool
    needs_base: bool
    env_vars: list[str]
    hint: str


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


class ScenarioOut(BaseModel):
    """Scenario metadata returned to callers (mirrors ai_circus_shared.ScenarioSummary)."""

    slug: str
    kind: str
    title: str
    description: str
    icon: str
    feature_columns: list[str] | None = None
    feature_schema: dict[str, Any] | None = None
    sample_questions: list[str] = []
    task_type: str | None = None
    target_units: str | None = None

    model_config = {"from_attributes": True}


@router.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness check."""
    return {"status": "ok"}


@router.get("/entitlements/{org_id}", response_model=list[ScenarioOut])
def list_entitled_scenarios(org_id: str, session: Session = Depends(get_session)) -> list[Scenario]:
    """Return the scenarios the given tenant (Logto Organization) is entitled to."""
    stmt = select(Scenario).join(Entitlement).where(Entitlement.org_id == org_id)
    return list(session.scalars(stmt))


@router.get("/entitlements/{org_id}/{scenario_slug}")
def check_entitlement(org_id: str, scenario_slug: str, session: Session = Depends(get_session)) -> dict[str, bool]:
    """Return 200 if the tenant is entitled to the scenario, 404 otherwise."""
    stmt = select(Entitlement).where(Entitlement.org_id == org_id, Entitlement.scenario_slug == scenario_slug)
    if session.scalars(stmt).first() is None:
        raise HTTPException(status_code=404, detail=f"Org {org_id!r} is not entitled to scenario {scenario_slug!r}.")
    return {"entitled": True}


@router.put("/entitlements/{org_id}/{scenario_slug}", status_code=204)
def grant_entitlement(org_id: str, scenario_slug: str, session: Session = Depends(get_session)) -> None:
    """Grant a tenant access to a scenario (idempotent). Mirrors a Logto role assignment."""
    if session.get(Scenario, scenario_slug) is None:
        raise HTTPException(status_code=404, detail=f"Unknown scenario {scenario_slug!r}.")

    stmt = select(Entitlement).where(Entitlement.org_id == org_id, Entitlement.scenario_slug == scenario_slug)
    if session.scalars(stmt).first() is None:
        session.add(Entitlement(org_id=org_id, scenario_slug=scenario_slug))
        session.commit()


@router.delete("/entitlements/{org_id}/{scenario_slug}", status_code=204)
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
    "/llm-settings/providers/{provider}/test",
    response_model=LlmProviderTestOut,
    dependencies=[Depends(require_admin)],
)
def test_llm_provider(provider: str) -> dict[str, object]:
    """Round-trip a minimal real completion through this provider's configured model."""
    config = get_env_config()
    if provider not in llm_settings.PROVIDERS:
        raise HTTPException(status_code=404, detail=f"Unknown provider {provider!r}.")
    return llm_settings.test_provider(config.LLM_GATEWAY_URL, config.LLM_GATEWAY_API_KEY.get_secret_value(), provider)


@router.post(
    "/llm-settings/providers/test-all",
    response_model=dict[str, LlmProviderTestOut],
    dependencies=[Depends(require_admin)],
)
def test_all_llm_providers() -> dict[str, dict[str, object]]:
    """Round-trip every provider concurrently — the Settings page's "Test All" button."""
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
    llm_settings.PROVIDERS' model_names) — this can't add a new route, only choose
    among the ones an operator already configured there.
    """
    valid_model_names = {spec.model_name for spec in llm_settings.PROVIDERS.values()}
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
