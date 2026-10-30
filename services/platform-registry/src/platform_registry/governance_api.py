"""
- Title:    /governance/* — admin audit trail endpoints
- Author:   Angel Martinez-Tenor

Admin-only. `GET /governance/audit` lists the hash-chained audit trail, `GET
/governance/audit/verify` walks the whole chain, and `POST /governance/audit` lets
data-platform-manager (which owns the gateway budgets this service knows nothing about) record
its mutations here, so there is one writer and one chain. All three answer 404 while
`GOVERNANCE_AUDIT_ENABLED` is off — the feature does not exist until an operator turns it on.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from ai_circus_shared.governance_audit import AuditRecord, AuditVerification
from ai_circus_shared.governance_events import env_flag
from ai_circus_shared.scenario_schema import load_all
from ai_circus_shared.storage import ObjectStore
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from platform_registry import get_env_config
from platform_registry.api import require_admin
from platform_registry.core import audit, governance, inventory, llm_settings
from platform_registry.core.db import get_session
from platform_registry.core.governance import ACTOR, publish_change, record_change

router = APIRouter(prefix="/governance")


def require_audit_enabled() -> None:
    """404 the audit endpoints while the feature flag is off."""
    if not governance.audit_enabled():
        raise HTTPException(status_code=404, detail="Admin audit trail is disabled (GOVERNANCE_AUDIT_ENABLED).")


class AuditPageOut(BaseModel):
    """One page of the audit trail, newest first; pass `next_before_id` as `before_id` for the next."""

    entries: list[AuditRecord]
    next_before_id: int | None = None


class ExternalAuditIn(BaseModel):
    """A mutation made in another service, recorded here. Only the actions listed are accepted."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["gateway.budget.set", "gateway.budget.deleted", "policy.invalidated", "policy.overwritten"]
    target_type: str = Field(max_length=64)
    target_id: str = Field(max_length=128)
    org_id: str | None = Field(default=None, max_length=64)
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None


@router.get(
    "/audit",
    response_model=AuditPageOut,
    dependencies=[Depends(require_admin), Depends(require_audit_enabled)],
)
def list_audit(
    limit: int = Query(default=100, ge=1, le=500),
    before_id: int | None = Query(default=None, ge=1),
    org_id: str | None = Query(default=None, max_length=64),
    session: Session = Depends(get_session),
) -> AuditPageOut:
    """The audit trail, newest first."""
    rows = audit.list_audit(session, limit=limit, before_id=before_id, org_id=org_id)
    entries = [AuditRecord.model_validate(row) for row in rows]
    return AuditPageOut(entries=entries, next_before_id=entries[-1].id if len(entries) == limit else None)


@router.get(
    "/audit/verify",
    response_model=AuditVerification,
    dependencies=[Depends(require_admin), Depends(require_audit_enabled)],
)
def verify_audit(session: Session = Depends(get_session)) -> AuditVerification:
    """Recompute the whole hash chain; `ok=false` names the first row that does not match."""
    return audit.verify_audit(session)


@router.post(
    "/audit",
    status_code=201,
    dependencies=[Depends(require_admin), Depends(require_audit_enabled)],
)
def record_external_change(body: ExternalAuditIn, session: Session = Depends(get_session)) -> dict[str, Any]:
    """Record an admin mutation another service just applied (audit row, then event)."""
    pending = record_change(
        session,
        action=body.action,
        target_type=body.target_type,
        target_id=body.target_id,
        org_id=body.org_id,
        before=body.before,
        after=body.after,
    )
    session.commit()
    publish_change(pending)
    return {"audit_id": pending.data.audit_id, "audit_hash": pending.data.audit_hash, "actor": ACTOR}


def require_inventory_enabled() -> None:
    """404 the inventory endpoint while the feature flag is off."""
    config = governance._config()
    if not env_flag(getattr(config, "GOVERNANCE_INVENTORY_ENABLED", None)):
        raise HTTPException(status_code=404, detail="AI-BOM inventory is disabled (GOVERNANCE_INVENTORY_ENABLED).")


def _open_store(bucket: str) -> ObjectStore:
    """A read-only handle on one scenario's bucket (never creates it)."""
    config = get_env_config()
    if not (config.OBJECT_STORE_ENDPOINT and config.OBJECT_STORE_ACCESS_KEY and config.OBJECT_STORE_SECRET_KEY):
        raise RuntimeError("OBJECT_STORE_ENDPOINT / _ACCESS_KEY / _SECRET_KEY are not configured")
    return ObjectStore.connect(
        bucket=bucket,
        endpoint_url=config.OBJECT_STORE_ENDPOINT,
        access_key=config.OBJECT_STORE_ACCESS_KEY,
        secret_key=config.OBJECT_STORE_SECRET_KEY.get_secret_value(),
        create_bucket=False,
    )


@router.get(
    "/inventory",
    dependencies=[Depends(require_admin), Depends(require_inventory_enabled)],
    responses={200: {"content": {inventory.MEDIA_TYPE: {}}}},
)
def get_inventory(format: Literal["cyclonedx"] = Query(default="cyclonedx")) -> Response:  # ruff: ignore[builtin-argument-shadowing] - the public query name
    """The platform's AI inventory as a CycloneDX 1.6 AI-BOM: every scenario with its declared
    governance metadata, the gateway's model routes, every trained model and each scenario's data.
    Parts that cannot be read right now are flagged `unavailable` in `metadata.properties`.
    """
    config = get_env_config()
    definitions = load_all(Path(config.SCENARIOS_DIR))
    routes = inventory.collect_routes(
        lambda: llm_settings.list_routes(config.LLM_GATEWAY_URL, config.LLM_GATEWAY_API_KEY.get_secret_value())
    )
    store_configured = bool(
        config.OBJECT_STORE_ENDPOINT and config.OBJECT_STORE_ACCESS_KEY and config.OBJECT_STORE_SECRET_KEY
    )
    trained: list[inventory.TrainedModel] | None = None
    complete = False
    if store_configured:
        trained, complete = inventory.collect_trained_models(definitions, _open_store)
    bom = inventory.build_bom(definitions, routes=routes, trained=trained, trained_complete=complete)
    return Response(content=json.dumps(bom, indent=2), media_type=inventory.MEDIA_TYPE)
