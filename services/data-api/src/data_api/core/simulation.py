"""
- Title:    Simulator runtime — a data product's source systems, driven from the API
- Author:   Angel Martinez-Tenor

Hosts each data product's simulator plugin (docs/adr/0013) per tenant org, behind the platform's
own capture interfaces: `KafkaSink` publishes to the source's tenant topic, `LandingSink` drops
deliveries into the landing bucket, `SourcesDb` is the operational source database CDC reads.
The UI's Simulate panel calls `act`; `backfill` (history) and `live` (a background trickle) run
as threads with their progress kept here. Nothing in this module knows what a receipt is.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ai_circus_shared.contracts import get_one
from ai_circus_shared.data_simulator import Simulator, SimulatorContext, load_simulator
from ai_circus_shared.events import tenant_topic
from ai_circus_shared.storage import ObjectStore
from confluent_kafka import Producer
from sqlalchemy import Engine, text

from data_api.core.logger import get_logger
from data_api.core.products import DataProduct

logger = get_logger(__name__)

LIVE_TICK_SECONDS = 1.0
MAX_LIVE_SECONDS = 4 * 3600  # a forgotten live mode stops by itself


class KafkaSink:
    """Publishes simulator records to the source's tenant topic."""

    def __init__(self, producer: Producer, product: DataProduct, org_id: str) -> None:
        """Bind to one data product and tenant."""
        self._producer, self._product, self._org = producer, product, org_id

    def publish(self, source_id: str, record: dict[str, Any], *, event_time: datetime | None = None) -> None:
        """Produce one record keyed by its business key (waits while the local queue is full)."""
        source = self._product.config.source(source_id)
        key = str(get_one(record, source.business_key) or "")
        headers: list[tuple[str, str | bytes | None]] = [
            ("contract_version", str(record.get("contract_version", ""))),
            ("trace_id", uuid.uuid4().hex),
        ]
        while True:
            try:
                self._producer.produce(
                    tenant_topic(self._org, source.topic),
                    json.dumps(record).encode(),
                    key=key.encode(),
                    headers=headers,
                )
                break
            except BufferError:
                self._producer.poll(0.2)  # local queue full during a backfill: wait for it to drain
        self._producer.poll(0)

    def flush(self) -> None:
        """Wait for queued records to be delivered."""
        self._producer.flush(30)


class LandingSink:
    """Drops simulator file deliveries into the landing bucket."""

    def __init__(self, store: ObjectStore, product: DataProduct, org_id: str) -> None:
        """Bind to one data product and tenant."""
        self._store, self._product, self._org = store, product, org_id

    def drop(self, source_id: str, delivery: str, records: list[dict[str, Any]], control: dict[str, Any]) -> str:
        """Write a delivery's data file, then its control file; returns the delivery folder."""
        source = next(s for s in self._product.config.file_sources() if s.id == source_id)
        folder = f"landing/{self._product.slug}/{source.prefix}{delivery}"
        body = "\n".join(json.dumps(r, ensure_ascii=False) for r in records).encode()
        self._store.put(self._org, f"{folder}/{source.data_file}", body)
        # The control file last: data-ingest only picks up a delivery once its control file exists.
        self._store.put(self._org, f"{folder}/{source.control.file_name}", json.dumps(control).encode())
        return folder


class SourcesDb:
    """The operational source database, as the simulator plugin protocol sees it."""

    def __init__(self, engine: Engine) -> None:
        """Wrap the sources engine."""
        self._engine = engine

    def execute(self, sql: str, params: dict[str, Any] | list[dict[str, Any]] | None = None) -> list[tuple]:
        """Run one statement (or executemany) in its own transaction; rows if it returns any."""
        with self._engine.begin() as conn:
            result = conn.execute(text(sql), params or {})
            return [tuple(row) for row in result.all()] if result.returns_rows else []


@dataclass
class TenantSimulation:
    """One tenant's simulator context, its live thread and its backfill progress."""

    ctx: SimulatorContext
    lock: threading.Lock = field(default_factory=threading.Lock)
    ready: bool = False
    live_until: float = 0.0
    live_thread: threading.Thread | None = None
    live_produced: int = 0
    backfill: dict[str, Any] = field(default_factory=lambda: {"state": "idle"})
    last_actions: list[dict[str, Any]] = field(default_factory=list)


