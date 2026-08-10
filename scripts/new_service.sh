#!/usr/bin/env bash
# Scaffold a new backend service from ai-circus-template via real cookiecutter
# generation, then adapt it to live inside this monorepo:
#   - flatten the nested git repo the template's post-gen hook creates
#   - add ai-circus-shared (libs/shared) as a local uv path dependency
#   - rewrite the Dockerfile to expect a repo-root build context (so it can
#     COPY libs/shared alongside the service's own folder)
#
# Usage: ./scripts/new_service.sh <service-name>   (e.g. etl-tabular)
set -euo pipefail

SERVICE_NAME="${1:?usage: new_service.sh <service-name>}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATE="${AI_CIRCUS_TEMPLATE:-$HOME/PROJECTS/ai-circus-template}"
SERVICE_DIR="$REPO_ROOT/services/$SERVICE_NAME"
PACKAGE_NAME="${SERVICE_NAME//-/_}"

if [ -d "$SERVICE_DIR" ]; then
    echo "❌ services/$SERVICE_NAME already exists" >&2
    exit 1
fi

echo "▶ Generating $SERVICE_NAME from $TEMPLATE ..."
cookiecutter "$TEMPLATE" --no-input -o "$REPO_ROOT/services" \
    project_name="$SERVICE_NAME" \
    project_description="ai-circus-framework $SERVICE_NAME service" \
    author_name="ai-circus-framework contributors" \
    author_email="dev@ai-circus-framework.local" \
    github_username_or_org="ai-circus-framework" \
    python_version="3.14"

# The template's post_gen hook git-inits + commits each generated project; flatten
# that so the service is tracked by this monorepo's single top-level repo instead.
rm -rf "$SERVICE_DIR/.git"

# Add the shared library as a local path dependency. Deliberately NOT editable:
# uv builds and installs it as a normal wheel into the service's .venv, so the
# runtime image is self-contained and doesn't need libs/shared copied alongside it.
if command -v uv >/dev/null 2>&1; then
    (cd "$SERVICE_DIR" && uv add "../../libs/shared" >/dev/null)
    echo "✓ added ai-circus-shared as a local (non-editable) dependency"
else
    echo "⚠️  uv not found — add 'ai-circus-shared = { path = \"../../libs/shared\" }' to $SERVICE_DIR/pyproject.toml manually"
fi

# Rewrite the Dockerfile for a repo-root build context (compose sets `context: .`,
# `dockerfile: services/$SERVICE_NAME/Dockerfile` for this service). The image
# preserves the *same relative layout* as the monorepo (services/<name>/ next to
# libs/shared/) so the `../../libs/shared` path dependency resolves identically in
# both local dev and the container — no path rewriting needed in pyproject.toml.
PYTHON_VERSION="$(grep -oP 'FROM python:\K[0-9.]+' "$SERVICE_DIR/Dockerfile" | head -1)"
cat > "$SERVICE_DIR/Dockerfile" <<EOF
# ── Builder stage ───────────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /uvx /bin/

WORKDIR /app

# Preserve the monorepo's relative layout (services/$SERVICE_NAME next to libs/shared)
# so the service's "../../libs/shared" path dependency resolves the same way here
# as it does in local dev.
COPY libs/shared /app/libs/shared
COPY services/$SERVICE_NAME/pyproject.toml services/$SERVICE_NAME/uv.lock services/$SERVICE_NAME/
RUN uv sync --project services/$SERVICE_NAME --frozen --no-cache --no-dev --no-install-project

COPY services/$SERVICE_NAME services/$SERVICE_NAME
RUN uv sync --project services/$SERVICE_NAME --frozen --no-cache --no-dev

# ── Runtime stage ───────────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS runtime
WORKDIR /app

COPY --from=builder /app/services/$SERVICE_NAME/.venv services/$SERVICE_NAME/.venv
COPY --from=builder /app/services/$SERVICE_NAME/src   services/$SERVICE_NAME/src
COPY --from=builder /app/services/$SERVICE_NAME/pyproject.toml services/$SERVICE_NAME/pyproject.toml
COPY --from=builder /app/services/$SERVICE_NAME/settings.yaml  services/$SERVICE_NAME/settings.yaml

ENV PATH="/app/services/$SERVICE_NAME/.venv/bin:\$PATH"
WORKDIR /app/services/$SERVICE_NAME

CMD ["python", "-m", "$PACKAGE_NAME.app"]
EOF

echo "✓ services/$SERVICE_NAME scaffolded — remember to add it to docker-compose.yml"
