"""Tests for ai_circus_shared.scenario_schema's DocumentsConfig seed-source validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_circus_shared.scenario_schema import (
    CategoricalFeatureUI,
    ChatConfig,
    DocumentChunking,
    DocumentEmbedding,
    DocumentsConfig,
    GithubDocsSource,
    MapRegion,
    RegionMapExtra,
    ScenarioDefinition,
    TabularDataset,
    TabularModel,
    TabularServices,
    VectorStoreConfig,
    qdrant_collection_name,
)

CHUNKING = DocumentChunking(strategy="recursive_character", chunk_size=800, chunk_overlap=120)
EMBEDDING = DocumentEmbedding(model="sentence-transformers/all-MiniLM-L6-v2")

SERVICES = TabularServices(etl="etl-tabular", training="training", prediction="prediction", assistant="assistant")


def _region_map_scenario(ui_extras: RegionMapExtra) -> ScenarioDefinition:
    """A minimal, otherwise-valid tabular_ml ScenarioDefinition, varying only ui_extras
    — for testing the region-map cross-field validators in isolation.
    """
    return ScenarioDefinition(
        slug="test_region_map",
        kind="tabular_ml",
        title="Test Region Map",
        description="A minimal scenario for testing region-map validation.",
        role_required="scenario:test_region_map",
        icon="🧪",
        industry="general",
        chat=ChatConfig(context="test"),
        dataset=TabularDataset(
            bucket="b",
            raw_object="raw/x.csv",
            seed_file="sample_data/x.csv",
            index_col="row_id",
            target="y",
            feature_columns=["region"],
            feature_schema={"region": CategoricalFeatureUI(label="Region", options=["a", "b"], default="a")},
        ),
        model=TabularModel(
            task_type="regression",
            candidates=["lightgbm"],
            accuracy_gain_threshold_for_complexity=0.02,
            target_label="Y",
        ),
        services=SERVICES,
        ui_extras=ui_extras,
    )


def test_qdrant_collection_name_is_prefix_and_org_scoped() -> None:
    """The collection name combines the configured prefix and the tenant's org id —
    etl-vectorize (writer) and rag-agent (reader) both call this, not their own copy.
    """
    vector_store = VectorStoreConfig(backend="qdrant", collection_prefix="docs_rag", top_k=3)

    assert qdrant_collection_name(vector_store, "org-1") == "docs_rag__org-1"


def test_documents_config_accepts_a_local_seed_prefix() -> None:
    """A tracked local seed folder alone is a valid seed source."""
    documents = DocumentsConfig(
        bucket="b", raw_prefix="raw/", seed_prefix="sample_docs", chunking=CHUNKING, embedding=EMBEDDING
    )

    assert documents.seed_prefix == "sample_docs"
    assert documents.github_source is None


def test_documents_config_accepts_a_github_source() -> None:
    """A public GitHub repo folder alone is a valid seed source."""
    documents = DocumentsConfig(
        bucket="b",
        raw_prefix="raw/",
        github_source=GithubDocsSource(repo="owner/name", path="reference", ref="develop"),
        chunking=CHUNKING,
        embedding=EMBEDDING,
    )

    assert documents.seed_prefix is None
    assert documents.github_source is not None
    assert documents.github_source.repo == "owner/name"


def test_documents_config_rejects_neither_seed_source() -> None:
    """Omitting both seed_prefix and github_source is invalid — there'd be nothing to bootstrap from."""
    with pytest.raises(ValidationError, match="at least one"):
        DocumentsConfig(bucket="b", raw_prefix="raw/", chunking=CHUNKING, embedding=EMBEDDING)


def test_documents_config_accepts_both_seed_sources_as_github_primary_with_local_fallback() -> None:
    """Setting both is valid: github_source is the primary source, seed_prefix is the
    local fallback ensure_raw_docs falls back to if the GitHub fetch fails.
    """
    documents = DocumentsConfig(
        bucket="b",
        raw_prefix="raw/",
        seed_prefix="sample_docs",
        github_source=GithubDocsSource(repo="owner/name", path="reference"),
        chunking=CHUNKING,
        embedding=EMBEDDING,
    )

    assert documents.seed_prefix == "sample_docs"
    assert documents.github_source is not None


def test_region_map_extra_accepts_a_valid_group_by_and_overrides() -> None:
    """A ui_extras.region_map whose group_by and every region's feature_overrides key
    are real dataset.feature_columns is valid.
    """
    scenario = _region_map_scenario(
        RegionMapExtra(
            group_by="region",
            value_label="Predicted value",
            regions=[MapRegion(key="a", label="A", lat=0.0, lon=0.0, feature_overrides={"region": "a"})],
        )
    )

    assert scenario.ui_extras is not None
    assert scenario.ui_extras.group_by == "region"


def test_region_map_extra_rejects_a_group_by_not_in_feature_columns() -> None:
    """ui_extras.group_by must name a real dataset.feature_columns entry."""
    with pytest.raises(ValidationError, match="group_by"):
        _region_map_scenario(
            RegionMapExtra(
                group_by="not_a_feature",
                value_label="Predicted value",
                regions=[MapRegion(key="a", label="A", lat=0.0, lon=0.0)],
            )
        )


def test_region_map_extra_rejects_a_feature_override_not_in_feature_columns() -> None:
    """Every region's feature_overrides key must also name a real dataset.feature_columns entry."""
    with pytest.raises(ValidationError, match="feature_overrides"):
        _region_map_scenario(
            RegionMapExtra(
                group_by="region",
                value_label="Predicted value",
                regions=[MapRegion(key="a", label="A", lat=0.0, lon=0.0, feature_overrides={"not_a_feature": 1})],
            )
        )
