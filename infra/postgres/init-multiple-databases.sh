#!/bin/bash
# Runs once, on first container start (postgres image convention: any *.sh under
# /docker-entrypoint-initdb.d/ is executed). POSTGRES_DB only creates one database
# (the `platform` schema used by services/platform-registry); Logto and llm-gateway
# (LiteLLM's own spend-tracking schema) each need their own.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE DATABASE logto;
    GRANT ALL PRIVILEGES ON DATABASE logto TO "$POSTGRES_USER";
    CREATE DATABASE litellm;
    GRANT ALL PRIVILEGES ON DATABASE litellm TO "$POSTGRES_USER";
EOSQL
