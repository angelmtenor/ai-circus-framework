"""
- Title:    Data product simulator plugin protocol
- Author:   Angel Martinez-Tenor

A data product ships its synthetic source systems as a Python package next to its
scenario.yaml (`data_product.simulator.package`, see docs/adr/0011). The generic data-tier
services load it with `load_simulator` and drive it through `SimulatorContext`, whose sinks are
the platform's *own* capture interfaces — a Kafka topic per source (tenant-scoped), the landing
bucket, the operational source database. A simulator therefore exercises the real pipeline end
to end and never writes bronze, silver or gold itself.

A plugin module exposes one factory, `create(seed: int) -> Simulator`.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol

from ai_circus_shared.data_product import DataProductConfig


class RecordSink(Protocol):
    def publish(self, source_id: str, record: dict[str, Any], *, event_time: datetime | None = None) -> None:
        """Publish one record to the kafka source's tenant-scoped topic."""
        ...

    def flush(self) -> None: ...


class FileSink(Protocol):
    def drop(self, source_id: str, delivery: str, records: list[dict[str, Any]], control: dict[str, Any]) -> str:
        """Write one delivery (data file + control file) under the file source's landing
        prefix; returns the delivery's object key prefix."""
        ...


class OperationalDb(Protocol):
    """The data product's operational source database (schema = slug, in `data_sources`)."""

    def execute(self, sql: str, params: dict[str, Any] | list[dict[str, Any]] | None = None) -> list[tuple]: ...


@dataclass
class SimulatorContext:
    org_id: str
    slug: str
    config: DataProductConfig
    records: RecordSink
    files: FileSink
    ops: OperationalDb
    # Anything the plugin wants to keep between calls in one process (its last receipt…).
    state: dict[str, Any] = field(default_factory=dict)


class Simulator(Protocol):
    def setup(self, ctx: SimulatorContext) -> None:
        """Create and seed the operational source tables (idempotent)."""
        ...

    def backfill(self, ctx: SimulatorContext, days: int, end: datetime) -> Iterator[str]:
        """Generate `days` of history ending at `end`; yields progress lines."""
        ...

    def live_tick(self, ctx: SimulatorContext, now: datetime, seconds: float) -> int:
        """Produce what `seconds` of live trading at `now` would; returns records produced."""
        ...

    def act(self, ctx: SimulatorContext, action: str, params: dict[str, Any], now: datetime) -> dict[str, Any]:
        """Run one declared `simulator.actions` entry; returns what to show (record ids…)."""
        ...


def load_simulator(scenario_dir: Path, package: str, seed: int) -> Simulator:
    """Import `<scenario_dir>/<package>/__init__.py` as an isolated module and build it.

    The module is registered under a slug-qualified name so two data products' simulators
    (each with its own relative imports) never collide in `sys.modules`.
    """
    init = (scenario_dir / package / "__init__.py").resolve()
    if not init.is_file() or scenario_dir.resolve() not in init.parents:
        raise FileNotFoundError(f"simulator package {package!r} not found under {scenario_dir}")
    name = f"_data_product_simulator_{scenario_dir.name}"
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, init, submodule_search_locations=[str(init.parent)])
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load simulator {init}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module.create(seed)
