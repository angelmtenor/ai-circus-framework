# ai-circus-shared

Internal library shared by every service in `ai-circus-framework`. Added as a local `uv`
path dependency (`ai-circus-shared = { path = "../../libs/shared" }`) — each service still
builds and ships as its own independent image (see root `README.md` → "Shared code").

## Modules

- `auth.py` — validates Logto-issued OIDC access tokens (JWKS-based) and extracts the
  tenant (Logto Organization id) and `scenario:*` roles from the claims.
- `storage.py` — thin MinIO/S3 client wrapper (`boto3`) for reading/writing datasets,
  model artifacts, and documents under a per-tenant key prefix.
- `entitlements.py` — HTTP client for `services/platform-registry`'s entitlement-check
  endpoint; every backend service calls this before serving a scenario request.
- `scenario_schema.py` — Pydantic models mirroring `scenarios/*/scenario.yaml`, used by
  `platform-registry` to validate and seed scenario definitions into Postgres.
