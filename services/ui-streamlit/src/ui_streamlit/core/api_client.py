"""
- Title:    Thin HTTP clients for the backend services
- Author:   ai-circus-framework contributors

Scenario listing/entitlements reuse ai_circus_shared.entitlements.PlatformRegistryClient
directly (see streamlit_app.py) rather than duplicating it here — prediction/assistant/
rag-agent aren't OpenAI-compatible-style entitlement APIs, so those still get their own
thin clients below.
"""

from __future__ import annotations

from typing import Any

import httpx


def _headers(access_token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"} if access_token else {}


def predict(
    base_url: str, scenario_slug: str, records: list[dict[str, Any]], access_token: str | None
) -> dict[str, Any]:
    """Call the prediction service's POST /predict/{scenario_slug} (one consolidated instance serves every
    tabular_ml scenario — see root plan's "Consolidation mechanism" decision).
    """
    response = httpx.post(
        f"{base_url}/predict/{scenario_slug}", json={"records": records}, headers=_headers(access_token), timeout=30.0
    )
    response.raise_for_status()
    return response.json()


def chat(
    base_url: str,
    scenario_slug: str,
    message: str,
    history: list[dict[str, str]],
    access_token: str | None,
) -> dict[str, Any]:
    """Call a chat-style service's POST /chat/{scenario_slug} (assistant or rag-agent — same request/response shape)."""
    response = httpx.post(
        f"{base_url}/chat/{scenario_slug}",
        json={"message": message, "history": history},
        headers=_headers(access_token),
        timeout=60.0,
    )
    response.raise_for_status()
    return response.json()
