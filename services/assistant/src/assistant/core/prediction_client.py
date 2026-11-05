"""
- Title:    HTTP client for the sibling `prediction` service
- Author:   Angel Martinez-Tenor

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

    def recommendable_items(self, *, scenario_slug: str, authorization: str | None) -> list[dict[str, Any]]:
        """A recommender's items — `GET /recommend/{scenario_slug}/items`."""
        response = httpx.get(
            f"{self.base_url}/recommend/{scenario_slug}/items",
            headers=self._headers(authorization),
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def recommend(self, *, scenario_slug: str, authorization: str | None, item: str) -> dict[str, Any]:
        """What goes with an item — `GET /recommend/{scenario_slug}`."""
        response = httpx.get(
            f"{self.base_url}/recommend/{scenario_slug}",
            params={"item": item},
            headers=self._headers(authorization),
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def forecast(
        self, *, scenario_slug: str, authorization: str | None, series: str | None, shifts: dict[str, float]
    ) -> dict[str, Any]:
        """A forecast scenario's forecast of one series — `GET /forecast/{scenario_slug}`, or its
        what-if under shifted exogenous values — `POST /forecast/{scenario_slug}/what-if`.
        """
        if shifts:
            response = httpx.post(
                f"{self.base_url}/forecast/{scenario_slug}/what-if",
                json={"series": series, "shifts": shifts},
                headers=self._headers(authorization),
                timeout=self.timeout_seconds,
            )
        else:
            response = httpx.get(
                f"{self.base_url}/forecast/{scenario_slug}",
                params={"series": series} if series else {},
                headers=self._headers(authorization),
                timeout=self.timeout_seconds,
            )
        response.raise_for_status()
        return response.json()

    def backtest(self, *, scenario_slug: str, authorization: str | None) -> dict[str, Any]:
        """How each forecast candidate did on past cut-offs — `GET /model/{scenario_slug}/backtest`."""
        response = httpx.get(
            f"{self.base_url}/model/{scenario_slug}/backtest",
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
