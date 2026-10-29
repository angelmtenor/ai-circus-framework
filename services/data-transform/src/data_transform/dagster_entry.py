"""
- Title:    Dagster code location entry point (`dagster ... -m data_transform.dagster_entry`)
- Author:   Angel Martinez-Tenor

Builds the shared environment (databases, profiles, clients) once per Dagster process and
exposes `defs`; everything testable lives in data_transform.dagster_defs.
"""

from __future__ import annotations

from data_transform import get_env_config
from data_transform.app import after_run, prepare_environment
from data_transform.core.logger import configure_logger
from data_transform.dagster_defs import build_definitions

configure_logger()
defs = build_definitions(prepare_environment(get_env_config()), after_run)
