"""Tests for ai_circus_shared.scenario_schema's DocumentsConfig seed-source validation
and the ui_extras (region_map / live_plant / process_optimizer) cross-field validators."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ai_circus_shared.scenario_schema import (
    CategoricalFeatureUI,
    ChatConfig,
    CycleTimeModel,
    DocumentChunking,
    DocumentEmbedding,
    DocumentsConfig,
    GithubDocsSource,
    LivePlantExtra,
    MapRegion,
    NumericFeatureUI,
    OptimizerEconomics,
    ProcessOptimizerExtra,
    RegionMapExtra,
    RegionMapLevel,
    ScenarioDefinition,
    SpecOption,
    TabularDataset,
    TabularModel,
    TabularServices,
    TextFeatureUI,
    ToolLifeModel,
    VectorStoreConfig,
    qdrant_collection_name,
)

CHUNKING = DocumentChunking(strategy="recursive_character", chunk_size=800, chunk_overlap=120)
EMBEDDING = DocumentEmbedding(model="sentence-transformers/all-MiniLM-L6-v2")

SERVICES = TabularServices(etl="etl-tabular", training="training", prediction="prediction", assistant="assistant")


def _ui_extras_scenario(
    ui_extras: RegionMapExtra | LivePlantExtra | ProcessOptimizerExtra, task_type: str = "regression"
) -> ScenarioDefinition:
    """A minimal, otherwise-valid tabular_ml ScenarioDefinition (one categorical
    feature "region", two numeric features "wear" and "speed"), varying only
    ui_extras — for testing the region-map/live-plant/process-optimizer cross-field
    validators in isolation.
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
            feature_columns=["region", "wear", "speed"],
            feature_schema={
                "region": CategoricalFeatureUI(label="Region", options=["a", "b"], default="a"),
                "wear": NumericFeatureUI(label="Wear", min=0, max=100, default=0),
                "speed": NumericFeatureUI(label="Speed", min=1, max=10, default=5),
            },
        ),
        model=TabularModel(
            task_type=task_type,  # type: ignore[arg-type]
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


def _optimizer(**overrides: object) -> ProcessOptimizerExtra:
    """A valid process_optimizer block for the `_ui_extras_scenario` fixture (recipe
    knob "speed", aging feature "wear"), with keyword overrides for the negative cases.
    """
    base: dict[str, object] = {
        "controllable": ["speed"],
        "spec_options": [SpecOption(label="fine", value=2.0), SpecOption(label="coarse", value=5.0)],
        "spec_default": 2.0,
        "wear_feature": "wear",
        "economics": OptimizerEconomics(
            unit_value=10,
            reject_cost=4,
            machine_rate_per_hour=60,
            cycle_time=CycleTimeModel(fixed_minutes=0.5, work_per_unit=100, rate_features=["speed"]),
            tool_life=ToolLifeModel(feature="speed", reference_value=5, reference_life_minutes=100, cost_per_edge=8),
        ),
    }
    base.update(overrides)
    return ProcessOptimizerExtra(**base)  # type: ignore[arg-type]


def test_process_optimizer_extra_accepts_a_valid_block() -> None:
    """A process_optimizer whose controllable/rate/tool-life/wear features are all
    real numeric features and whose default spec is offered is valid, with the
    documented defaults for the optional fields.
    """
    scenario = _ui_extras_scenario(_optimizer())

    extras = scenario.ui_extras
    assert isinstance(extras, ProcessOptimizerExtra)
    assert extras.objective == "minimize"
    assert extras.discrete_values == {}
    assert extras.tick_seconds == 3
    assert extras.economics.batch_size == 500
    assert extras.economics.tool_life is not None
    assert extras.economics.tool_life.taylor_n == 0.25


def test_process_optimizer_extra_accepts_no_wear_feature_and_no_tool_life() -> None:
    """The live-line aging story is optional — a scenario with no consumable tool just
    optimizes/applies recipes with a constant-per-unit cost model.
    """
    economics = OptimizerEconomics(
        unit_value=10,
        reject_cost=4,
        machine_rate_per_hour=60,
        cycle_time=CycleTimeModel(fixed_minutes=1, work_per_unit=1),
    )
    scenario = _ui_extras_scenario(_optimizer(wear_feature=None, economics=economics))

    assert isinstance(scenario.ui_extras, ProcessOptimizerExtra)
    assert scenario.ui_extras.wear_feature is None
    assert scenario.ui_extras.economics.tool_life is None


def test_process_optimizer_extra_rejects_a_categorical_controllable() -> None:
    """Recipe knobs must be numeric — the optimizer samples a continuous/discrete numeric range."""
    with pytest.raises(ValidationError, match=r"controllable.*numeric"):
        _ui_extras_scenario(_optimizer(controllable=["region"]))


def test_process_optimizer_extra_rejects_an_unknown_controllable() -> None:
    with pytest.raises(ValidationError, match="controllable 'not_a_feature'"):
        _ui_extras_scenario(_optimizer(controllable=["not_a_feature"]))


def test_process_optimizer_extra_rejects_discrete_values_for_a_non_controllable() -> None:
    """discrete_values only makes sense for a knob the optimizer actually searches."""
    with pytest.raises(ValidationError, match="discrete_values"):
        _ui_extras_scenario(_optimizer(discrete_values={"wear": [0.0, 50.0]}))


def test_process_optimizer_extra_rejects_a_spec_default_not_offered() -> None:
    with pytest.raises(ValidationError, match="spec_default"):
        _ui_extras_scenario(_optimizer(spec_default=3.0))


def test_process_optimizer_extra_rejects_a_wear_feature_without_tool_life() -> None:
    """wear_feature needs economics.tool_life — that's what defines its aging rate."""
    economics = OptimizerEconomics(
        unit_value=10,
        reject_cost=4,
        machine_rate_per_hour=60,
        cycle_time=CycleTimeModel(fixed_minutes=1, work_per_unit=1),
    )
    with pytest.raises(ValidationError, match="tool_life is missing"):
        _ui_extras_scenario(_optimizer(economics=economics))


def test_process_optimizer_extra_rejects_an_unknown_rate_feature() -> None:
    economics = OptimizerEconomics(
        unit_value=10,
        reject_cost=4,
        machine_rate_per_hour=60,
        cycle_time=CycleTimeModel(fixed_minutes=1, work_per_unit=1, rate_features=["not_a_feature"]),
        tool_life=ToolLifeModel(feature="speed", reference_value=5, reference_life_minutes=100, cost_per_edge=8),
    )
    with pytest.raises(ValidationError, match="rate_features"):
        _ui_extras_scenario(_optimizer(economics=economics))


def test_process_optimizer_extra_rejects_a_classification_scenario() -> None:
    """A probability isn't a physical quantity to keep within a spec limit."""
    with pytest.raises(ValidationError, match="regression"):
        _ui_extras_scenario(_optimizer(), task_type="classification")


def test_process_optimizer_extra_requires_at_least_one_controllable_and_spec() -> None:
    with pytest.raises(ValidationError):
        _optimizer(controllable=[])
    with pytest.raises(ValidationError):
        _optimizer(spec_options=[])


# ── deep_learning kind ─────────────────────────────────────────────────────────

from ai_circus_shared.deep_learning import gallery_sample_id, image_key, mask_key, reference_sample_id  # noqa: E402
from ai_circus_shared.scenario_schema import (  # noqa: E402
    DeepLearningConfig,
    DeepLearningServices,
    DlAnomalyDetection,
    DlLabel,
    DlTrainBudget,
    DlTraining,
    HuggingFaceFilesSource,
    HuggingFaceImageFolderSource,
    NpzImagesSource,
    ReadingRoomExtra,
    TriageBoardExtra,
    TriageLane,
)

DL_SERVICES = DeepLearningServices(training="dl-training", inference="dl-inference")
BUDGET = DlTrainBudget(epochs=1, batch_size=2, learning_rate=1e-4)


def _text_source() -> HuggingFaceFilesSource:
    return HuggingFaceFilesSource(
        repo="o/d",
        revision="abc",
        files={"train": "train.jsonl", "test": "test.jsonl"},
        sha256={"train.jsonl": "1", "test.jsonl": "2"},
        text_field="t",
        label_field="l",
    )


def _dl_config(modality: str = "text") -> DeepLearningConfig:
    source = _text_source() if modality == "text" else NpzImagesSource(url="https://x/y.npz", md5="0")
    return DeepLearningConfig(
        modality=modality,  # type: ignore[arg-type]
        bucket="b",
        source=source,
        labels=[DlLabel(key="a", label="A"), DlLabel(key="b", label="B")],
        base_model="org/model",
        base_model_revision="rev",
        base_model_params="1M",
        input_label="Input",
        target_label="Target",
        training=DlTraining(gpu=BUDGET, cpu=BUDGET),
    )


def _dl_scenario(**overrides: object) -> ScenarioDefinition:
    fields: dict[str, object] = {
        "slug": "dl",
        "kind": "deep_learning",
        "title": "DL",
        "description": "d",
        "role_required": "scenario:dl",
        "icon": "🧪",
        "industry": "healthcare",
        "chat": ChatConfig(context="c"),
        "deep_learning": _dl_config(),
        "services": DL_SERVICES,
    }
    fields.update(overrides)
    return ScenarioDefinition(**fields)  # type: ignore[arg-type]


def test_deep_learning_scenario_is_valid() -> None:
    scenario = _dl_scenario()
    assert scenario.deep_learning is not None
    assert isinstance(scenario.services, DeepLearningServices)


def test_deep_learning_kind_requires_the_block_and_vice_versa() -> None:
    with pytest.raises(ValidationError, match="requires a `deep_learning` block"):
        _dl_scenario(deep_learning=None)
    with pytest.raises(ValidationError, match="requires a `deep_learning` block"):
        _dl_scenario(kind="tabular_ml")


def test_deep_learning_kind_requires_deep_learning_services() -> None:
    with pytest.raises(ValidationError, match="services"):
        _dl_scenario(services=SERVICES)


def test_modality_must_match_the_source_type() -> None:
    with pytest.raises(ValidationError, match="npz_images or huggingface_image_folder source"):
        DeepLearningConfig(**{**_dl_config().model_dump(), "modality": "image"})
    with pytest.raises(ValidationError, match="needs a huggingface_files source with text_field"):
        DeepLearningConfig(**{**_dl_config("image").model_dump(), "modality": "text"})


def _parquet_image_source(**overrides: object) -> HuggingFaceFilesSource:
    fields: dict[str, object] = {
        "repo": "o/d",
        "revision": "abc",
        "files": {"train": "train.parquet", "test": "test.parquet"},
        "sha256": {"train.parquet": "1", "test.parquet": "2"},
        "format": "parquet",
        "image_field": "image",
        "mask_field": "mask",
        "label_field": "label",
    }
    return HuggingFaceFilesSource(**{**fields, **overrides})  # type: ignore[arg-type]


def test_huggingface_parquet_image_source() -> None:
    config = DeepLearningConfig(**{**_dl_config("image").model_dump(), "source": _parquet_image_source().model_dump()})
    assert isinstance(config.source, HuggingFaceFilesSource)
    assert config.source.image_field == "image"
    with pytest.raises(ValidationError, match="exactly one of text_field / image_field"):
        _parquet_image_source(text_field="t")
    with pytest.raises(ValidationError, match="must be format='parquet'"):
        _parquet_image_source(format="jsonl")
    with pytest.raises(ValidationError, match="mask_field needs an image_field"):
        HuggingFaceFilesSource(**{**_text_source().model_dump(), "mask_field": "m"})
    merged = _parquet_image_source(label_map={"3": "a", "*": "b"}).model_dump()
    assert DeepLearningConfig(**{**_dl_config("image").model_dump(), "source": merged}).source.label_map["*"] == "b"
    with pytest.raises(ValidationError, match=r"huggingface_files label_map values \['z'\] are not label keys"):
        DeepLearningConfig(
            **{**_dl_config("image").model_dump(), "source": _parquet_image_source(label_map={"3": "z"}).model_dump()}
        )


def _image_folder_source(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = {
        "type": "huggingface_image_folder",
        "repo": "o/d",
        "revision": "abc",
        "folders": {"train": "images/train/part", "test": "images/test/part"},
        "mask_folders": {"test": "masks/test/part"},
        "label_map": {"good": "a", "*": "b"},
        "manifest_sha256": "0" * 64,
    }
    return {**fields, **overrides}


def test_huggingface_image_folder_source() -> None:
    config = DeepLearningConfig(**{**_dl_config("image").model_dump(), "source": _image_folder_source()})
    assert isinstance(config.source, HuggingFaceImageFolderSource)
    assert config.source.mask_suffix == "_mask"
    with pytest.raises(ValidationError, match="'train' and 'test' folders"):
        HuggingFaceImageFolderSource(**_image_folder_source(folders={"train": "t"}))  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match=r"mask_folders for unknown splits: \['validation'\]"):
        HuggingFaceImageFolderSource(**_image_folder_source(mask_folders={"validation": "m"}))  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match=r"label_map values \['z'\] are not label keys"):
        DeepLearningConfig(
            **{**_dl_config("image").model_dump(), "source": _image_folder_source(label_map={"good": "a", "*": "z"})}
        )
    with pytest.raises(ValidationError, match="needs a huggingface_files source with text_field"):
        DeepLearningConfig(**{**_dl_config().model_dump(), "source": _image_folder_source()})


