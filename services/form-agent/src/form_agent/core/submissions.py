"""
- Title:    Submission validation + persistence
- Author:   Angel Martinez-Tenor

Persists as one JSON object per submission via the tenant-scoped SeaweedFS client
(`ai_circus_shared.storage`) — the same mechanism etl/training/prediction already use
for artifacts, avoiding a new Postgres schema/migration for this demo feature.

Besides the values, a submission records what its filed PDF prints (see core/pdf.py):
the registry timestamp and a verification code, fixed at submission time so the PDF
re-renders identically on every download — and who submitted it, so only that same
user (within the same tenant) can download it again.
"""

from __future__ import annotations

import json
import re
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from ai_circus_shared.form_validation import validate_submission
from ai_circus_shared.scenario_schema import ScenarioDefinition
from ai_circus_shared.storage import ObjectStore

from form_agent.core.pdf import Filing

# One shared bucket for every assisted_form scenario's submissions — ObjectStore
# already tenant-scopes every key by org_id (see storage.py), and `submit()` below
# further namespaces by scenario/case, so a single bucket needs no per-scenario config.
SUBMISSIONS_BUCKET = "form-agent-submissions"

CASE_NUMBER_RE = re.compile(r"^[A-Z0-9_]{1,80}-[0-9A-F]{8}$")
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I — read aloud, typed back


def case_number(scenario_slug: str) -> str:
    """A short, human-shareable case number: SLUG-XXXXXXXX."""
    return f"{scenario_slug.upper()}-{uuid.uuid4().hex[:8].upper()}"


def verification_code() -> str:
    """A random, unambiguous 12-character code printed as XXXX-XXXX-XXXX."""
    raw = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def submit(
    store: ObjectStore,
    org_id: str,
    definition: ScenarioDefinition,
    fields: dict[str, str],
    submitted_by: str | None = None,
) -> tuple[str | None, dict[str, str]]:
    """Validate a submission against `definition.form` and, if valid, persist it.

    Only the fields of the model being filed are kept (a value typed into another
    model's box before switching models is not part of this submission).

    Returns `(case_number, errors)` — `case_number` is `None` whenever `errors` is
    non-empty, and vice versa.
    """
    assert definition.form is not None  # guaranteed by kind="assisted_form" filter
    errors = validate_submission(definition.form, fields)
    if errors:
        return None, errors

    active = {spec.id for spec in definition.form.active_fields(fields)}
    kept = {key: value for key, value in fields.items() if key in active}
    case = case_number(definition.slug)
    payload = {
        "scenario_slug": definition.slug,
        "case_number": case,
        "fields": kept,
        "submitted_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "verification_code": verification_code(),
        "submitted_by": submitted_by,
    }
    store.put(org_id, f"{definition.slug}/{case}.json", json.dumps(payload, indent=2).encode("utf-8"))
    return case, {}


def load_submission(
    store: ObjectStore, org_id: str, scenario_slug: str, case: str, submitted_by: str | None
) -> dict[str, Any] | None:
    """A persisted submission of this tenant, or None if unknown/malformed or filed by
    someone else — callers answer 404 either way, so a case number can't be probed.
    """
    if not CASE_NUMBER_RE.match(case) or not store.exists(org_id, f"{scenario_slug}/{case}.json"):
        return None
    payload = json.loads(store.get(org_id, f"{scenario_slug}/{case}.json"))
    if payload.get("submitted_by") != submitted_by:
        return None
    return payload


def filing_of(payload: dict[str, Any]) -> Filing:
    """The registry data a filed PDF prints, from a persisted submission. Older
    submissions (before timestamps were recorded) get a stable placeholder.
    """
    submitted_at = datetime.fromisoformat(payload["submitted_at"]) if payload.get("submitted_at") else datetime.now(UTC)
    return Filing(
        case_number=payload["case_number"],
        submitted_at=submitted_at,
        verification_code=payload.get("verification_code") or "-",
    )
