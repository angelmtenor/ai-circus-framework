"""Tests for the case desk's LLM extraction of a scanned application (core/extraction.py)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from ai_circus_shared.auth import Identity
from ai_circus_shared.scenario_schema import ScenarioDefinition
from fastapi import FastAPI
from fastapi.testclient import TestClient

from assistant.api import _chat_llm, _llm_model, _scenario_definition, router
from assistant.core.extraction import build_extraction_prompt, parse_extraction
from assistant.core.identity import resolve_identity

REPO = Path(__file__).resolve().parents[3]
AID = ScenarioDefinition.load(REPO / "scenarios/prestaciones_sociales/scenario.yaml")
assert AID.dataset is not None and AID.ui_extras is not None
SKIP = {"RentaPerCapitaIPREM"}
OCR = (
    "SOLICITUD DE AYUDA ECONOMICA DE EMERGENCIA SOCIAL\n"
    "Edad: 34   Meses de empadronamiento: 60\n"
    "Importe solicitado: 1.250,50 EUR\n"
    "Documento de identidad (DNI/NIE): No aportado\n"
    "Exposicion: Estoy sola con mis dos hijos y nos cortan la luz.\n"
)


def _answer(**fields: Any) -> str:
    base = {
        "EdadSolicitante": {"value": "34", "evidence": "Edad: 34", "confidence": 0.95},
        "ImporteSolicitado": {"value": "1.250,50 €", "evidence": "Importe solicitado: 1.250,50 EUR", "confidence": 0.9},
        "AportaDNI": {"value": "no", "evidence": "No aportado", "confidence": 0.8},
        "ExposicionMotivos": {
            "value": "Estoy sola con mis dos hijos y nos cortan la luz.",
            "evidence": "Estoy sola con mis dos hijos",
            "confidence": 0.9,
        },
    }
    return "Claro:\n```json\n" + json.dumps({"fields": {**base, **fields}}) + "\n```"


def test_prompt_lists_the_boxes_but_not_the_computed_ones() -> None:
    prompt = build_extraction_prompt(AID.dataset, SKIP, AID.ui_extras.form_title)  # type: ignore[union-attr]
    assert '"AportaDNI"' in prompt and '"ExposicionMotivos"' in prompt
    assert '"RentaPerCapitaIPREM"' not in prompt
    assert "Never guess" in prompt and "exact words" in prompt


def test_values_are_fitted_to_their_boxes() -> None:
    result = parse_extraction(_answer(), AID.dataset, SKIP, OCR)
    fields = result["fields"]
    assert fields["EdadSolicitante"]["value"] == 34
    assert fields["ImporteSolicitado"]["value"] == pytest.approx(1250.5)  # Spanish format, symbol stripped
    assert fields["AportaDNI"]["value"] == "No"  # normalised to the option's spelling
    assert all(f["verified"] for f in fields.values())
    assert "MesesEmpadronamiento" in result["missing"]  # in the text, but the model didn't give it


def test_unusable_values_are_rejected_not_trusted() -> None:
    answer = _answer(
        Distrito={"value": "Atlantis", "evidence": "x", "confidence": 1},
        MiembrosUnidad={"value": 99, "evidence": "x", "confidence": 1},
        MenoresACargo={"value": "dos", "evidence": "x", "confidence": 1},
        NotABox={"value": 1, "evidence": "x", "confidence": 1},
        RentaPerCapitaIPREM={"value": 0.3, "evidence": "x", "confidence": 1},
    )
    result = parse_extraction(answer, AID.dataset, SKIP, OCR)
    assert set(result["rejected"]) == {"Distrito", "MiembrosUnidad", "MenoresACargo"}
    assert "NotABox" not in result["fields"] and "RentaPerCapitaIPREM" not in result["fields"]


def test_a_quote_the_scan_does_not_contain_is_unverified_and_loses_confidence() -> None:
    answer = _answer(MesesEmpadronamiento={"value": 120, "evidence": "Vive aquí desde 2015", "confidence": 0.99})
    field = parse_extraction(answer, AID.dataset, SKIP, OCR)["fields"]["MesesEmpadronamiento"]
    assert (field["verified"], field["confidence"]) == (False, 0.5)


def test_text_boxes_are_cut_at_their_max_length() -> None:
    long = "palabra " * 200
    answer = _answer(DestinoAyuda={"value": long, "evidence": "Estoy sola", "confidence": 1})
    value = parse_extraction(answer, AID.dataset, SKIP, OCR)["fields"]["DestinoAyuda"]["value"]
    assert len(value) <= 300


@pytest.mark.parametrize("content", ["no json at all", "[1, 2]", '{"other": 1}'])
def test_a_reply_that_is_not_the_expected_json_raises(content: str) -> None:
    with pytest.raises(ValueError):
        parse_extraction(content, AID.dataset, SKIP, OCR)


class FakeLlm:
    """Stand-in ChatOpenAI: records the prompt and replies with a canned answer."""

    model_kwargs: ClassVar[dict[str, Any]] = {}
    extra_body: dict[str, Any] | None = None

    def __init__(self, reply: str) -> None:
        """Remember the canned reply; no prompt seen yet."""
        self.reply = reply
        self.messages: list[Any] = []

    def model_copy(self, update: dict[str, Any]) -> FakeLlm:
        """ChatOpenAI.model_copy: the per-request copy is the same stand-in."""
        return self

    def invoke(self, messages: list[Any]) -> SimpleNamespace:
        """Record the prompt and return the canned reply."""
        self.messages = messages
        return SimpleNamespace(content=self.reply)


def _client(definition: ScenarioDefinition, llm: FakeLlm) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[resolve_identity] = lambda: Identity(subject="u", org_id="org-1", roles=frozenset())
    app.dependency_overrides[_scenario_definition] = lambda: definition
    app.dependency_overrides[_chat_llm] = lambda: llm
    app.dependency_overrides[_llm_model] = lambda: "test-model"
    return TestClient(app)


def test_extract_record_endpoint_returns_validated_boxes() -> None:
    llm = FakeLlm(_answer())
    response = _client(AID, llm).post("/extract-record/prestaciones_sociales", json={"text": OCR})

    assert response.status_code == 200
    body = response.json()
    assert body["model"] == "test-model"
    assert body["fields"]["AportaDNI"]["value"] == "No"
    assert OCR in llm.messages[1].content  # the OCR text reaches the model as data


def test_extract_record_is_404_without_a_case_desk_and_422_on_oversized_text() -> None:
    titanic = ScenarioDefinition.load(REPO / "scenarios/titanic/scenario.yaml")
    assert _client(titanic, FakeLlm("{}")).post("/extract-record/titanic", json={"text": OCR}).status_code == 404
    too_long = {"text": "x" * 20001}
    assert _client(AID, FakeLlm("{}")).post("/extract-record/prestaciones_sociales", json=too_long).status_code == 422


def test_extract_record_is_502_when_the_model_answers_garbage() -> None:
    response = _client(AID, FakeLlm("sorry, no")).post("/extract-record/prestaciones_sociales", json={"text": OCR})
    assert response.status_code == 502


def test_intake_samples_only_serve_the_listed_files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    folder = tmp_path / "prestaciones_sociales" / "sample_uploads"
    folder.mkdir(parents=True)
    listed = AID.ui_extras.sample_uploads[0].file  # type: ignore[union-attr]
    (folder / listed).write_bytes(b"%PDF-1.4 fake")
    (folder / "secret.pdf").write_bytes(b"nope")
    monkeypatch.setattr("assistant.api.get_env_config", lambda: SimpleNamespace(SCENARIOS_DIR=str(tmp_path)))
    client = _client(AID, FakeLlm("{}"))

    assert client.get(f"/intake-samples/prestaciones_sociales/{listed}").content == b"%PDF-1.4 fake"
    assert client.get("/intake-samples/prestaciones_sociales/secret.pdf").status_code == 404
    assert client.get("/intake-samples/prestaciones_sociales/..%2F..%2Fx.pdf").status_code == 404