def _anomaly_config(**overrides: object) -> DeepLearningConfig:
    bank = DlTrainBudget(batch_size=4, memory_bank_size=64)
    fields = {
        **_dl_config("image").model_dump(),
        "task": "anomaly_detection",
        "anomaly": DlAnomalyDetection(normal_label="a").model_dump(),
        "training": DlTraining(gpu=bank, cpu=bank, calibration_holdout_fraction=0.3).model_dump(),
    }
    return DeepLearningConfig(**{**fields, **overrides})  # type: ignore[arg-type]


def test_anomaly_detection_task_is_valid_without_epochs() -> None:
    config = _anomaly_config()
    assert config.task == "anomaly_detection"
    assert config.training.gpu.epochs is None


def test_anomaly_detection_task_validation() -> None:
    with pytest.raises(ValidationError, match="modality='image' and an `anomaly` block"):
        _anomaly_config(anomaly=None)
    with pytest.raises(ValidationError, match="normal_label 'z' is not a label key"):
        _anomaly_config(anomaly={"normal_label": "z"})
    with pytest.raises(ValidationError, match="exactly two labels"):
        _anomaly_config(labels=[{"key": k, "label": k} for k in "abc"])
    with pytest.raises(ValidationError, match="calibration_holdout_fraction > 0"):
        _anomaly_config(
            training={
                "gpu": {"batch_size": 1, "memory_bank_size": 64},
                "cpu": {"batch_size": 1, "memory_bank_size": 64},
            }
        )
    with pytest.raises(ValidationError, match=r"training\.cpu needs memory_bank_size"):
        _anomaly_config(
            training={
                "gpu": {"batch_size": 1, "memory_bank_size": 64},
                "cpu": {"batch_size": 1},
                "calibration_holdout_fraction": 0.3,
            }
        )


