"""Tests for form-filling system prompt construction — verifies it stays entirely
data-driven from `definition.form`/`definition.chat.context`, with no scenario-specific
wording hardcoded in the function itself.
"""

from __future__ import annotations

from ai_circus_shared.scenario_schema import (
    ChatConfig,
    DocumentChunking,
    DocumentEmbedding,
    DocumentsConfig,
    FormConfig,
    FormFieldSpec,
    RequiredIf,
    ScenarioDefinition,
    VectorStoreConfig,
)

from form_agent.core.prompt import build_form_system_prompt

CHUNKING = DocumentChunking(strategy="recursive_character", chunk_size=800, chunk_overlap=120)
EMBEDDING = DocumentEmbedding(model="sentence-transformers/all-MiniLM-L6-v2")


def _definition(form: FormConfig, *, with_documents: bool = False) -> ScenarioDefinition:
    documents = (
        DocumentsConfig(
            bucket="b", raw_prefix="raw/", seed_prefix="sample_docs", chunking=CHUNKING, embedding=EMBEDDING
        )
        if with_documents
        else None
    )
    vector_store = (
        VectorStoreConfig(backend="qdrant", collection_prefix="service_request", top_k=3) if with_documents else None
    )
    return ScenarioDefinition(
        slug="service_request",
        kind="assisted_form",
        title="Public Service Request Portal",
        description="d",
        role_required="scenario:service_request",
        icon="🏛️",
        chat=ChatConfig(context="A generic local-government service desk."),
        form=form,
        documents=documents,
        vector_store=vector_store,
        services={"etl": "etl-vectorize", "agent": "form-agent"},
    )


def test_prompt_describes_every_field_with_its_requirement() -> None:
    """Each field's id/label/type/requirement shows up, generically — no field concept
    is hardcoded (min_length/pattern fields render the same way as any other).
    """
    form = FormConfig(
        title="Public Service Request",
        fields=[
            FormFieldSpec(id="full_name", label="Full name", type="text", required=True),
            FormFieldSpec(
                id="address",
                label="Address",
                type="text",
                required_if=RequiredIf(field="request_type", in_values=["streetlight_outage"]),
            ),
        ],
    )

    prompt = build_form_system_prompt(_definition(form))

    assert "id='full_name'" in prompt
    assert "always required" in prompt
    assert "required only if 'request_type' is one of ['streetlight_outage']" in prompt
    assert "update_form_fields" in prompt


def test_prompt_includes_classification_instructions_only_when_configured() -> None:
    """A plain slot-filling form (no classification_field) gets no retrieve_catalog mention."""
    plain_form = FormConfig(title="Contact form", fields=[FormFieldSpec(id="email", label="Email", type="email")])

    prompt = build_form_system_prompt(_definition(plain_form))

    assert "retrieve_catalog" not in prompt


def test_prompt_includes_classification_instructions_when_configured() -> None:
    """A classification-driven form tells the model to call retrieve_catalog and lists the options."""
    form = FormConfig(
        title="Public Service Request",
        fields=[
            FormFieldSpec(id="request_type", label="Request type", type="select", options=["a", "b"], required=True)
        ],
        classification_field="request_type",
        classification_options=["a", "b"],
    )

    prompt = build_form_system_prompt(_definition(form, with_documents=True))

    assert "retrieve_catalog" in prompt
    assert "['a', 'b']" in prompt


def test_prompt_grounds_in_chat_context_and_form_title() -> None:
    """The domain framing comes entirely from chat.context/form.title, not hardcoded wording."""
    form = FormConfig(title="Custom Portal Title", fields=[FormFieldSpec(id="email", label="Email", type="email")])

    prompt = build_form_system_prompt(_definition(form))

    assert "Custom Portal Title" in prompt
    assert "A generic local-government service desk." in prompt
