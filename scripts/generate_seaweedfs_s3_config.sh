#!/usr/bin/env bash
# Generates infra/seaweedfs/s3.json, the S3 gateway identities file SeaweedFS's
# `-s3.config` flag reads (see the seaweedfs service in docker-compose.yml).
# Unlike infra/traefik/console.htpasswd (a Basic Auth credential only Traefik and a
# human need to know), this file's accessKey/secretKey MUST match
# OBJECT_STORE_ACCESS_KEY/OBJECT_STORE_SECRET_KEY in .env exactly — every backend
# service authenticates to SeaweedFS using those same .env values. So this script
# is not a one-time random generator like generate_console_auth.sh; it's a
# deterministic projection of .env, safe to re-run any time .env changes (`make
# bootstrap` does so automatically) and mirrors s3.json.example's shape.
#
# Usage: ./scripts/generate_seaweedfs_s3_config.sh
#   - Reads OBJECT_STORE_ACCESS_KEY/OBJECT_STORE_SECRET_KEY from .env.
#   - Writes infra/seaweedfs/s3.json (mode 600, gitignored), overwriting any previous one.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"
OUT_FILE="$REPO_ROOT/infra/seaweedfs/s3.json"

if [ ! -f "$ENV_FILE" ]; then
  echo "❌ $ENV_FILE not found — run 'make bootstrap' first." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
. "$ENV_FILE"
set +a

: "${OBJECT_STORE_ACCESS_KEY:?OBJECT_STORE_ACCESS_KEY missing from .env}"
: "${OBJECT_STORE_SECRET_KEY:?OBJECT_STORE_SECRET_KEY missing from .env}"

mkdir -p "$(dirname "$OUT_FILE")"
cat > "$OUT_FILE" <<JSON
{
  "identities": [
    {
      "name": "${OBJECT_STORE_ACCESS_KEY}",
      "credentials": [
        {
          "accessKey": "${OBJECT_STORE_ACCESS_KEY}",
          "secretKey": "${OBJECT_STORE_SECRET_KEY}"
        }
      ],
      "actions": ["Admin", "Read", "Write"]
    }
  ]
}
JSON
chmod 600 "$OUT_FILE"

echo "▶ Wrote $OUT_FILE from .env's OBJECT_STORE_ACCESS_KEY/OBJECT_STORE_SECRET_KEY"
