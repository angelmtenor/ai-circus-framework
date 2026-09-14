# MLflow tracking server — the platform's MLOps monitor (experiment/model-run history
# for every `training` run). The official ghcr.io/mlflow/mlflow image is a bare
# `pip install mlflow` and ships neither a Postgres driver nor boto3, so it can't use
# this platform's existing stores; this tiny image adds exactly those two so the server
# stays stateless like every other service here: run metadata in the shared Postgres
# (`mlflow` database, see infra/postgres/init-multiple-databases.sh), artifacts in
# SeaweedFS (bucket `mlflow`, proxied through the server — clients never need S3
# credentials). Built by `make k3s-build` as ai-circus/mlflow:local; the pin below is
# the single place to bump the MLflow version.
FROM python:3.12-slim

ARG MLFLOW_VERSION=3.16.0
RUN pip install --no-cache-dir "mlflow==${MLFLOW_VERSION}" "psycopg2-binary>=2.9" "boto3>=1.35" \
    && useradd --create-home --shell /bin/bash --uid 1000 app

USER app
WORKDIR /home/app
EXPOSE 5000

# MLflow >= 3 otherwise forks six `huey` job-consumer processes (~150 MiB each — a full
# 1 GiB before the first request) for its server-side jobs feature (evaluation/
# scoring jobs, prompt optimization), which nothing here uses: training talks to the
# plain tracking API. Off, the server idles around 450 MiB. Its Host-header allow-list
# (MLFLOW_SERVER_ALLOWED_HOSTS) is set per deployment, since it names the ingress host.
ENV MLFLOW_SERVER_ENABLE_JOB_EXECUTION=false

# Backend/artifact locations come from the environment (k8s/base/mlflow.yaml,
# docker-compose.yml) — MLFLOW_BACKEND_STORE_URI / MLFLOW_ARTIFACTS_DESTINATION /
# MLFLOW_S3_ENDPOINT_URL / AWS_* — so this CMD is the same everywhere.
CMD ["mlflow", "server", "--host", "0.0.0.0", "--port", "5000", "--serve-artifacts", "--workers", "1"]
