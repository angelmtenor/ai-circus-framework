"""
test_env_profiles.py
--------------------

Tests for the environment-aware configuration loading.

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import pytest

from dl_training.data_model import get_env_config


@pytest.fixture(autouse=True)
def _prepare_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear the lru_cache and set the mandatory fields with no profile default."""
    get_env_config.cache_clear()
    monkeypatch.setenv("OBJECT_STORE_SECRET_KEY", "test-secret")
    for name in ("SCENARIOS", "DL_DEVICE", "OBJECT_STORE_ENDPOINT", "MLFLOW_TRACKING_URI"):
        monkeypatch.delenv(name, raising=False)


def test_local_profile_leaves_the_endpoint_to_the_host_script(monkeypatch: pytest.MonkeyPatch) -> None:
    """scripts/dl_train_host.sh exports OBJECT_STORE_ENDPOINT — the profile must not
    set one (a profile value would beat that env var).
    """
    monkeypatch.delenv("APP_ENVIRONMENT", raising=False)
    monkeypatch.setenv("OBJECT_STORE_ENDPOINT", "http://127.0.0.1:18333")

    config = get_env_config()

    assert config.OBJECT_STORE_ENDPOINT == "http://127.0.0.1:18333"
    assert config.SCENARIOS_DIR == "../../scenarios"
    assert config.DL_CACHE_DIR == "~/.cache/ai-circus/dl-data"
    assert config.ORG_ID == "demo"


def test_docker_profile() -> None:
    config = get_env_config(env="docker")
    assert config.SCENARIOS_DIR == "/app/scenarios"
    assert config.OBJECT_STORE_ENDPOINT == "http://seaweedfs:8333"
    assert config.DL_CACHE_DIR == "/tmp/dl-cache"  # ruff: ignore[hardcoded-temp-file]


def test_per_run_env_vars_are_not_shadowed_by_a_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """SCENARIOS/DL_DEVICE are exactly what each run sets (make dl-train-nlp, the admin
    console's per-scenario Job) — no profile default may beat them.
    """
    monkeypatch.setenv("SCENARIOS", "symptom_triage")
    monkeypatch.setenv("DL_DEVICE", "cpu")

    config = get_env_config(env="docker")

    assert config.SCENARIOS == "symptom_triage"
    assert config.DL_DEVICE == "cpu"


def test_optional_fields_default_to_none() -> None:
    config = get_env_config(env="docker")
    assert config.SCENARIOS is None
    assert config.DL_DEVICE is None
    assert config.MLFLOW_TRACKING_URI is None
