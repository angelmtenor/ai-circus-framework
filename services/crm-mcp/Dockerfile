# ── Builder stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /uvx /bin/

WORKDIR /app

# Precompile bytecode at build time: a fresh container never has .pyc files on disk, so
# every pod start would otherwise recompile each imported module on first use.
# UV_LINK_MODE=copy because the uv cache is a mount.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Preserve the monorepo's relative layout (services/crm-mcp next to libs/shared and libs/mcp-kit)
# so the service's "../../libs/*" path dependencies resolve the same way here
# as it does in local dev.
COPY libs/shared /app/libs/shared
COPY libs/mcp-kit /app/libs/mcp-kit
COPY services/crm-mcp/pyproject.toml services/crm-mcp/uv.lock services/crm-mcp/
RUN --mount=type=cache,target=/root/.cache/uv,id=uv-cache,sharing=locked \
    uv sync --project services/crm-mcp --frozen --no-dev --no-install-project

COPY services/crm-mcp services/crm-mcp
RUN --mount=type=cache,target=/root/.cache/uv,id=uv-cache,sharing=locked \
    uv sync --project services/crm-mcp --frozen --no-dev

# ── Runtime stage ───────────────────────────────────────────────────────────────
FROM python:3.14-slim AS runtime
WORKDIR /app

# Pull the Debian security fixes published since the base image was built, and drop the
# base image's own pip: nothing installs at runtime, and its vendored urllib3/msgpack are
# what CI's Trivy scan (HIGH/CRITICAL, fixed-only) flags.
RUN apt-get update && apt-get upgrade -y --no-install-recommends && rm -rf /var/lib/apt/lists/* \
    && python -m pip uninstall -y --root-user-action=ignore pip

# UID 1000 is what every k8s manifest's runAsUser pins. The code/venv below stay
# root-owned (read-only to the service): only the home directory (caches) is writable.
RUN useradd --create-home --uid 1000 --shell /usr/sbin/nologin app

COPY --from=builder /app/services/crm-mcp/.venv services/crm-mcp/.venv
COPY --from=builder /app/services/crm-mcp/src   services/crm-mcp/src
COPY --from=builder /app/services/crm-mcp/pyproject.toml services/crm-mcp/pyproject.toml
COPY --from=builder /app/services/crm-mcp/settings.yaml  services/crm-mcp/settings.yaml
COPY --from=builder /app/services/crm-mcp/policy.yaml    services/crm-mcp/policy.yaml

ENV PATH="/app/services/crm-mcp/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MALLOC_ARENA_MAX=2
WORKDIR /app/services/crm-mcp

USER app

CMD ["python", "-m", "crm_mcp.app"]
