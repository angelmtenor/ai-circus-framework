"""Tests for dl-training's entry points (main / download_main) with the pipeline faked."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

import dl_training.app as app
from tests.conftest import FakeSecret
from tests.fixtures import scenario, text_config


class FakeEnvConfig:
    """Minimal stand-in for EnvConfig."""

    def __init__(self, **overrides: Any) -> None:
        """Valid-looking defaults."""
        self.SCENARIOS: str | None = None
        self.SCENARIOS_DIR = "/scenarios"
        self.ORG_ID = "demo"
        self.OBJECT_STORE_ENDPOINT = "http://seaweedfs:8333"
        self.OBJECT_STORE_ACCESS_KEY = "ai_circus"
        self.OBJECT_STORE_SECRET_KEY = FakeSecret("s3cret")
        self.DL_DEVICE: str | None = "cpu"
        self.DL_CACHE_DIR = "/tmp/dl-cache"  # ruff: ignore[hardcoded-temp-file]
        self.MLFLOW_TRACKING_URI = None
        self.__dict__.update(overrides)


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> dict[str, list]:
    """Fake config/scenarios/store; record what the pipeline would have been called with."""
    calls: dict[str, list] = {"train": [], "connect": [], "raw": []}
    monkeypatch.setattr(app, "configure_logger", lambda: None)
    monkeypatch.setattr(app, "get_env_config", lambda: FakeEnvConfig())
    monkeypatch.setattr(
        app,
        "resolve_scenarios",
        lambda *_a, **_kw: {"a": scenario(text_config(), "a"), "b": scenario(text_config(), "b")},
    )
    monkeypatch.setattr(app.ObjectStore, "connect", staticmethod(lambda **kw: calls["connect"].append(kw) or "store"))

    def fake_train(definition: Any, store: Any, **kwargs: Any) -> dict[str, Any]:
        calls["train"].append((definition.slug, kwargs["org_id"], kwargs["device"].kind))
        return {"training_seconds": 1.0, "evaluation": {"metrics": {"accuracy": 1.0}}}

    monkeypatch.setattr(app.pipeline, "train_scenario", fake_train)
    monkeypatch.setattr(
        app.data, "ensure_raw", lambda store, org, dl, cache: calls["raw"].append(org) or {"f": Path("/x")}
    )
    return calls


def test_main_trains_every_resolved_scenario(wired: dict[str, list]) -> None:
    app.main()
    assert wired["train"] == [("a", "demo", "cpu"), ("b", "demo", "cpu")]
    assert wired["connect"][0]["bucket"] == "b"
    assert wired["connect"][0]["secret_key"] == "s3cret"  # ruff: ignore[hardcoded-password-string]


def test_main_keeps_going_after_one_failure_then_exits_1(
    wired: dict[str, list], monkeypatch: pytest.MonkeyPatch
) -> None:
    def flaky(definition: Any, store: Any, **kwargs: Any) -> dict[str, Any]:
        wired["train"].append(definition.slug)
        if definition.slug == "a":
            raise RuntimeError("boom")
        return {"training_seconds": 1.0, "evaluation": {"metrics": {}}}

    monkeypatch.setattr(app.pipeline, "train_scenario", flaky)
    with pytest.raises(SystemExit) as exc:
        app.main()
    assert exc.value.code == 1
    assert wired["train"] == ["a", "b"]


def test_main_rejects_an_impossible_device(wired: dict[str, list], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app, "get_env_config", lambda: FakeEnvConfig(DL_DEVICE="tpu"))
    with pytest.raises(SystemExit):
        app.main()
    assert wired["train"] == []


def test_main_exits_when_no_scenario_matches(wired: dict[str, list], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app, "resolve_scenarios", lambda *_a, **_kw: {})
    with pytest.raises(SystemExit):
        app.main()


def test_main_exits_on_invalid_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    class Required(BaseModel):
        value: int

    def raise_validation_error() -> None:
        try:
            Required.model_validate({})
        except ValidationError as e:
            raise e

    monkeypatch.setattr(app, "configure_logger", lambda: None)
    monkeypatch.setattr(app, "get_env_config", raise_validation_error)
    with pytest.raises(SystemExit) as exc:
        app.main()
    assert exc.value.code == 1


def test_download_main_only_fetches_raw_data(wired: dict[str, list]) -> None:
    app.download_main()
    assert wired["raw"] == ["demo", "demo"]
    assert wired["train"] == []
