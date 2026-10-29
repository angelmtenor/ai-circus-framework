"""
- Title:    Lineage of a data product — sources → bronze → silver → gold → metrics
- Author:   Angel Martinez-Tenor

Built from what the platform already knows, nothing hand-drawn: the capture side from the
scenario's `data_product.sources` (topic / landing prefix / CDC table → its bronze table), the
transformation side from the dbt manifest data-transform publishes after every run
(`tenant-platform/dbt/<slug>/postgres/manifest.json`: models, their sources/refs and tests),
and the serving side from the declared metrics. OpenMetadata (governance tier) ingests the same
manifest for the enterprise-grade view; this one is always available with the core tier.
"""

from __future__ import annotations

import json
import time
from typing import Any

from ai_circus_shared.data_product import CdcSource, FileSource, KafkaSource, bronze_table
from ai_circus_shared.storage import ObjectStore

from data_api.core.products import DataProduct

PLATFORM_ORG = "platform"
_CACHE_TTL = 60.0
_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}


def _manifest(store: ObjectStore | None, slug: str) -> dict[str, Any] | None:
    cached = _cache.get(slug)
    if cached is not None and time.monotonic() - cached[0] < _CACHE_TTL:
        return cached[1]
    manifest = None
    if store is not None:
        try:
            manifest = json.loads(store.get(PLATFORM_ORG, f"dbt/{slug}/postgres/manifest.json"))
        except Exception:
            manifest = None
    _cache[slug] = (time.monotonic(), manifest)
    return manifest


def build(product: DataProduct, store: ObjectStore | None) -> dict[str, Any]:
    """Nodes and edges from the sources, the dbt manifest and the metrics (see module docstring)."""
    nodes: dict[str, dict[str, Any]] = {}
    edges: set[tuple[str, str]] = set()

    def node(node_id: str, label: str, kind: str, layer: str, **extra: Any) -> str:
        nodes.setdefault(node_id, {"id": node_id, "label": label, "kind": kind, "layer": layer, **extra})
        return node_id

    for source in product.config.sources:
        if isinstance(source, KafkaSource):
            topic = node(f"topic:{source.topic}", source.topic, "topic", "source", description=source.label)
            edges.add((topic, node(f"bronze:{bronze_table(source.id)}", bronze_table(source.id), "table", "bronze")))
        elif isinstance(source, FileSource):
            files = node(f"files:{source.id}", f"landing/{source.prefix}", "files", "source", description=source.label)
            edges.add((
                files,
                node(f"bronze:{bronze_table(source.lands_in)}", bronze_table(source.lands_in), "table", "bronze"),
            ))
        elif isinstance(source, CdcSource):
            for table in source.tables:
                origin = node(f"cdc:{table.name}", f"erp.{table.name}", "cdc", "source", description=table.label)
                edges.add((
                    origin,
                    node(f"bronze:bronze_cdc_{table.name}", f"bronze_cdc_{table.name}", "table", "bronze"),
                ))
                edges.add((origin, node(f"bronze:cdc_{table.name}", f"cdc_{table.name}", "table", "bronze")))

    manifest = _manifest(store, product.slug)
    model_ids: dict[str, str] = {}
    if manifest:
        tests: dict[str, int] = {}
        for test in manifest.get("nodes", {}).values():
            if test.get("resource_type") == "test":
                for parent in test.get("depends_on", {}).get("nodes", []):
                    tests[parent] = tests.get(parent, 0) + 1
        for uid, model in manifest.get("nodes", {}).items():
            # Seeds are reference data kept as code (e.g. store coordinates), shown next to models.
            if model.get("resource_type") not in ("model", "seed"):
                continue
            layer = "gold" if str(model.get("schema", "")).endswith("_gold") else "silver"
            model_ids[uid] = node(
                f"{layer}:{model['name']}",
                model["name"],
                model["resource_type"],
                layer,
                description=(model.get("description") or "")[:200],
                tests=tests.get(uid, 0),
            )
        for uid, model in manifest.get("nodes", {}).items():
            if uid not in model_ids:
                continue
            for parent in model.get("depends_on", {}).get("nodes", []):
                if parent in model_ids:
                    edges.add((model_ids[parent], model_ids[uid]))
                elif parent.startswith("source.") and ".bronze." in parent:
                    table = parent.rsplit(".", 1)[-1]
                    edges.add((node(f"bronze:{table}", table, "table", "bronze"), model_ids[uid]))
    known = {n["label"]: n_id for n_id, n in nodes.items() if n["layer"] in ("silver", "gold")}
    for metric in product.config.metrics:
        dataset = known.get(metric.dataset)
        if dataset:
            edges.add((
                dataset,
                node(f"metric:{metric.name}", metric.label, "metric", "serve", description=metric.description),
            ))
    return {
        "nodes": list(nodes.values()),
        "edges": [{"from": a, "to": b} for a, b in sorted(edges)],
        "generated_at": (manifest or {}).get("metadata", {}).get("generated_at"),
    }
