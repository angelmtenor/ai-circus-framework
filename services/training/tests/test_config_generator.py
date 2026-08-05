"""
test_config_generator.py
-------------------------

Tests for the data model generator and the resulting Pydantic model.

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml
from pydantic import SecretStr

from training.core.config_generator import generate_data_model


@pytest.fixture
def temp_config(tmp_path: Path) -> Path:
    """Create a temporary env_config.yaml for testing."""
    config_data = {
        "env_variables": [
            {
                "name": "TEST_VAR_STRING",
                "description": "A test string variable",
                "type": "string",
                "mandatory": True,
                "secret": False,
                "default": "default_val",
            },
            {
                "name": "TEST_VAR_SECRET",
                "description": "A test secret variable",
                "type": "string",
                "mandatory": False,
                "secret": True,
                "validation": {
                    "regex": "^[A-Z]{3}$",
                    "error_message": "Must be 3 uppercase letters",
                },
            },
        ],
        "global_settings": {
            "env_file": ".env.test",
            "case_sensitive": True,
        },
    }
    config_path = tmp_path / "env_config_test.yaml"
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(config_data, f)
    return config_path


def test_generation_logic(temp_config: Path, tmp_path: Path) -> None:
    """Test that the generator produces valid Python code with expected patterns."""
    output_path = tmp_path / "generated_model.py"
    env_example_path = tmp_path / ".env.example.test"
    generate_data_model(temp_config, output_path, env_example_path)

    assert output_path.exists()
    assert env_example_path.exists()
    content = output_path.read_text()

    # Check for core components
    assert "class EnvConfig(BaseSettings):" in content
    assert "TEST_VAR_STRING: str = Field(" in content
    assert "TEST_VAR_SECRET: SecretStr | None = Field(" in content
    assert 'env_file=".env.test"' in content
    assert '@field_validator("TEST_VAR_SECRET", mode="after")' in content
    assert 'r"^[A-Z]{3}$"' in content


def test_generated_model_functionality(temp_config: Path, tmp_path: Path) -> None:
    """Test the behavior of the generated model by importing it dynamically."""
    output_path = tmp_path / "generated_model.py"
    env_example_path = tmp_path / ".env.example.test"
    generate_data_model(temp_config, output_path, env_example_path)

    # Dynamic import of the generated file
    import importlib.util

    spec = importlib.util.spec_from_file_location("dynamic_model", output_path)
    if spec is None or spec.loader is None:
        pytest.fail("Could not load generated model")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # 1. Test default value
    config = module.EnvConfig(_env_file=None)  # type: ignore[attr-defined]
    assert config.TEST_VAR_STRING == "default_val"
    assert config.TEST_VAR_SECRET is None

    # 2. Test valid secret with validation
    config_valid = module.EnvConfig(TEST_VAR_SECRET="ABC", _env_file=None)  # type: ignore[attr-defined] # ruff: ignore[hardcoded-password-func-arg]
    assert isinstance(config_valid.TEST_VAR_SECRET, SecretStr)
    assert config_valid.TEST_VAR_SECRET.get_secret_value() == "ABC"

    # 3. Test invalid secret (regex mismatch)
    with pytest.raises(ValueError, match="Must be 3 uppercase letters"):
        module.EnvConfig(TEST_VAR_SECRET="abc", _env_file=None)  # type: ignore[attr-defined] # ruff: ignore[hardcoded-password-func-arg]

    # 4. Test mandatory field missing (if we removed the default in YAML)
    # Our test YAML has a default, so it's not missing.
    # Let's verify environment variable override.
    os.environ["TEST_VAR_STRING"] = "env_override"
    config_env = module.EnvConfig(_env_file=None)  # type: ignore[attr-defined]
    assert config_env.TEST_VAR_STRING == "env_override"
    del os.environ["TEST_VAR_STRING"]
