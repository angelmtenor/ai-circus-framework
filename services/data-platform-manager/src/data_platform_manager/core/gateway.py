"""
- Title:    AI Gateway rate-limit report (read-only)
- Author:   ai-circus-framework contributors

llm-gateway execs the real LiteLLM proxy (see services/llm-gateway/app.py) — this
module talks to *that* running process's own admin API (master-key protected),
same as platform_registry.core.llm_settings, never a provider SDK directly.
Read-only by design: litellm_config.yaml's model_list is this repo's single
source of truth for rpm/tpm ceilings (see that file's own comment on why
per-tenant budgets need a DB-backed proxy this deployment deliberately doesn't
run) — an operator edits it and restarts llm-gateway; nothing here writes back.
"""

from __future__ import annotations

import httpx


class LlmGatewayError(RuntimeError):
    """Raised when llm-gateway's admin API call fails outright (network/5xx)."""


def _client(base_url: str, master_key: str) -> httpx.Client:
    return httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {master_key}"}, timeout=10.0)


def get_rate_limits(base_url: str, master_key: str) -> list[dict[str, object]]:
    """One entry per routed model: its static rpm/tpm ceiling, or None for either
    if litellm_config.yaml doesn't set one (see llm_gateway's local-embed entry).
    """
    with _client(base_url, master_key) as client:
        try:
            response = client.get("/model/info")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LlmGatewayError(f"Could not reach llm-gateway: {exc}") from exc
    deployments = response.json().get("data", [])
    results = [
        {
            "model_name": deployment.get("model_name"),
            "rpm": (deployment.get("litellm_params") or {}).get("rpm"),
            "tpm": (deployment.get("litellm_params") or {}).get("tpm"),
        }
        for deployment in deployments
    ]
    return sorted(results, key=lambda r: r["model_name"] or "")
