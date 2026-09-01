"""Tests for submission validation + persistence."""

from __future__ import annotations

import json

from ai_circus_shared.scenario_schema import ChatConfig, FormConfig, FormFieldSpec, ScenarioDefinition

from form_agent.core.submissions import SUBMISSIONS_BUCKET, case_number, submit


class FakeObjectStore:
    """In-memory stand-in for ai_circus_shared.storage.ObjectStore."""

    def __init__(self) -> None:
        """Initialize an empty in-memory object map keyed by (org_id, path)."""
        self.puts: list[tuple[str, str, bytes]] = []

    def put(self, tenant_org_id: str, path: str, data: bytes) -> str:
        """Record the put call instead of touching real SeaweedFS."""
        self.puts.append((tenant_org_id, path, data))
        return f"tenant-{tenant_org_id}/{path}"


def _definition(form: FormConfig) -> ScenarioDefinition:
    return ScenarioDefinition(
        slug="service_request",
        kind="assisted_form",
        title="Public Service Request Portal",
        description="d",
        role_required="scenario:service_request",
        icon="🏛️",
        industry="public_sector",
        chat=ChatConfig(context="A generic local-government service desk."),
        form=form,
        services={"etl": "etl-vectorize", "agent": "form-agent"},
    )


def test_case_number_is_slug_prefixed_and_uppercase() -> None:
    case = case_number("service_request")

    assert case.startswith("SERVICE_REQUEST-")
    assert case == case.upper()


def test_submit_rejects_an_invalid_submission_without_persisting() -> None:
    form = FormConfig(title="t", fields=[FormFieldSpec(id="email", label="Email", type="email", required=True)])
    store = FakeObjectStore()

    case, errors = submit(store, "org-1", _definition(form), {})

    assert case is None
    assert errors == {"email": "This field is required."}
    assert store.puts == []


def test_submit_persists_a_valid_submission_and_returns_a_case_number() -> None:
    form = FormConfig(title="t", fields=[FormFieldSpec(id="email", label="Email", type="email", required=True)])
    store = FakeObjectStore()

    case, errors = submit(store, "org-1", _definition(form), {"email": "jane@example.com"})

    assert errors == {}
    assert case is not None
    assert len(store.puts) == 1
    org_id, path, data = store.puts[0]
    assert org_id == "org-1"
    assert path == f"service_request/{case}.json"
    payload = json.loads(data)
    assert payload == {"scenario_slug": "service_request", "case_number": case, "fields": {"email": "jane@example.com"}}


def test_submissions_bucket_is_a_fixed_shared_name() -> None:
    """Not per-scenario config — ObjectStore already tenant-scopes keys, and submit()
    further namespaces by scenario/case, so one bucket serves every assisted_form scenario.
    """
    assert SUBMISSIONS_BUCKET == "form-agent-submissions"