def test_classification_task_needs_epochs_and_no_anomaly_block() -> None:
    with pytest.raises(ValidationError, match=r"training\.gpu needs epochs and learning_rate"):
        DeepLearningConfig(
            **{**_dl_config().model_dump(), "training": {"gpu": {"batch_size": 1}, "cpu": BUDGET.model_dump()}}
        )
    with pytest.raises(ValidationError, match="only for task='anomaly_detection'"):
        DeepLearningConfig(**{**_dl_config("image").model_dump(), "anomaly": {"normal_label": "a"}})


def test_labels_must_be_unique() -> None:
    with pytest.raises(ValidationError, match="unique"):
        DeepLearningConfig(**{**_dl_config().model_dump(), "labels": [{"key": "a", "label": "A"}] * 2})


def test_huggingface_source_needs_train_test_and_checksums() -> None:
    with pytest.raises(ValidationError, match="'train' and 'test'"):
        HuggingFaceFilesSource(**{**_text_source().model_dump(), "files": {"train": "train.jsonl"}})
    with pytest.raises(ValidationError, match="no sha256"):
        HuggingFaceFilesSource(**{**_text_source().model_dump(), "sha256": {"train.jsonl": "1"}})


def test_triage_board_must_route_every_label_exactly_once() -> None:
    ok = TriageBoardExtra(
        lanes=[TriageLane(key="x", label="X", labels=["a"]), TriageLane(key="y", label="Y", labels=["b"])]
    )
    assert _dl_scenario(ui_extras=ok).ui_extras == ok
    with pytest.raises(ValidationError, match="unrouted=\\['b'\\]"):
        _dl_scenario(ui_extras=TriageBoardExtra(lanes=[TriageLane(key="x", label="X", labels=["a"])]))
    with pytest.raises(ValidationError, match="duplicated=\\['a'\\]"):
        _dl_scenario(
            ui_extras=TriageBoardExtra(
                lanes=[TriageLane(key="x", label="X", labels=["a", "b"]), TriageLane(key="y", label="Y", labels=["a"])]
            )
        )
    with pytest.raises(ValidationError, match="unknown labels"):
        _dl_scenario(ui_extras=TriageBoardExtra(lanes=[TriageLane(key="x", label="X", labels=["a", "b", "z"])]))


