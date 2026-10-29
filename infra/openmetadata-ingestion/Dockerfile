# OpenMetadata metadata ingestion for the Data Platform's governance tier (docs/adr/0010) —
# the ingestion framework as a one-shot Job instead of OpenMetadata's bundled Airflow
# (~1 GiB saved). catalog_sync.py turns each data product's scenario.yaml into workflows
# (Postgres, ClickHouse, Kafka, dbt, Superset) plus the lineage and contracts the connectors
# can't infer. Python 3.11: the ingestion framework's supported range lags the services' 3.14.
FROM python:3.11-slim
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 --shell /usr/sbin/nologin app
RUN pip install --no-cache-dir "openmetadata-ingestion[postgres,clickhouse,kafka,dbt,superset]==2.0.7.1" \
    "PyYAML>=6" "boto3>=1.35" && pip uninstall -y --root-user-action=ignore pip
COPY catalog_sync.py /app/catalog_sync.py
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MALLOC_ARENA_MAX=2
USER app
WORKDIR /app
CMD ["python", "/app/catalog_sync.py"]
