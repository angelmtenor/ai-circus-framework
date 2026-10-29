"""
- Title:    Regenerate the governance JSON Schemas under docs/governance/schemas/
- Author:   Angel Martinez-Tenor

The schemas are the contracts external governance tools (and llm-gateway, which cannot
import ai_circus_shared) integrate against; they are generated from the pydantic models in
libs/shared, and a libs/shared test fails if a committed file drifts from its model.

Run from libs/shared:  uv run python ../../scripts/export_governance_schemas.py
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from ai_circus_shared.governance import GovernanceConfig
from ai_circus_shared.governance_audit import AuditRecord, AuditVerification
from ai_circus_shared.governance_events import EVENT_DATA_MODELS, CloudEventEnvelope

SCHEMAS_DIR = Path(__file__).resolve().parents[1] / "docs" / "governance" / "schemas"


def schema_files() -> dict[str, type[BaseModel]]:
    """Published file name -> the model it is generated from."""
    files: dict[str, type[BaseModel]] = {
        "ai-system-governance.json": GovernanceConfig,
        "cloudevent-envelope.json": CloudEventEnvelope,
        "audit-record.json": AuditRecord,
        "audit-verification.json": AuditVerification,
    }
    for name, model in EVENT_DATA_MODELS.items():
        files[f"event-{name.replace('.', '-')}.v1.json"] = model
    return files


def render(model: type[BaseModel]) -> str:
    return json.dumps(model.model_json_schema(), indent=2, sort_keys=True) + "\n"


def main() -> None:
    SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)
    for file_name, model in schema_files().items():
        (SCHEMAS_DIR / file_name).write_text(render(model), encoding="utf-8")
        print("wrote", file_name)


if __name__ == "__main__":
    main()
