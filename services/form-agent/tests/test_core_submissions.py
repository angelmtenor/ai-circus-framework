"""Tests for submission validation + persistence."""

from __future__ import annotations

import json

import pytest
from ai_circus_shared.scenario_schema import ChatConfig, FormConfig, FormFieldSpec, ScenarioDefinition

from form_agent.core.submissions import (
    SUBMISSIONS_BUCKET,
    case_number,
    filing_of,
    list_submissions,
    load_submission,
    submit,
    verification_code,
)


class FakeObjectStore:
    """In-memory stand-in for ai_circus_shared.storage.ObjectStore."""

    def __init__(self) -> None:
        """Initialize an empty in-memory object map keyed by (org_id, path)."""
        self.puts: list[tuple[str, str, bytes]] = []

    def put(self, tenant_org_id: str, path: str, data: bytes) -> str:
        """Record the put call instead of touching real SeaweedFS."""
        self.puts.append((tenant_org_id, path, data))
        return f"tenant-{tenant_org_id}/{path}"

    def exists(self, tenant_org_id: str, path: str) -> bool:
        """Whether an earlier put wrote this tenant's key."""
        return any(org == tenant_org_id and key == path for org, key, _ in self.puts)

    def get(self, tenant_org_id: str, path: str) -> bytes:
        """The last bytes put under this tenant's key."""
        return next(data for org, key, data in reversed(self.puts) if org == tenant_org_id and key == path)

    def list(self, tenant_org_id: str, prefix: str = "") -> list[str]:
        """This tenant's keys under a prefix."""
        return [key for org, key, _ in self.puts if org == tenant_org_id and key.startswith(prefix)]


def _filed(store: FakeObjectStore, org: str, case: str, by: str, when: str) -> None:
    payload = {"case_number": case, "submitted_by": by, "submitted_at": when, "fields": {}}
    store.put(org, f"s/{case}.json", json.dumps(payload).encode())


def test_list_submissions_is_scoped_by_tenant_and_user_newest_first() -> None:
    store = FakeObjectStore()
    _filed(store, "org-1", "S-00000001", "user-1", "2026-01-01T00:00:00+00:00")
    _filed(store, "org-1", "S-00000002", "user-1", "2026-03-01T00:00:00+00:00")
    _filed(store, "org-1", "S-00000003", "user-2", "2026-02-01T00:00:00+00:00")
    _filed(store, "org-2", "S-00000004", "user-1", "2026-04-01T00:00:00+00:00")
    store.put("org-1", "s/broken.json", b"not json")

    cases, truncated = list_submissions(store, "org-1", "s", "user-1")  # type: ignore[arg-type]

    assert [c["case_number"] for c in cases] == ["S-00000002", "S-00000001"]
    assert truncated is False


def test_list_submissions_says_when_a_cap_left_cases_out(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeObjectStore()
    for i in range(3):
        _filed(store, "org-1", f"S-0000000{i}", "user-1", f"2026-01-0{i + 1}T00:00:00+00:00")
    monkeypatch.setattr("form_agent.core.submissions.MAX_LISTED_CASES", 2)
    cases, truncated = list_submissions(store, "org-1", "s", "user-1")  # type: ignore[arg-type]
    assert len(cases) == 2 and truncated is True
    monkeypatch.setattr("form_agent.core.submissions.MAX_SCANNED_CASES", 1)
    assert list_submissions(store, "org-1", "s", "user-1")[1] is True  # type: ignore[arg-type]


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
    assert {k: payload[k] for k in ("scenario_slug", "case_number", "fields")} == {
        "scenario_slug": "service_request",
        "case_number": case,
        "fields": {"email": "jane@example.com"},
    }
    assert payload["submitted_at"] and len(payload["verification_code"]) == 14


def test_submissions_bucket_is_a_fixed_shared_name() -> None:
    """Not per-scenario config — ObjectStore already tenant-scopes keys, and submit()
    further namespaces by scenario/case, so one bucket serves every assisted_form scenario.
    """
    assert SUBMISSIONS_BUCKET == "form-agent-submissions"


def _variant_definition() -> ScenarioDefinition:
    from pathlib import Path

    return ScenarioDefinition.load(Path(__file__).parents[3] / "scenarios/sede_electronica/scenario.yaml")


def _complete_devolucion() -> dict[str, str]:
    return {
        "tramite": "devolucion",
        "nif": "12345678Z",
        "primer_apellido": "Fernández",
        "nombre": "Lucía",
        "telefono": "+34 612 345 678",
        "email": "lucia@example.com",
        "domicilio": "Calle Mayor 14",
        "codigo_postal": "41940",
        "municipio": "Villaclara",
        "provincia": "Sevilla",
        "medio_notificacion": "Medios electrónicos (sede electrónica)",
        "lugar": "Villaclara",
        "fecha_firma": "2026-09-28",
        "declaracion": "true",
        "di_referencia": "IBI-2026-0012345",
        "di_fecha_ingreso": "2026-06-30",
        "di_importe_ingresado": "412.50",
        "di_importe_solicitado": "412.50",
        "di_motivo": "Pago duplicado",
        "di_hechos": "El mismo recibo del IBI se cargó dos veces en mi cuenta.",
        "di_iban": "ES3099990001230123456789",
        "di_titular": "true",
    }


def test_submit_keeps_only_the_filed_models_boxes_and_records_who_filed_it() -> None:
    store = FakeObjectStore()
    fields = {**_complete_devolucion(), "af_importe": "999.00"}  # typed into another model's box

    case, errors = submit(store, "org-1", _variant_definition(), fields, submitted_by="user-1")

    assert errors == {}
    payload = json.loads(store.puts[0][2])
    assert "af_importe" not in payload["fields"]
    assert payload["fields"]["di_iban"] == "ES3099990001230123456789"
    assert payload["submitted_by"] == "user-1"
    assert load_submission(store, "org-1", "sede_electronica", case or "", "user-1") == payload
    assert load_submission(store, "org-1", "sede_electronica", case or "", "user-2") is None
    assert load_submission(store, "org-2", "sede_electronica", case or "", "user-1") is None
    assert load_submission(store, "org-1", "sede_electronica", "../../etc/passwd", "user-1") is None


def test_submit_rejects_a_wrong_dni_letter_in_spanish() -> None:
    case, errors = submit(
        FakeObjectStore(), "org-1", _variant_definition(), {**_complete_devolucion(), "nif": "12345678A"}
    )

    assert case is None
    assert errors == {"nif": "DNI/NIE no válido (la letra de control no coincide)."}


def test_verification_codes_are_unambiguous_and_filing_of_reads_them_back() -> None:
    code = verification_code()
    assert len(code) == 14 and code.count("-") == 2
    assert not set(code) & set("01OI")
    filing = filing_of({
        "case_number": "X-0000ABCD",
        "submitted_at": "2026-09-28T15:42:07+00:00",
        "verification_code": code,
    })
    assert filing.submitted_at.year == 2026 and filing.verification_code == code
    assert filing_of({"case_number": "X-0000ABCD"}).verification_code == "-"
