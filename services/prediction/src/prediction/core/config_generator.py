"""
config_generator.py
--------------------

Utility to generate a Pydantic Settings model from settings.yaml.
Automates the synchronization of environment variable definitions
with the application's data model and .env.example file.

Author: ai-circus-framework contributors
"""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path
from typing import Any

import yaml
from loguru import logger

DEFAULT_CONFIG = "settings.yaml"
DEFAULT_OUTPUT = "src/prediction/data_model.py"
DEFAULT_ENV_EXAMPLE = ".env.example"


def _profile_names(config: dict[str, Any]) -> list[str]:
    """Return the configured environment profile names (excluding the shared 'base' block)."""
    return [name for name in config.get("environments", {}) if name != "base"]


def update_env_example(config: dict[str, Any], output_path: str | Path) -> None:
    """Generate or update .env.example from config (secrets only)."""
    profiles = ", ".join(_profile_names(config))
    lines = [f"# Active Environment Profile ({profiles})", "APP_ENVIRONMENT=local", ""]

    for var in config.get("env_variables", []):
        if not var.get("secret", False):
            continue

        name = var["name"]
        description = var.get("description", "")

        if description:
            lines.append(f"# {description}")

        lines.append(f"{name}=")
        lines.append("")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines).strip() + "\n")
    logger.info("Updated {}", output_path)


