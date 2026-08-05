"""
- Title:    Entitlement & scenario-metadata API
- Author:   ai-circus-framework contributors

Not exposed through Traefik — only other backend services on the docker network call
this. Each of those services is responsible for validating the end user's Logto token
(see ai_circus_shared.auth) *before* calling here to confirm the tenant is entitled.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from platform_registry.core.db import get_session
from platform_registry.core.models import Entitlement, Scenario

router = APIRouter()


class ScenarioOut(BaseModel):
    """Scenario metadata returned to callers (mirrors ai_circus_shared.ScenarioSummary).

    The `*_service` fields let a caller build a request URL by hostname convention
    (`http://<service>.localhost`) for whichever compose instance implements this
    particular scenario — see Scenario's docstring in core/models.py.
    """

    slug: str
    kind: str
    title: str
    description: str
    icon: str
    prediction_service: str | None = None
    assistant_service: str | None = None
    agent_service: str | None = None
    feature_columns: list[str] | None = None
    feature_schema: dict[str, Any] | None = None

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
