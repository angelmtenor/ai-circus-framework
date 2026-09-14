#!/bin/bash
# Runs once, on first container start (postgres image convention: any *.sh under
# /docker-entrypoint-initdb.d/ is executed). POSTGRES_DB only creates one database
# (the `platform` schema used by services/platform-registry); Keycloak, llm-gateway
# (LiteLLM's own spend-tracking schema), each of assistant/rag-agent/form-agent
# (their own persisted conversation-history schema, see
# ai_circus_shared.conversations), data-platform-manager (its own document-store
# schema, see ai_circus_shared.document_store), Langfuse (GenAI observability, its
# own Prisma-managed schema) and MLflow (ML experiment tracking backend store) each
# need their own.
#
# Existing volumes never re-run this script — k8s/base/langfuse.yaml and
# k8s/base/mlflow.yaml therefore also create their own database on start-up when it
# is missing (an idempotent `CREATE DATABASE` init container), so a cluster created
# before those two existed doesn't need a `make reset-all`.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE keycloak;
    GRANT ALL PRIVILEGES ON DATABASE keycloak TO "$POSTGRES_USER";
    CREATE DATABASE litellm;
    GRANT ALL PRIVILEGES ON DATABASE litellm TO "$POSTGRES_USER";
    CREATE DATABASE assistant;
    GRANT ALL PRIVILEGES ON DATABASE assistant TO "$POSTGRES_USER";
    CREATE DATABASE rag_agent;
    GRANT ALL PRIVILEGES ON DATABASE rag_agent TO "$POSTGRES_USER";
    CREATE DATABASE form_agent;
    GRANT ALL PRIVILEGES ON DATABASE form_agent TO "$POSTGRES_USER";
    CREATE DATABASE data_platform_manager;
    GRANT ALL PRIVILEGES ON DATABASE data_platform_manager TO "$POSTGRES_USER";
    CREATE DATABASE langfuse;
    GRANT ALL PRIVILEGES ON DATABASE langfuse TO "$POSTGRES_USER";
    CREATE DATABASE mlflow;
    GRANT ALL PRIVILEGES ON DATABASE mlflow TO "$POSTGRES_USER";
EOSQL
