#!/usr/bin/env bash
# Drop everything one data product holds on the k3s cluster — bronze/silver/gold/ops schemas,
# its Iceberg tables, landing deliveries, its operational source (ERP) schema, its Kafka topics
# and the CDC slot — so the next `make k3s-data-seed` starts from nothing. Every tenant's data
# of that product goes. Secrets are read inside the pods (never printed here).
#
# Usage: scripts/k3s_data_reset.sh <data_product_slug> <topic_domain>
set -euo pipefail

SLUG="${1:?usage: k3s_data_reset.sh <data_product_slug> <topic_domain>}"
DOMAIN="${2:?usage: k3s_data_reset.sh <data_product_slug> <topic_domain>}"
[[ "$SLUG" =~ ^[a-z][a-z0-9_]{0,47}$ && "$DOMAIN" =~ ^[a-z][a-z0-9_]*$ ]] || { echo "❌ unsafe slug/domain"; exit 1; }
NS=ai-circus

echo "▶ pausing data-ingest and the transform CronJob"
kubectl -n "$NS" scale deployment/data-ingest --replicas=0 >/dev/null
kubectl -n "$NS" patch cronjob/data-transform -p '{"spec":{"suspend":true}}' >/dev/null 2>&1 || true
kubectl -n "$NS" wait --for=delete pod -l app=data-ingest --timeout=120s >/dev/null 2>&1 || true

echo "▶ dropping warehouse schemas, Iceberg catalog entries and the source schema"
kubectl -n "$NS" exec -i postgres-0 -- sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d data_platform' <<SQL
DROP SCHEMA IF EXISTS "${SLUG}" CASCADE;
DROP SCHEMA IF EXISTS "${SLUG}_silver" CASCADE;
DROP SCHEMA IF EXISTS "${SLUG}_gold" CASCADE;
DO \$\$ BEGIN
  IF to_regclass('public.iceberg_tables') IS NOT NULL THEN
    DELETE FROM public.iceberg_tables WHERE table_namespace = '${SLUG}_bronze';
  END IF;
  IF to_regclass('public.iceberg_namespace_properties') IS NOT NULL THEN
    DELETE FROM public.iceberg_namespace_properties WHERE namespace = '${SLUG}_bronze';
  END IF;
END \$\$;
SQL
kubectl -n "$NS" exec -i postgres-0 -- sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d data_sources' <<SQL
SELECT pg_drop_replication_slot(slot_name) FROM pg_replication_slots WHERE slot_name = 'data_ingest';
DROP SCHEMA IF EXISTS "${SLUG}" CASCADE;
SQL

echo "▶ deleting lake files and landing deliveries"
kubectl -n "$NS" exec -i deploy/data-api -- python - "$SLUG" <<'PY'
import os, sys
import boto3
slug = sys.argv[1]
s3 = boto3.client("s3", endpoint_url=os.environ.get("OBJECT_STORE_ENDPOINT", "http://seaweedfs:8333"),
                  aws_access_key_id=os.environ["OBJECT_STORE_ACCESS_KEY"],
                  aws_secret_access_key=os.environ["OBJECT_STORE_SECRET_KEY"], region_name="us-east-1")
bucket, deleted = "ai-circus-data", 0
for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
    keys = [o["Key"] for o in page.get("Contents", [])
            if o["Key"].startswith(f"lake/{slug}_bronze/") or f"/landing/{slug}/" in o["Key"]
            or f"/dbt/{slug}/" in o["Key"]]
    for i in range(0, len(keys), 1000):
        s3.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in keys[i:i + 1000]]})
        deleted += len(keys[i:i + 1000])
print(f"  {deleted} objects deleted")
PY

echo "▶ deleting the domain's Kafka topics (every tenant) and the pipeline-events topics"
kubectl -n "$NS" exec kafka-0 -- sh -c "/opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --delete \
    --topic 'tenant-.*\\.${DOMAIN}\\..*' 2>/dev/null; /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 \
    --delete --topic 'tenant-.*\\.platform\\.pipeline\\.events\\.v1' 2>/dev/null" || true

echo "▶ resuming"
kubectl -n "$NS" scale deployment/data-ingest --replicas=1 >/dev/null
kubectl -n "$NS" patch cronjob/data-transform -p '{"spec":{"suspend":false}}' >/dev/null 2>&1 || true
kubectl -n "$NS" rollout restart deployment/data-api >/dev/null  # forgets simulator state
kubectl -n "$NS" rollout status deployment/data-ingest --timeout=240s >/dev/null
kubectl -n "$NS" rollout status deployment/data-api --timeout=240s >/dev/null
echo "✓ $SLUG reset — 'make k3s-data-seed' to generate its history again"
