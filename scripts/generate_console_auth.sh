#!/usr/bin/env bash
# Generates the htpasswd file Traefik's Basic Auth middleware (always on, see the
# admin-basicauth labels on the keycloak/seaweedfs services in docker-compose.yml) reads to
# gate Keycloak's Admin Console and SeaweedFS's console. Uses `openssl passwd -apr1` rather
# than requiring `htpasswd`/pulling an image, since openssl is already a dependency
# of every Docker host this project targets.
#
# Deliberately writes a FILE (infra/traefik/console.htpasswd, gitignored, mounted
# read-only into the traefik container) rather than an env var: the root Makefile does
# `include .env; export`, and GNU Make treats a bare `$x` inside an included value as
# a variable reference — it would silently mangle an apr1/bcrypt hash (which is full of
# `$`-delimited segments) before docker compose ever saw it. A file sidesteps that
# entirely, and keeps the hash out of `docker inspect`/`docker compose config` output.
#
# Usage: ./scripts/generate_console_auth.sh [username] [password]
#   - Writes infra/traefik/console.htpasswd (mode 600), overwriting any previous one.
#   - Without a password argument (the public-deployment path) it generates a random
#     one and prints it ONCE, to the terminal only — it is not stored anywhere in
#     recoverable form; save it in a password manager now.
#   - With a password argument (or CONSOLE_PASSWORD in the environment) it hashes that
#     instead — for local dev, to keep this console login in step with the shared demo
#     password in .env (KEYCLOAK_*/LANGFUSE_INIT_USER_*/ADMIN_API_KEY), or to regenerate
#     the shipped infra/traefik/console.htpasswd.example. Never use it for a public
#     deployment: the whole point there is a password nobody else knows.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USERNAME="${1:-admin}"
PASSWORD="${2:-${CONSOLE_PASSWORD:-}}"
OUT_FILE="$REPO_ROOT/infra/traefik/console.htpasswd"

if [ -z "$PASSWORD" ]; then
  PASSWORD="$(openssl rand -base64 24 | tr -dc 'A-Za-z0-9' | cut -c1-24)"
fi
HASH="$(openssl passwd -apr1 "$PASSWORD")"

mkdir -p "$(dirname "$OUT_FILE")"
printf '%s:%s\n' "$USERNAME" "$HASH" > "$OUT_FILE"
chmod 600 "$OUT_FILE"

echo "▶ Wrote $OUT_FILE"
echo ""
echo "  Username: $USERNAME"
echo "  Password: $PASSWORD   (save this now — it is not stored anywhere; you cannot recover it later)"
echo ""
echo "Run 'make check-public-ready' to confirm this and .env are set before deploying with 'make up'."
