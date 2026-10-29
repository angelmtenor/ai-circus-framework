# Apache Superset for the Data Platform's bi tier (docs/adr/0009). The official image ships no
# database drivers: add Postgres (metadata DB + the warehouse) and ClickHouse (the marts on
# the analytics tier). Configuration comes from superset_config.py, mounted by the k8s
# manifest from a ConfigMap (k8s/data-platform/bi/superset.yaml).
FROM apache/superset:6.1.0
USER root
RUN uv pip install --python /app/.venv/bin/python --no-cache psycopg2-binary==2.9.11 clickhouse-connect==0.8.18
USER superset