def test_triage_board_also_serves_image_scenarios() -> None:
    """An inspection line is a triage board of images: pass / reject / manual review."""
    board = TriageBoardExtra(
        lanes=[
            TriageLane(key="pass", label="Pass", labels=["a"]),
            TriageLane(key="reject", label="Reject", labels=["b"]),
        ],
        item_noun="part",
        reviewer_noun="inspector",
    )
    assert _dl_scenario(deep_learning=_anomaly_config(), ui_extras=board).ui_extras == board


def test_reading_room_needs_an_image_scenario_and_a_real_positive_label() -> None:
    image = _dl_config("image")
    assert _dl_scenario(deep_learning=image, ui_extras=ReadingRoomExtra(positive_label="b")).ui_extras is not None
    with pytest.raises(ValidationError, match="not a label key"):
        _dl_scenario(deep_learning=image, ui_extras=ReadingRoomExtra(positive_label="z"))
    with pytest.raises(ValidationError, match="requires modality='image'"):
        _dl_scenario(ui_extras=ReadingRoomExtra(positive_label="b"))


def test_ui_extras_kinds_are_scoped_to_their_scenario_kind() -> None:
    with pytest.raises(ValidationError, match="not available for kind='deep_learning'"):
        _dl_scenario(ui_extras=LivePlantExtra())
    with pytest.raises(ValidationError, match="not available for kind='tabular_ml'"):
        _ui_extras_scenario(ReadingRoomExtra(positive_label="a"))  # type: ignore[arg-type]


