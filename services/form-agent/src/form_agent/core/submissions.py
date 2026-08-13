"""
- Title:    Submission validation + persistence
- Author:   ai-circus-framework contributors

Persists as one JSON object per submission via the tenant-scoped MinIO client
(`ai_circus_shared.storage`) — the same mechanism etl/training/prediction already use
for artifacts, avoiding a new Postgres schema/migration for this demo feature.
"""

from __future__ import annotations

import json
import uuid

from ai_circus_shared.form_validation import validate_submission
from ai_circus_shared.scenario_schema import ScenarioDefinition
from ai_circus_shared.storage import ObjectStore

# One shared bucket for every assisted_form scenario's submissions — ObjectStore
# already tenant-scopes every key by org_id (see storage.py), and `submit()` below
# further namespaces by scenario/case, so a single bucket needs no per-scenario config.
SUBMISSIONS_BUCKET = "form-agent-submissions"


def case_number(scenario_slug: str) -> str:
    """A short, human-shareable case number: SLUG-XXXXXXXX."""
    return f"{scenario_slug.upper()}-{uuid.uuid4().hex[:8].upper()}"


def submit(
    store: ObjectStore,
    org_id: str,
    definition: ScenarioDefinition,
    fields: dict[str, str],
) -> tuple[str | None, dict[str, str]]:
    """Validate a submission against `definition.form` and, if valid, persist it.

    Returns `(case_number, errors)` — `case_number` is `None` whenever `errors` is
    non-empty, and vice versa.
    """
    assert definition.form is not None  # guaranteed by kind="assisted_form" filter
    errors = validate_submission(definition.form, fields)
    if errors:
        return None, errors

    case = case_number(definition.slug)
    payload = {"scenario_slug": definition.slug, "case_number": case, "fields": fields}
    store.put(org_id, f"{definition.slug}/{case}.json", json.dumps(payload, indent=2).encode("utf-8"))
    return case, {}
