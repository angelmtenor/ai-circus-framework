#!/bin/bash
# Runs once, on first container start (postgres image convention: any *.sh under
# /docker-entrypoint-initdb.d/ is executed). POSTGRES_DB only creates one database
# (the `platform` schema used by services/platform-registry); Keycloak, llm-gateway
# (LiteLLM's own spend-tracking schema), and each of assistant/rag-agent/form-agent
# (their own persisted conversation-history schema, see
# ai_circus_shared.conversations) each need their own.
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
EOSQL
