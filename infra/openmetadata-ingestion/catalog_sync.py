"""
- Title:    Catalogue sync — every data product into OpenMetadata, end to end
- Author:   Angel Martinez-Tenor

The governance tier's Job (`make k3s-data-governance-up`, docs/adr/0010). For every
`kind: data_product` scenario it runs OpenMetadata's own connectors as one-shot workflows —
no Airflow — and then adds what no connector can infer:

1. services and metadata: the warehouse (Postgres: bronze / silver / gold / ops schemas), the
   operational source database (the ERP the simulator plays), Kafka (every tenant topic), the
   ClickHouse marts (analytics tier) and Superset (bi tier) when they run;
2. dbt: models, descriptions, tests and their results, and model-to-model lineage, read from
   the artifacts data-transform publishes to the object store after every build;
3. the data product's contracts as its topics' message schemas;
4. capture lineage: topic → bronze table (data-ingest) and ERP table → CDC tables;
5. a Domain and a Data Product owning the curated tables, so the catalogue shows the product,
   not just tables.

Reads scenario.yaml directly (this image runs Python 3.11, without the services' shared
library). Every secret comes from the environment; nothing is printed.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests
import yaml

OM = os.environ.get("OPENMETADATA_URL", "http://openmetadata:8585").rstrip("/")
SCENARIOS = Path(os.environ.get("SCENARIOS_DIR", "/app/scenarios"))
WAREHOUSE_SERVICE = "data_platform_warehouse"
SOURCES_SERVICE = "data_platform_sources"
KAFKA_SERVICE = "data_platform_kafka"
CLICKHOUSE_SERVICE = "data_platform_clickhouse"
SUPERSET_SERVICE = "data_platform_superset"
BUCKET = "ai-circus-data"


def log(message: str) -> None:
    print(f"[catalog-sync] {message}", flush=True)


class OpenMetadata:
    """The REST calls this sync needs, as the admin."""

    def __init__(self) -> None:
        for _ in range(90):
            try:
                if requests.get(f"{OM}/api/v1/system/version", timeout=5).ok:
                    break
            except requests.RequestException:
                pass
            time.sleep(5)
        password = base64.b64encode(os.environ.get("OPENMETADATA_ADMIN_PASSWORD", "admin").encode()).decode()
        response = requests.post(f"{OM}/api/v1/users/login", timeout=30, json={
            "email": os.environ.get("OPENMETADATA_ADMIN_EMAIL", "admin@open-metadata.org"), "password": password})
        response.raise_for_status()
        self.token = response.json()["accessToken"]
        self.headers = {"Authorization": f"Bearer {self.token}"}

    def get(self, path: str, **params: Any) -> dict[str, Any] | None:
        response = requests.get(f"{OM}/api/v1/{path}", headers=self.headers, params=params, timeout=60)
        return response.json() if response.ok else None

    def put(self, path: str, body: dict[str, Any]) -> dict[str, Any] | None:
        response = requests.put(f"{OM}/api/v1/{path}", headers=self.headers, json=body, timeout=60)
        if not response.ok:
            log(f"PUT {path}: {response.status_code} {response.text[:200]}")
            return None
        # PUT /lineage answers 200 with an empty body.
        return response.json() if response.content else {}

    def patch(self, path: str, operations: list[dict[str, Any]]) -> bool:
        response = requests.patch(f"{OM}/api/v1/{path}", timeout=60, data=json.dumps(operations),
                                  headers={**self.headers, "Content-Type": "application/json-patch+json"})
        if not response.ok:
            log(f"PATCH {path}: {response.status_code} {response.text[:200]}")
        return response.ok


def run_workflow(config: dict[str, Any], kind: str = "metadata") -> None:
    """One OpenMetadata ingestion workflow, in-process."""
    if kind == "metadata":
        from metadata.workflow.metadata import MetadataWorkflow as Workflow
    else:
        raise ValueError(kind)
    name = config["source"]["serviceName"]
    try:
        workflow = Workflow.create(config)
        workflow.execute()
        workflow.print_status()
        workflow.stop()
        log(f"workflow {name} ({config['source']['type']}): done")
    except Exception as exc:  # noqa: BLE001 — one failing connector must not stop the others
        log(f"workflow {name} ({config['source']['type']}) failed: {exc}")


def server_config(token: str) -> dict[str, Any]:
    return {"openMetadataServerConfig": {"hostPort": f"{OM}/api", "authProvider": "openmetadata",
                                         "securityConfig": {"jwtToken": token}}}


def postgres_workflow(token: str, service: str, database: str, schemas: list[str]) -> dict[str, Any]:
    return {
        "source": {
            "type": "postgres", "serviceName": service,
            "serviceConnection": {"config": {
                "type": "Postgres", "username": os.environ["POSTGRES_USER"],
                "authType": {"password": os.environ["POSTGRES_PASSWORD"]}, "hostPort": "postgres:5432",
                "database": database}},
            "sourceConfig": {"config": {"type": "DatabaseMetadata", "includeViews": True,
                                        "schemaFilterPattern": {"includes": [f"^{s}$" for s in schemas]}}},
        },
        "sink": {"type": "metadata-rest", "config": {}},
        "workflowConfig": server_config(token),
    }


def dbt_workflow(token: str, service: str, slug: str, target: str) -> dict[str, Any]:
    return {
        "source": {
            "type": "dbt", "serviceName": service,
            "sourceConfig": {"config": {
                "type": "DBT", "dbtUpdateDescriptions": True, "includeTags": True,
                "dbtConfigSource": {
                    "dbtConfigType": "s3",
                    "dbtSecurityConfig": {"awsAccessKeyId": os.environ["OBJECT_STORE_ACCESS_KEY"],
                                          "awsSecretAccessKey": os.environ["OBJECT_STORE_SECRET_KEY"],
                                          "awsRegion": "us-east-1",
                                          "endPointURL": os.environ.get("OBJECT_STORE_ENDPOINT", "http://seaweedfs:8333")},
                    "dbtPrefixConfig": {"dbtBucketName": BUCKET,
                                        "dbtObjectPrefix": f"tenant-platform/dbt/{slug}/{target}"},
                }}},
        },
        "sink": {"type": "metadata-rest", "config": {}},
        "workflowConfig": server_config(token),
    }


def kafka_workflow(token: str) -> dict[str, Any]:
    return {
        "source": {
            "type": "kafka", "serviceName": KAFKA_SERVICE,
            "serviceConnection": {"config": {"type": "Kafka", "bootstrapServers": "kafka:9092"}},
            "sourceConfig": {"config": {"type": "MessagingMetadata",
                                        "topicFilterPattern": {"includes": ["^tenant-.*"]}}},
        },
        "sink": {"type": "metadata-rest", "config": {}},
        "workflowConfig": server_config(token),
    }


def clickhouse_workflow(token: str, schemas: list[str]) -> dict[str, Any]:
    return {
        "source": {
            "type": "clickhouse", "serviceName": CLICKHOUSE_SERVICE,
            "serviceConnection": {"config": {
                "type": "Clickhouse", "username": "clickhouse", "password": os.environ.get("CLICKHOUSE_PASSWORD", ""),
                "hostPort": "clickhouse:8123"}},
            "sourceConfig": {"config": {"type": "DatabaseMetadata",
                                        "schemaFilterPattern": {"includes": [f"^{s}$" for s in schemas]}}},
        },
        "sink": {"type": "metadata-rest", "config": {}},
        "workflowConfig": server_config(token),
    }


def superset_workflow(token: str, db_services: list[str]) -> dict[str, Any]:
    return {
        "source": {
            "type": "superset", "serviceName": SUPERSET_SERVICE,
            "serviceConnection": {"config": {
                "type": "Superset", "hostPort": "http://superset:8088",
                "connection": {"provider": "db", "username": "admin",
                               "password": os.environ.get("SUPERSET_ADMIN_PASSWORD", "")}}},
            "sourceConfig": {"config": {"type": "DashboardMetadata", "lineageInformation": {
                "dbServicePrefixes": db_services}}},
        },
        "sink": {"type": "metadata-rest", "config": {}},
        "workflowConfig": server_config(token),
    }


def reachable(url: str) -> bool:
    try:
        return requests.get(url, timeout=5).status_code < 500
    except requests.RequestException:
        return False


def entity(om: OpenMetadata, kind: str, fqn: str) -> dict[str, Any] | None:
    return om.get(f"{kind}/name/{requests.utils.quote(fqn, safe='')}")


def add_lineage(om: OpenMetadata, source: tuple[str, dict[str, Any]], target: tuple[str, dict[str, Any]],
                description: str) -> None:
    om.put("lineage", {"edge": {"fromEntity": {"id": source[1]["id"], "type": source[0]},
                                "toEntity": {"id": target[1]["id"], "type": target[0]},
                                "lineageDetails": {"description": description}}})


def enrich(om: OpenMetadata, slug: str, product: dict[str, Any], scenario_dir: Path) -> None:
    """Contracts as topic schemas, capture lineage, the Domain and the Data Product."""
    topics = (om.get("topics", service=KAFKA_SERVICE, limit=1000) or {}).get("data", [])
    for source in product["sources"]:
        bronze_fqn = f"{WAREHOUSE_SERVICE}.data_platform.{slug}.bronze_{source.get('lands_in', source['id'])}"
        bronze = entity(om, "tables", bronze_fqn)
        if source["type"] == "kafka":
            contract = (scenario_dir / source["contract"]).read_text()
            for topic in (t for t in topics if t["name"].endswith(f".{source['topic']}")):
                om.patch(f"topics/{topic['id']}", [{"op": "add", "path": "/messageSchema", "value": {
                    "schemaText": contract, "schemaType": "JSON"}}, {"op": "add", "path": "/description",
                    "value": f"{source['label']} — contract {source['contract']} (tenant topic)"}])
                if bronze:
                    add_lineage(om, ("topic", topic), ("table", bronze),
                                "data-ingest: JSON Schema + record rules, idempotent upsert by business key; "
                                "rejects go to ops_quarantine")
        elif source["type"] == "cdc":
            for table in source["tables"]:
                origin = entity(om, "tables", f"{SOURCES_SERVICE}.data_sources.{slug}.{table['name']}")
                for name in (f"bronze_cdc_{table['name']}", f"cdc_{table['name']}"):
                    target = entity(om, "tables", f"{WAREHOUSE_SERVICE}.data_platform.{slug}.{name}")
                    if origin and target:
                        add_lineage(om, ("table", origin), ("table", target),
                                    "data-ingest: logical replication (CDC) — change log and current state")
    domain_name = product.get("domain", slug).capitalize()
    om.put("domains", {"name": domain_name, "displayName": domain_name, "domainType": "Consumer-aligned",
                       "description": f"Business domain of the `{slug}` data product (AI Open Framework)."})
    assets = []
    for dataset in product["datasets"]:
        schema = {"silver": f"{slug}_silver", "gold": f"{slug}_gold"}.get(dataset["layer"], slug)
        table = entity(om, "tables", f"{WAREHOUSE_SERVICE}.data_platform.{schema}.{dataset['name']}")
        if table:
            assets.append({"id": table["id"], "type": "table"})
            om.patch(f"tables/{table['id']}", [{"op": "add", "path": "/description", "value": (
                f"{dataset['label']} — {dataset['description']}. Grain: {dataset['grain']}.")}])
    om.put("dataProducts", {"name": slug, "displayName": slug.replace("_", " ").title(),
                            "domains": [domain_name], "assets": assets,
                            "description": "Governed data product: curated datasets, metrics and access rules "
                                           "declared in its scenario.yaml."})
    log(f"{slug}: contracts, lineage, domain {domain_name} and data product with {len(assets)} assets")


def main() -> int:
    om = OpenMetadata()
    token = om.token
    products = []
    for path in sorted(SCENARIOS.glob("*/scenario.yaml")):
        raw = yaml.safe_load(path.read_text())
        if isinstance(raw, dict) and raw.get("kind") == "data_product":
            products.append((raw["slug"], raw["data_product"], path.parent))
    if not products:
        log("no data_product scenario found")
        return 0
    slugs = [slug for slug, _, _ in products]
    warehouse_schemas = [s for slug in slugs for s in (slug, f"{slug}_silver", f"{slug}_gold")]
    run_workflow(postgres_workflow(token, WAREHOUSE_SERVICE, "data_platform", warehouse_schemas))
    run_workflow(postgres_workflow(token, SOURCES_SERVICE, "data_sources", slugs))
    run_workflow(kafka_workflow(token))
    for slug in slugs:
        run_workflow(dbt_workflow(token, WAREHOUSE_SERVICE, slug, "postgres"))
    db_services = [WAREHOUSE_SERVICE]
    if reachable("http://clickhouse:8123/ping") and os.environ.get("CLICKHOUSE_PASSWORD"):
        run_workflow(clickhouse_workflow(token, [s for slug in slugs for s in (f"{slug}_silver", f"{slug}_gold")]))
        for slug in slugs:
            run_workflow(dbt_workflow(token, CLICKHOUSE_SERVICE, slug, "clickhouse"))
        db_services.append(CLICKHOUSE_SERVICE)
    if reachable("http://superset:8088/health"):
        run_workflow(superset_workflow(token, db_services))
    for slug, product, scenario_dir in products:
        enrich(om, slug, product, scenario_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
