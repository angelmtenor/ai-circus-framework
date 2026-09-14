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
    LivePlantExtra,
    MapRegion,
    NumericFeatureUI,
    RegionMapExtra,
    RegionMapLevel,
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


def _ui_extras_scenario(ui_extras: RegionMapExtra | LivePlantExtra) -> ScenarioDefinition:
    """A minimal, otherwise-valid tabular_ml ScenarioDefinition (one categorical
    feature "region", one numeric feature "wear"), varying only ui_extras — for
    testing the region-map/live-plant cross-field validators in isolation.
    """
    return ScenarioDefinition(
        slug="test_ui_extras",
        kind="tabular_ml",
        title="Test UI Extras",
        description="A minimal scenario for testing ui_extras validation.",
        role_required="scenario:test_ui_extras",
        icon="🧪",
        industry="general",
        chat=ChatConfig(context="test"),
        dataset=TabularDataset(
            bucket="b",
            raw_object="raw/x.csv",
            seed_file="sample_data/x.csv",
            index_col="row_id",
            target="y",
            feature_columns=["region", "wear"],
            feature_schema={
                "region": CategoricalFeatureUI(label="Region", options=["a", "b"], default="a"),
                "wear": NumericFeatureUI(label="Wear", min=0, max=100, default=0),
            },
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
    """A ui_extras.region_map whose every level's group_by and every region's
    feature_overrides key are real dataset.feature_columns is valid.
    """
    scenario = _ui_extras_scenario(
        RegionMapExtra(
            value_label="Predicted value",
            levels=[
                RegionMapLevel(
                    key="region",
                    label="Region",
                    group_by="region",
                    regions=[MapRegion(key="a", label="A", lat=0.0, lon=0.0, feature_overrides={"region": "a"})],
                )
            ],
        )
    )

    assert scenario.ui_extras is not None
    assert scenario.ui_extras.levels[0].group_by == "region"


def test_region_map_level_accepts_no_group_by() -> None:
    """A level with group_by=None (a coarser level reusing a finer level's real
    trained feature entirely through feature_overrides) is valid.
    """
    scenario = _ui_extras_scenario(
        RegionMapExtra(
            value_label="Predicted value",
            levels=[
                RegionMapLevel(
                    key="region",
                    label="Region",
                    regions=[MapRegion(key="a", label="A", lat=0.0, lon=0.0, feature_overrides={"wear": 5})],
                )
            ],
        )
    )

    assert scenario.ui_extras.levels[0].group_by is None


def test_region_map_extra_accepts_multiple_levels() -> None:
    """Two levels (e.g. region + province) can coexist, each with its own group_by/regions."""
    scenario = _ui_extras_scenario(
        RegionMapExtra(
            value_label="Predicted value",
            levels=[
                RegionMapLevel(
                    key="region",
                    label="Region",
                    group_by="region",
                    regions=[MapRegion(key="a", label="A", lat=0.0, lon=0.0)],
                ),
                RegionMapLevel(
                    key="wear_group",
                    label="Wear group",
                    group_by="region",
                    regions=[MapRegion(key="b", label="B", lat=1.0, lon=1.0, feature_overrides={"wear": 10})],
                ),
            ],
        )
    )

    assert len(scenario.ui_extras.levels) == 2


def test_region_map_extra_rejects_a_group_by_not_in_feature_columns() -> None:
    """Every level's group_by must name a real dataset.feature_columns entry."""
    with pytest.raises(ValidationError, match="group_by"):
        _ui_extras_scenario(
            RegionMapExtra(
                value_label="Predicted value",
                levels=[
                    RegionMapLevel(
                        key="region",
                        label="Region",
                        group_by="not_a_feature",
                        regions=[MapRegion(key="a", label="A", lat=0.0, lon=0.0)],
                    )
                ],
            )
        )


def test_region_map_extra_rejects_a_feature_override_not_in_feature_columns() -> None:
    """Every region's feature_overrides key must also name a real dataset.feature_columns entry."""
    with pytest.raises(ValidationError, match="feature_overrides"):
        _ui_extras_scenario(
            RegionMapExtra(
                value_label="Predicted value",
                levels=[
                    RegionMapLevel(
                        key="region",
                        label="Region",
                        group_by="region",
                        regions=[
                            MapRegion(key="a", label="A", lat=0.0, lon=0.0, feature_overrides={"not_a_feature": 1})
                        ],
                    )
                ],
            )
        )


def test_region_map_extra_rejects_zero_levels() -> None:
    """At least one level is required — an empty region map tab would have nothing to show."""
    with pytest.raises(ValidationError):
        RegionMapExtra(value_label="Predicted value", levels=[])


def test_live_plant_extra_accepts_a_valid_numeric_wear_feature() -> None:
    """A ui_extras.live_plant whose wear_feature names a real numeric feature is valid."""
    scenario = _ui_extras_scenario(LivePlantExtra(wear_feature="wear"))

    assert scenario.ui_extras is not None
    assert scenario.ui_extras.wear_feature == "wear"


def test_live_plant_extra_accepts_no_wear_feature() -> None:
    """wear_feature is optional — None falls back to the generic random-walk simulation."""
    scenario = _ui_extras_scenario(LivePlantExtra())

    assert scenario.ui_extras.wear_feature is None


def test_live_plant_extra_rejects_a_wear_feature_not_in_feature_columns() -> None:
    """wear_feature must name a real dataset.feature_columns entry."""
    with pytest.raises(ValidationError, match="wear_feature"):
        _ui_extras_scenario(LivePlantExtra(wear_feature="not_a_feature"))


def test_live_plant_extra_rejects_a_categorical_wear_feature() -> None:
    """wear_feature must be numeric — a categorical column can't be incremented."""
    with pytest.raises(ValidationError, match="numeric"):
        _ui_extras_scenario(LivePlantExtra(wear_feature="region"))