def test_sample_ids_and_image_keys() -> None:
    assert image_key(gallery_sample_id(3)) == "images/s-3.png"
    assert image_key(reference_sample_id(12)) == "images/r-12.png"
    assert mask_key(gallery_sample_id(3)) == "masks/s-3.png"
    with pytest.raises(ValueError, match="Invalid sample id"):
        mask_key("../s-1")
    for bad in ["../x", "s-", "x-1", "s-1/../../y", "s-1234567"]:
        with pytest.raises(ValueError, match="Invalid sample id"):
            image_key(bad)


def test_repo_deep_learning_scenarios_load() -> None:
    from pathlib import Path

    from ai_circus_shared.scenario_schema import resolve_scenarios

    scenarios = resolve_scenarios(Path(__file__).parents[3] / "scenarios", "", kind="deep_learning")
    assert set(scenarios) == {
        "symptom_triage",
        "chest_xray_pneumonia",
        "pcb_visual_inspection",
        "screw_visual_inspection",
        "solar_cell_inspection",
    }


def test_resolve_scenarios_ignores_other_kinds_it_cannot_parse(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A scenario of an unknown/future kind must not break services of other kinds."""
    from ai_circus_shared.scenario_schema import resolve_scenarios

    (tmp_path / "future").mkdir()
    (tmp_path / "future" / "scenario.yaml").write_text("slug: future\nkind: quantum_ml\nwhatever: {}\n")
    (tmp_path / "broken_other").mkdir()
    (tmp_path / "broken_other" / "scenario.yaml").write_text("kind: conversational_rag\n")  # invalid, other kind
    repo = __import__("pathlib").Path(__file__).parents[3] / "scenarios"
    (tmp_path / "triage").mkdir()
    (tmp_path / "triage" / "scenario.yaml").write_text((repo / "symptom_triage" / "scenario.yaml").read_text())

    assert set(resolve_scenarios(tmp_path, "", kind="deep_learning")) == {"symptom_triage"}
    assert resolve_scenarios(tmp_path, "", kind="tabular_ml") == {}


# --- titanic-era additions: display columns, CV/metric selection, voyage_explorer, tutorial ---


def _titanic() -> ScenarioDefinition:
    from pathlib import Path

    return ScenarioDefinition.load(Path(__file__).parents[3] / "scenarios/titanic/scenario.yaml")


def _with(definition: ScenarioDefinition, **changes: object) -> ScenarioDefinition:
    """Re-validate a copy with `changes` applied (model_copy alone skips validators)."""
    return ScenarioDefinition.model_validate({**definition.model_dump(), **changes})


def test_repo_titanic_scenario_loads_as_a_tutorial() -> None:
    titanic = _titanic()
    assert titanic.industry == "tutorial"
    assert titanic.model is not None and titanic.model.selection_metric == "roc_auc"
    assert titanic.model.cv_folds == 5
    assert titanic.dataset is not None and titanic.dataset.display_columns == ["Name"]
    assert titanic.ui_extras is not None and titanic.ui_extras.kind == "voyage_explorer"
    assert titanic.tutorial is not None and len(titanic.tutorial.steps) >= 10


def test_society_ethics_is_a_valid_domain() -> None:
    assert _with(_titanic(), industry="society_ethics").industry == "society_ethics"


def test_display_columns_cannot_be_features_or_protected() -> None:
    titanic = _titanic()
    dataset = titanic.dataset.model_dump()  # type: ignore[union-attr]
    with pytest.raises(ValidationError, match="display_columns"):
        TabularDataset.model_validate({**dataset, "display_columns": ["Sex"]})
    with pytest.raises(ValidationError, match="display_columns"):
        TabularDataset.model_validate({**dataset, "display_columns": ["Name"], "protected_features_excluded": ["Name"]})


def test_selection_metric_must_match_the_task_and_cv_folds_cannot_be_one() -> None:
    base = {"candidates": ["lightgbm"], "accuracy_gain_threshold_for_complexity": 0.01, "target_label": "Y"}
    with pytest.raises(ValidationError, match="not valid for regression"):
        TabularModel(task_type="regression", selection_metric="roc_auc", **base)  # type: ignore[arg-type]
    with pytest.raises(ValidationError, match="cv_folds"):
        TabularModel(task_type="classification", cv_folds=1, **base)  # type: ignore[arg-type]
    assert TabularModel(task_type="regression", selection_metric="r2", cv_folds=3, **base).cv_folds == 3  # type: ignore[arg-type]


def test_voyage_explorer_requires_every_zone_option_exactly_once() -> None:
    titanic = _titanic()
    extras = titanic.ui_extras.model_dump()  # type: ignore[union-attr]
    extras["zones"] = extras["zones"][:2]
    with pytest.raises(ValidationError, match="exactly once"):
        _with(titanic, ui_extras=extras)


def test_voyage_explorer_rejects_bad_filters_names_and_personas() -> None:
    titanic = _titanic()
    extras = titanic.ui_extras.model_dump()  # type: ignore[union-attr]
    with pytest.raises(ValidationError, match="must be a categorical feature"):
        _with(titanic, ui_extras={**extras, "filters": ["Age"]})
    with pytest.raises(ValidationError, match="not a display_column"):
        _with(titanic, ui_extras={**extras, "name_column": "Ticket"})
    bad_persona = {**extras["personas"][0], "record": {**extras["personas"][0]["record"], "Pclass": "4th"}}
    with pytest.raises(ValidationError, match="not among its options"):
        _with(titanic, ui_extras={**extras, "personas": [bad_persona]})


def test_voyage_explorer_requires_a_classification_model() -> None:
    titanic = _titanic()
    model = {**titanic.model.model_dump(), "task_type": "regression", "selection_metric": None}  # type: ignore[union-attr]
    with pytest.raises(ValidationError, match="requires a classification model"):
        _with(titanic, model=model)


def test_tutorial_validates_chart_columns_and_examples() -> None:
    titanic = _titanic()
    tutorial = titanic.tutorial.model_dump()  # type: ignore[union-attr]
    step = {**tutorial["steps"][0], "charts": [{"type": "bar", "x": "Cabin"}]}
    with pytest.raises(ValidationError, match="unknown"):
        _with(titanic, tutorial={**tutorial, "steps": [step]})
    missing = {**tutorial["examples"][0], "record": {"Pclass": "1st"}}
    with pytest.raises(ValidationError, match="missing features"):
        _with(titanic, tutorial={**tutorial, "examples": [missing]})
    out_of_range = {**tutorial["examples"][0], "record": {**tutorial["examples"][0]["record"], "Age": 300}}
    with pytest.raises(ValidationError, match="outside"):
        _with(titanic, tutorial={**tutorial, "examples": [out_of_range]})
    with pytest.raises(ValidationError, match="examples widget"):
        _with(titanic, tutorial={**tutorial, "examples": []})


def test_tutorial_is_tabular_ml_only() -> None:
    titanic = _titanic()
    with pytest.raises(ValidationError, match="only available for kind='tabular_ml'"):
        _with(titanic, kind="conversational_rag")


# --- free-text features (toxic_leadership) ---


def _titanic_with_text(max_length: int = 200) -> dict:  # type: ignore[type-arg]
    """Titanic's dump with an extra `Notes` text feature (every example/persona gets one)."""
    raw = _titanic().model_dump()
    raw["dataset"]["feature_columns"].append("Notes")
    raw["dataset"]["feature_schema"]["Notes"] = {"type": "text", "label": "Notes", "max_length": max_length}
    for example in [*raw["tutorial"]["examples"], *raw["ui_extras"]["personas"]]:
        example["record"]["Notes"] = "Travelling with family."
    return raw


def test_text_feature_is_parsed_and_capped() -> None:
    definition = ScenarioDefinition.model_validate(_titanic_with_text())
    assert definition.dataset is not None
    assert definition.dataset.feature_schema["Notes"].type == "text"
    assert definition.dataset.text_columns() == ["Notes"]
    assert TextFeatureUI(label="Review").max_length == 1000
    with pytest.raises(ValidationError):
        TextFeatureUI(label="Review", max_length=50_000)


def test_text_features_cannot_be_charted() -> None:
    raw = _titanic_with_text()
    with pytest.raises(ValidationError, match="free-text"):
        ScenarioDefinition.model_validate(
            {**raw, "dataset": {**raw["dataset"], "default_charts": [{"type": "bar", "x": "Notes"}]}}
        )
    with pytest.raises(ValidationError, match="unknown"):
        ScenarioDefinition.model_validate(
            {**raw, "dataset": {**raw["dataset"], "default_charts": [{"type": "bar", "x": "Cabin"}]}}
        )
    step = {**raw["tutorial"]["steps"][0], "charts": [{"type": "histogram", "x": "Age", "color_by": "Notes"}]}
    with pytest.raises(ValidationError, match="free-text"):
        ScenarioDefinition.model_validate({**raw, "tutorial": {**raw["tutorial"], "steps": [step]}})


def test_text_example_values_are_length_checked() -> None:
    raw = _titanic_with_text(max_length=10)
    with pytest.raises(ValidationError, match="at most 10 chars"):
        ScenarioDefinition.model_validate(raw)


def test_text_features_cannot_be_voyage_filters() -> None:
    raw = _titanic_with_text()
    with pytest.raises(ValidationError, match="must be a categorical feature"):
        ScenarioDefinition.model_validate({**raw, "ui_extras": {**raw["ui_extras"], "filters": ["Notes"]}})


def test_top_terms_widget_needs_a_text_feature() -> None:
    titanic = _titanic()
    tutorial = titanic.tutorial.model_dump()  # type: ignore[union-attr]
    step = {**tutorial["steps"][0], "widgets": ["top_terms"]}
    with pytest.raises(ValidationError, match="no text feature"):
        _with(titanic, tutorial={**tutorial, "steps": [step]})
    raw = _titanic_with_text()
    raw["tutorial"]["steps"][0]["widgets"] = ["top_terms"]
    assert ScenarioDefinition.model_validate(raw).tutorial is not None


def test_office_tower_is_a_valid_voyage_scene() -> None:
    titanic = _titanic()
    extras = titanic.ui_extras.model_dump()  # type: ignore[union-attr]
    assert _with(titanic, ui_extras={**extras, "scene": "office_tower"}).ui_extras.scene == "office_tower"  # type: ignore[union-attr]
    with pytest.raises(ValidationError):
        _with(titanic, ui_extras={**extras, "scene": "submarine"})


def _toxic() -> ScenarioDefinition:
    from pathlib import Path

    return ScenarioDefinition.load(Path(__file__).parents[3] / "scenarios/toxic_leadership/scenario.yaml")


def test_repo_toxic_leadership_scenario_loads_with_text_challenger_and_rubric() -> None:
    toxic = _toxic()
    assert toxic.industry == "tutorial"
    assert toxic.dataset is not None and toxic.dataset.text_columns() == ["Review"]
    assert toxic.model is not None and "logistic_regression" not in toxic.model.candidates
    assert toxic.model.text_challenger is not None and toxic.model.text_challenger.embedding_model == "local-embed"
    assert toxic.ui_extras is not None and toxic.ui_extras.scene == "office_tower"  # type: ignore[union-attr]
    assert toxic.rubric_check is not None and toxic.rubric_check.text_feature == "Review"
    assert toxic.tutorial is not None and any("top_terms" in s.widgets for s in toxic.tutorial.steps)


def test_rubric_check_needs_a_text_feature_and_unique_keys() -> None:
    toxic = _toxic()
    rubric = toxic.rubric_check.model_dump()  # type: ignore[union-attr]
    with pytest.raises(ValidationError, match="text_feature"):
        _with(toxic, rubric_check={**rubric, "text_feature": "CompBenefits"})
    duplicate = {**rubric, "negative": [*rubric["negative"], rubric["positive"][0]]}
    with pytest.raises(ValidationError, match="unique"):
        _with(toxic, rubric_check=duplicate)
    with pytest.raises(ValidationError, match="type: text"):
        _with(_titanic(), rubric_check=rubric)


def test_text_challenger_needs_a_text_feature() -> None:
    titanic = _titanic()
    challenger = _toxic().model.text_challenger.model_dump()  # type: ignore[union-attr]
    model = {**titanic.model.model_dump(), "text_challenger": challenger}  # type: ignore[union-attr]
    with pytest.raises(ValidationError, match="text_challenger requires"):
        _with(titanic, model=model)


# --- public-sector additions: official-layout multi-model forms, risk_watchlist ---


def _repo_scenario(slug: str) -> ScenarioDefinition:
    from pathlib import Path

    return ScenarioDefinition.load(Path(__file__).parents[3] / f"scenarios/{slug}/scenario.yaml")


def test_repo_sede_electronica_is_a_multi_model_official_form() -> None:
    sede = _repo_scenario("sede_electronica")
    form = sede.form
    assert form is not None and form.locale == "es" and form.general_variant == "solicitud_general"
    assert {v.key for v in form.variants} == set(form.classification_options or [])
    assert [f.id for f in form.active_fields({"tramite": "devolucion"})][-1] == "di_titular"
    assert form.variant_for({"tramite": "nope"}) is None
    assert len(form.active_fields({})) == len(form.fields)
    assert all(upload.file for upload in form.sample_uploads)


def _form(**changes: object) -> dict:  # type: ignore[type-arg]
    form = _repo_scenario("sede_electronica").form.model_dump()  # type: ignore[union-attr]
    return {**form, **changes}


def test_form_variants_must_be_classification_options_and_need_sections() -> None:
    from ai_circus_shared.scenario_schema import FormConfig

    form = _form()
    bad_key = [{**form["variants"][0], "key": "not_an_option"}, *form["variants"][1:]]
    with pytest.raises(ValidationError, match="unique classification_options"):
        FormConfig.model_validate({**form, "variants": bad_key})
    with pytest.raises(ValidationError, match="requires shared `sections`"):
        FormConfig.model_validate({**form, "sections": []})
    with pytest.raises(ValidationError, match="general_variant"):
        FormConfig.model_validate({**form, "general_variant": "missing"})


def test_form_fields_need_a_known_section_and_unique_ids_and_boxes() -> None:
    from ai_circus_shared.scenario_schema import FormConfig

    form = _form()
    fields = form["fields"]
    with pytest.raises(ValidationError, match="section"):
        FormConfig.model_validate({**form, "fields": [*fields[:-1], {**fields[-1], "section": "nowhere"}]})
    with pytest.raises(ValidationError, match="casilla numbers must be unique"):
        FormConfig.model_validate({**form, "fields": [*fields[:-1], {**fields[-1], "casilla": "01"}]})
    variant = form["variants"][1]
    clash = {**variant, "fields": [*variant["fields"], {**variant["fields"][0], "id": "nif", "casilla": "99"}]}
    with pytest.raises(ValidationError, match="unique across shared fields and variants"):
        FormConfig.model_validate({**form, "variants": [form["variants"][0], clash, *form["variants"][2:]]})


def test_a_plain_form_cannot_place_fields_in_sections() -> None:
    from ai_circus_shared.scenario_schema import FormConfig, FormFieldSpec

    with pytest.raises(ValidationError, match="defines no `sections`"):
        FormConfig(title="t", fields=[FormFieldSpec(id="a", label="A", type="text", section="x")])


def test_repo_bank_early_warning_has_a_valid_risk_watchlist() -> None:
    bank = _repo_scenario("bank_early_warning")
    assert bank.industry == "public_sector"
    assert bank.ui_extras is not None and bank.ui_extras.kind == "risk_watchlist"
    assert bank.model is not None and bank.model.selection_metric == "roc_auc"


def test_risk_watchlist_rejects_unknown_columns_bad_tiers_and_overlapping_pillars() -> None:
    bank = _repo_scenario("bank_early_warning")
    extras = bank.ui_extras.model_dump()  # type: ignore[union-attr]
    with pytest.raises(ValidationError, match="must be a numeric feature"):
        _with(bank, ui_extras={**extras, "landscape_x": "charter_type"})
    with pytest.raises(ValidationError, match=r"not dataset\.display_columns"):
        _with(bank, ui_extras={**extras, "name_column": "cert"})
    with pytest.raises(ValidationError, match="strictly increase"):
        _with(bank, ui_extras={**extras, "tiers": list(reversed(extras["tiers"]))})
    pillars = [*extras["pillars"], {"label": "Again", "features": ["roa_pct"]}]
    with pytest.raises(ValidationError, match="in two pillars"):
        _with(bank, ui_extras={**extras, "pillars": pillars})
    with pytest.raises(ValidationError, match="requires a classification model"):
        _with(bank, model={**bank.model.model_dump(), "task_type": "regression", "selection_metric": None})  # type: ignore[union-attr]