def generate_data_model(
    config_path: str | Path,
    output_path: str | Path,
    env_example_path: str | Path = DEFAULT_ENV_EXAMPLE,
) -> None:
    """Read YAML config and write the Pydantic model file."""
    config_bytes = Path(config_path).read_bytes()
    config = yaml.safe_load(config_bytes)
    # Hash raw bytes (not a text-mode read) so this matches check_env_drift's
    # read_bytes() exactly, regardless of platform line-ending translation.
    yaml_hash = hashlib.sha256(config_bytes).hexdigest()

    global_settings = config.get("global_settings", {})
    env_file = global_settings.get("env_file", ".env")
    case_sensitive = global_settings.get("case_sensitive", True)

    lines = [
        '"""',
        "data_model.py",
        "-----------",
        "Generated Pydantic Settings model from settings.yaml.",
        "DO NOT EDIT DIRECTLY. Run 'make generate-data-model' to update.",
        "",
        "Author: ai-circus-framework contributors",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "import os",
        "import re",
        "from functools import lru_cache",
        "from pathlib import Path",
        "from typing import Any",
        "",
        "import yaml",
        "from pydantic import Field, SecretStr, field_validator",
        "from pydantic_settings import BaseSettings, SettingsConfigDict",
        "",
        "",
        "class EnvConfig(BaseSettings):",
        '    """Environment configuration model."""',
        "",
        "    model_config = SettingsConfigDict(",
        f'        env_file="{env_file}",',
        '        env_file_encoding="utf-8",',
        '        extra="ignore",',
        f"        case_sensitive={case_sensitive},",
        "    )",
    ]

    vars_list = config.get("env_variables", [])
    for var in vars_list:
        name = var["name"]
        is_mandatory = var.get("mandatory", False)
        is_secret = var.get("secret", False)
        default = var.get("default")

        base_type = "SecretStr" if is_secret else "str"
        type_hint = base_type if is_mandatory else f"{base_type} | None"

        description = var.get("description", "").replace('"', '\\"')

        field_args = [f'description="{description}"']
        if not (is_mandatory and default is None):
            if default is None:
                field_args.append("default=None")
            elif isinstance(default, str):
                field_args.append(f'default="{default}"')
            else:
                field_args.append(f"default={default}")

        # format as multi-line if total length would be long
        line_start = f"    {name}: {type_hint} = Field("
        args_str = ", ".join(field_args)
        if len(line_start + args_str + ")") > 80:
            lines.append(line_start)
            for i, arg in enumerate(field_args):
                comma = "," if i < len(field_args) - 1 else ""
                lines.append(f"        {arg}{comma}")
            lines[-1] += "\n    )"
        else:
            lines.append(f"{line_start}{args_str})")

    # Add validators
    for var in vars_list:
        if "validation" in var and "regex" in var["validation"]:
            name = var["name"]
            regex = var["validation"]["regex"]
            err = var["validation"].get("error_message", f"Invalid format for {var['name']}").replace('"', '\\"')
            validator_name = f"validate_{name.lower()}"

            lines.extend([
                "",
                f'    @field_validator("{name}", mode="after")',
                "    @classmethod",
                f"    def {validator_name}(cls, v: Any) -> Any:",
                '        """Validate field format via regex."""',
                "        if v is None:",
                "            return v",
                "        val = v.get_secret_value() if hasattr(v, 'get_secret_value') else str(v)",
                "        if not val:",
                "            return None",
                f'        if not re.match(r"{regex}", val):',
                "            raise ValueError(",
                f'                "{err}"',
                "            )",
                "        return v",
            ])

    lines.extend([
        "",
        "",
        f'_SOURCE_YAML_HASH = "{yaml_hash}"',
        "",
        "",
        "EnvConfig.model_rebuild()",
        "",
        "",
        "def _load_env_overrides(env: str) -> dict[str, Any]:",
        '    """Load per-environment non-secret defaults from settings.yaml.',
        "",
        "    Merges the base non-secret defaults with the profile-specific",
        "    overrides defined under ``environments.<env>`` in settings.yaml.",
        '    """',
        '    config_path = Path(__file__).parent.parent.parent / "settings.yaml"',
        '    with config_path.open(encoding="utf-8") as f:',
        "        data = yaml.safe_load(f)",
        '    base: dict[str, Any] = data.get("environments", {}).get("base", {}).copy()',
        '    base.update(data.get("environments", {}).get(env, {}))',
        "    return base",
        "",
        "",
        "@lru_cache(maxsize=4)",
        "def get_env_config(env: str | None = None) -> EnvConfig:",
        '    """Return the validated environment configuration for the given profile.',
        "",
        "    The active profile is resolved from the *env* argument, then the",
        '    ``APP_ENVIRONMENT`` environment variable, defaulting to ``"local"``.',
        f"    Valid profiles: {', '.join(_profile_names(config))}.",
        '    """',
        '    active_env = env or os.getenv("APP_ENVIRONMENT", "local")',
        "    overrides = _load_env_overrides(active_env)",
        "    return EnvConfig(**overrides)",
        "",
        "",
        "def main() -> None:",
        '    """Display the loaded configuration (redacted)."""',
        "    env_config = get_env_config()",
        '    print("--- Loaded Configuration ---")  # noqa: T201',
        "    for field in EnvConfig.model_fields:",
        "        val = getattr(env_config, field)",
        '        if hasattr(val, "get_secret_value"):',
        '            val = "****" + val.get_secret_value()[-4:] if val and val.get_secret_value() else "None"',
        '        print(f"{field}: {val}")  # noqa: T201',
        "",
        "",
        'if __name__ == "__main__":',
        "    main()",
    ])

    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with output_file.open("w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    logger.info("Generated {} from {}", output_path, config_path)
    update_env_example(config, env_example_path)


def main() -> None:
    """Entry point for the generator tool."""
    parser = argparse.ArgumentParser(description="Generate Pydantic model from YAML.")
    parser.add_argument("--config", default=DEFAULT_CONFIG, help="Path to settings.yaml")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output path for data_model.py")
    parser.add_argument("--env-example", default=DEFAULT_ENV_EXAMPLE, help="Output path for .env.example")
    args = parser.parse_args()

    generate_data_model(args.config, args.output, args.env_example)


def check_env_drift() -> None:
    """Verify that data_model.py is in sync with settings.yaml."""
    config_path = Path(DEFAULT_CONFIG)
    output_path = Path(DEFAULT_OUTPUT)

    if not config_path.exists():
        logger.error("Config file not found: {}", config_path)
        raise SystemExit(1)
    if not output_path.exists():
        logger.error("Data model not found: {}. Run 'make generate-data-model'.", output_path)
        raise SystemExit(1)

    current_hash = hashlib.sha256(config_path.read_bytes()).hexdigest()

    model_content = output_path.read_text(encoding="utf-8")

    match = re.search(r'_SOURCE_YAML_HASH = "([a-f0-9]{64})"', model_content)
    if not match:
        logger.warning("No hash found in {}. Regenerate with 'make generate-data-model'.", output_path)
        raise SystemExit(1)

    embedded_hash = match.group(1)
    if current_hash != embedded_hash:
        logger.error(
            "Drift detected! settings.yaml has changed since last 'make generate-data-model'.\n"
            "  Expected: {}\n  Current:  {}\n"
            "  Fix: run 'make generate-data-model' (or 'make setup' to also re-verify the environment)",
            embedded_hash[:12],
            current_hash[:12],
        )
        raise SystemExit(1)

    logger.info("✓ data_model.py is in sync with settings.yaml")


if __name__ == "__main__":
    main()
