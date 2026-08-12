"""
- Title:    HTTP client for the sibling `prediction` service
- Author:   ai-circus-framework contributors

Gives the chat agent (see core/tools.py) real dataset rows, held-out evaluation
results, and live model predictions — without any code execution (see core/chat.py's
module docstring): every call here hits `prediction`'s already-tested, entitlement-
checked REST API (services/prediction/src/prediction/api.py) and returns its JSON
response as-is. Mirrors `ai_circus_shared.entitlements.PlatformRegistryClient`'s shape,
but lives here rather than in libs/shared since only `assistant` needs it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class PredictionServiceClient:
    """HTTP client for `prediction`'s dataset/predict endpoints.

    Every method takes the caller's raw `authorization` header value and forwards it
    unchanged — `prediction` resolves and entitlement-checks it independently (see
    `prediction.core.identity.resolve_identity`), so this client mints no token of its
    own and needs no shared secret with `prediction`.
    """

    base_url: str
    timeout_seconds: float = 10.0

    def _headers(self, authorization: str | None) -> dict[str, str]:
        return {"Authorization": authorization} if authorization else {}

    def sample(self, *, scenario_slug: str, authorization: str | None, limit: int) -> dict[str, Any]:
        """Real dataset rows — `GET /dataset/{scenario_slug}/sample`."""
        response = httpx.get(
            f"{self.base_url}/dataset/{scenario_slug}/sample",
            params={"limit": limit},
            headers=self._headers(authorization),
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def evaluation(self, *, scenario_slug: str, authorization: str | None, limit: int) -> dict[str, Any]:
        """Held-out actual-vs-predicted evaluation — `GET /dataset/{scenario_slug}/evaluation`."""
        response = httpx.get(
            f"{self.base_url}/dataset/{scenario_slug}/evaluation",
            params={"limit": limit},
            headers=self._headers(authorization),
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def predict(
        self, *, scenario_slug: str, authorization: str | None, records: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """Live model predictions for caller-supplied records — `POST /predict/{scenario_slug}`."""
        response = httpx.post(
            f"{self.base_url}/predict/{scenario_slug}",
            json={"records": records},
            headers=self._headers(authorization),
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()