class SimulationManager:
    """Hosts the simulator plugins, one context per (data product, tenant)."""

    def __init__(
        self, products: dict[str, DataProduct], producer: Producer, store: ObjectStore, sources: Engine
    ) -> None:
        """Share one producer, store and sources engine across tenants."""
        self._products, self._producer, self._store = products, producer, store
        self._db = SourcesDb(sources)
        self._plugins: dict[str, Simulator] = {}
        self._tenants: dict[tuple[str, str], TenantSimulation] = {}
        self._lock = threading.Lock()

    def _plugin(self, product: DataProduct) -> Simulator:
        config = product.config.simulator
        if config is None:
            raise LookupError(f"{product.slug} ships no simulator.")
        if product.slug not in self._plugins:
            self._plugins[product.slug] = load_simulator(product.scenario_dir, config.package, config.seed)
        return self._plugins[product.slug]

    def tenant(self, product: DataProduct, org_id: str) -> TenantSimulation:
        """The tenant's simulation state (created on first use)."""
        key = (product.slug, org_id)
        with self._lock:
            sim = self._tenants.get(key)
            if sim is None:
                ctx = SimulatorContext(
                    org_id=org_id,
                    slug=product.slug,
                    config=product.config,
                    records=KafkaSink(self._producer, product, org_id),
                    files=LandingSink(self._store, product, org_id),
                    ops=self._db,
                )
                sim = self._tenants[key] = TenantSimulation(ctx)
            return sim

    def _ensure_ready(self, product: DataProduct, sim: TenantSimulation) -> Simulator:
        plugin = self._plugin(product)
        if not sim.ready:
            plugin.setup(sim.ctx)
            sim.ready = True
        return plugin

    def status(self, product: DataProduct, org_id: str) -> dict[str, Any]:
        """Live mode, backfill progress and the last actions."""
        sim = self.tenant(product, org_id)
        live = sim.live_thread is not None and sim.live_thread.is_alive()
        return {
            "available": product.config.simulator is not None,
            "live": live,
            "live_until": datetime.fromtimestamp(sim.live_until, UTC).isoformat() if live else None,
            "live_produced": sim.live_produced,
            "backfill": dict(sim.backfill),
            "last_actions": list(sim.last_actions),
        }

    def act(self, product: DataProduct, org_id: str, action: str, params: dict[str, Any]) -> dict[str, Any]:
        """Run one declared action with only its declared parameters."""
        config = product.config.simulator
        declared = {a.name: a for a in (config.actions if config else [])}
        if action not in declared:
            raise LookupError(f"Unknown simulator action {action!r}.")
        types = {p.name: p.type for p in declared[action].params}
        clean = {}
        for name, value in params.items():
            kind = types.get(name)
            if kind is None or value is None:
                continue
            if (kind == "items") != isinstance(value, list):
                raise ValueError(f"Parameter {name!r} must be {'a list of items' if kind == 'items' else 'a value'}.")
            if kind == "items" and len(value) > 20:
                raise ValueError(f"Parameter {name!r} holds at most 20 items.")
            clean[name] = value
        sim = self.tenant(product, org_id)
        with sim.lock:
            plugin = self._ensure_ready(product, sim)
            result = plugin.act(sim.ctx, action, clean, datetime.now(UTC))
        entry = {"action": action, "at": datetime.now(UTC).isoformat(), **result}
        sim.last_actions = [entry, *sim.last_actions][:10]
        return entry

    def start_backfill(self, product: DataProduct, org_id: str, days: int | None) -> dict[str, Any]:
        """Start a backfill thread (no-op while one runs); returns its status."""
        sim = self.tenant(product, org_id)
        if sim.backfill.get("state") == "running":
            return dict(sim.backfill)
        config = product.config.simulator
        n_days = max(1, min(days or (config.history_days if config else 30), 2000))
        sim.backfill = {"state": "running", "days": n_days, "started_at": datetime.now(UTC).isoformat(), "progress": []}

        def run() -> None:
            try:
                with sim.lock:
                    plugin = self._ensure_ready(product, sim)
                for line in plugin.backfill(sim.ctx, n_days, datetime.now(UTC)):
                    sim.backfill["progress"] = [*sim.backfill["progress"], line][-20:]
                sim.ctx.records.flush()
                sim.backfill.update(state="done", finished_at=datetime.now(UTC).isoformat())
            except Exception as exc:
                logger.exception("Backfill of {} for {} failed", product.slug, org_id)
                sim.backfill.update(state="failed", error=str(exc)[:300])

        threading.Thread(target=run, name=f"backfill-{product.slug}-{org_id}", daemon=True).start()
        return dict(sim.backfill)

    def set_live(self, product: DataProduct, org_id: str, on: bool, minutes: int) -> dict[str, Any]:
        """Turn live trading on for `minutes`, or off."""
        sim = self.tenant(product, org_id)
        if not on:
            sim.live_until = 0.0
            return self.status(product, org_id)
        sim.live_until = time.time() + max(1, min(minutes * 60, MAX_LIVE_SECONDS))
        if sim.live_thread is None or not sim.live_thread.is_alive():
            sim.live_thread = threading.Thread(
                target=self._live_loop, args=(product, sim), name=f"live-{product.slug}-{sim.ctx.org_id}", daemon=True
            )
            sim.live_thread.start()
        return self.status(product, org_id)

    def _live_loop(self, product: DataProduct, sim: TenantSimulation) -> None:
        last = time.monotonic()
        while time.time() < sim.live_until:
            time.sleep(LIVE_TICK_SECONDS)
            now = time.monotonic()
            try:
                with sim.lock:
                    plugin = self._ensure_ready(product, sim)
                    sim.live_produced += plugin.live_tick(sim.ctx, datetime.now(UTC), now - last)
            except Exception:
                logger.exception("Live tick of {} failed", product.slug)
            last = now

    def stop_all(self) -> None:
        """Stop every live trickle and flush the producer."""
        for sim in self._tenants.values():
            sim.live_until = 0.0
        self._producer.flush(5)
