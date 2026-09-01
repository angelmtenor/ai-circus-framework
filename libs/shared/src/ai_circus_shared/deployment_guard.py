"""Refuse to boot with a known-demo credential when DEPLOYMENT_TARGET=public.

Every service accepts `ADMIN_API_KEY`/`ENGINEERING_DEMO_API_KEY` as a bearer-token auth
bypass (see `auth.py`'s `resolve_caller_identity`) and ships a documented demo default
for both in the root `.env.example`, intended for local/`docker compose up` use only.
`APP_ENVIRONMENT` can't distinguish "local docker dev" from "this same docker-compose.yml
deployed as-is to a public VM" — both set `APP_ENVIRONMENT=docker`, and it's also the
knob that picks each service's own `settings.yaml` profile, a different concern entirely.
`DEPLOYMENT_TARGET` is a separate, explicit `local`/`public` opt-in an operator sets in
their own (gitignored) `.env` before exposing any service to the internet.
"""

from __future__ import annotations

import os

DEMO_ADMIN_API_KEY = "ai-circus-2026"
DEMO_ENGINEERING_DEMO_API_KEY = "ai-circus-engineering-2026"


def enforce_safe_for_public_deployment(
    *,
    admin_api_key: str | None,
    engineering_demo_api_key: str | None,
    auth_disabled: str,
) -> None:
    """Raise if `DEPLOYMENT_TARGET=public` but a demo auth bypass is still active.

    Call once at service startup, right after config validation and before the app
    starts accepting requests. A no-op unless the operator has set
    `DEPLOYMENT_TARGET=public` (default: unset, treated as `local`), so local/
    `docker compose up` dev keeps working with the shipped demo credentials.

    Raises:
        RuntimeError: `DEPLOYMENT_TARGET=public` and `AUTH_DISABLED=true`, or either
            credential still matches its shipped demo default.
    """
    if os.getenv("DEPLOYMENT_TARGET", "local").strip().lower() != "public":
        return

    problems = []
    if auth_disabled.strip().lower() == "true":
        problems.append("AUTH_DISABLED=true bypasses Keycloak entirely")
    if admin_api_key == DEMO_ADMIN_API_KEY:
        problems.append("ADMIN_API_KEY is still the shipped demo default")
    if engineering_demo_api_key == DEMO_ENGINEERING_DEMO_API_KEY:
        problems.append("ENGINEERING_DEMO_API_KEY is still the shipped demo default")

    if problems:
        raise RuntimeError(
            "DEPLOYMENT_TARGET=public but this service isn't safe to expose: "
            + "; ".join(problems)
            + ". Rotate the affected value(s) in .env (or blank them to disable the "
            "bypass outright) before deploying — see README 'Public deployment'."
        )
