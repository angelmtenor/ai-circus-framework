"""Client for services/platform-registry's entitlement and scenario-metadata API.

Every backend service calls `check_entitlement` before serving a scenario request —
enforcement happens at the API, not just in the UI. `list_scenarios` mirrors the same
`/entitlements/{org_id}` endpoint `ui-react` calls directly (via its own TypeScript
client) to render only the scenarios a tenant is entitled to.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from functools import cache
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict

# Every prediction/chat request otherwise made a fresh HTTP round-trip to
# platform-registry just to re-check the same (org, scenario) entitlement or
# re-fetch the same active model name — the dominant per-request latency cost
# under load. A short in-process TTL cache cuts that to ~one call per window per
# key; a just-revoked entitlement or just-switched model can take up to this long
# to take effect everywhere.
_CACHE_TTL_SECONDS = 30.0
# Entitlement keys include the caller-chosen scenario slug, so without a ceiling an
# authenticated caller could grow this process's memory by requesting endless
# made-up slugs. Expired entries are swept first; only then is the oldest dropped.
_CACHE_MAX_ENTRIES = 4096

# The org id (a Keycloak organization UUID, or a fixed demo tenant id) and the scenario
# slug are interpolated into platform-registry URL paths below. Anything outside this
# alphabet — `?`, `#`, `/`, `%`, `..` — could re-target the request (e.g. `churn?x`
# would check entitlement to `churn` on behalf of slug `churn?x`), so it is rejected up
# front instead of escaped: no legitimate org id or slug ever contains one.
_SAFE_PATH_SEGMENT = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


class _TtlCache[K, V]:
    """A tiny thread-safe TTL map with a size ceiling — see _CACHE_MAX_ENTRIES."""

    def __init__(self, ttl_seconds: float = _CACHE_TTL_SECONDS, max_entries: int = _CACHE_MAX_ENTRIES) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._data: dict[K, tuple[V, float]] = {}
        self._lock = threading.Lock()

    def get(self, key: K) -> V | None:
        with self._lock:
            entry = self._data.get(key)
        if entry is None or entry[1] <= time.monotonic():
            return None
        return entry[0]

    def put(self, key: K, value: V) -> None:
        now = time.monotonic()
        with self._lock:
            if len(self._data) >= self._max and key not in self._data:
                for stale in [k for k, (_, expiry) in self._data.items() if expiry <= now]:
                    del self._data[stale]
                if len(self._data) >= self._max:
                    del self._data[next(iter(self._data))]
            self._data[key] = (value, now + self._ttl)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


_entitlement_cache: _TtlCache[tuple[str, str, str], bool] = _TtlCache()
_active_model_cache: _TtlCache[str, str] = _TtlCache()
_providers_cache: _TtlCache[str, list[dict[str, Any]]] = _TtlCache()
_active_voice_settings_cache: _TtlCache[str, tuple[str, str]] = _TtlCache()


@cache
def _http() -> httpx.Client:
    """One process-wide pooled client (httpx.Client is thread-safe), so the calls
    below reuse keep-alive connections to platform-registry instead of paying a
    fresh TCP handshake on every cache miss.
    """
    return httpx.Client()


class EntitlementDeniedError(Exception):
    """Raised when a tenant/org is not entitled to a scenario."""


class ScenarioSummary(BaseModel):
    """Scenario metadata as served by platform-registry (mirrors scenario.yaml).

    One consolidated `prediction`/`assistant`/`rag-agent` instance serves every
    scenario of its kind (routed by `{scenario_slug}` in the request path, e.g.
    `POST /predict/{slug}`), so there's no per-scenario service name to carry here —
    `ui-react` calls one fixed configured URL per kind. `feature_columns`/`feature_schema`
    drive `ui-react`'s generic tabular_ml form renderer, and `sample_questions` renders
    as clickable chat suggestions — all `None` for `conversational_rag` scenarios
    except `sample_questions`, which applies to both kinds.

    The single source of truth for this shape: platform-registry's API uses this
    directly as its `/entitlements/{org_id}` response_model (via `from_attributes`,
    straight off its `Scenario` ORM rows); `ui-react`'s own TypeScript `ScenarioSummary`
    type (see apiClient.ts) mirrors the same JSON shape independently.
    """

    model_config = ConfigDict(frozen=True, from_attributes=True)

    slug: str
    kind: str
    title: str
    description: str
    icon: str
    # Domain taxonomy slug (see scenario_schema.Industry — industries plus `tutorial`
    # / `society_ethics`) — powers ui-react's Domain filter, orthogonal to `kind`.
    industry: str
    # Attribution for a ported public dataset (see scenario_schema.DatasetCredits) —
    # None for scenarios whose content is original.
    credits: dict[str, Any] | None = None
    feature_columns: list[str] | None = None
    feature_schema: dict[str, Any] | None = None
    # tabular_ml only — seeds ui-react's Data tab dashboard (see scenario_schema.py's
    # ChartSpec); plain dicts here, not the pydantic model, matching feature_schema's
    # own wire/ORM-facing shape above.
    default_charts: list[dict[str, Any]] | None = None
    sample_questions: list[str] = []
    # tabular_ml only — lets ui-react render a plain "value units" prediction for
    # regression scenarios instead of the classification percentage/probability view.
    task_type: str | None = None
    target_units: str | None = None
    # tabular_ml only — human-friendly name/explanation for the target (see
    # scenario_schema.TabularModel.target_label/target_description).
    target_label: str | None = None
    target_description: str | None = None
    target_value_labels: dict[str, str] | None = None
    # tabular_ml only — the dataset column being predicted (not itself a feature).
    target: str | None = None
    # assisted_form only — drives ui-react's generic form renderer (see
    # scenario_schema.FormConfig); a plain dict here, not the pydantic model, matching
    # feature_schema's own wire/ORM-facing shape above.
    form: dict[str, Any] | None = None
    # tabular_ml only — opts this scenario into one of ui-react's two generic 5th
    # workspace tabs (see scenario_schema.UiExtras); a plain dict here, same reasoning
    # as `form` above. None is the common case (the plain 4-tab workspace).
    ui_extras: dict[str, Any] | None = None
    # deep_learning only — modality, labels, base model and input hints that drive
    # ui-react's generic DeepLearningView (see scenario_schema.DeepLearningConfig);
    # a plain dict for the same reason as `form` above.
    deep_learning: dict[str, Any] | None = None
    # tabular_ml only — a guided Tutorial tab (see scenario_schema.TutorialConfig); a
    # plain dict for the same reason as `form` above.
    tutorial: dict[str, Any] | None = None
    # tabular_ml with a free-text feature only — an LLM rubric check over a behaviour
    # description, and the sentence-embedding challenger model (see
    # scenario_schema.RubricCheckConfig / TextChallenger); plain dicts like `form`.
    rubric_check: dict[str, Any] | None = None
    text_challenger: dict[str, Any] | None = None


@dataclass(frozen=True)
class PlatformRegistryClient:
    """HTTP client for the platform-registry service."""

    base_url: str
    timeout_seconds: float = 5.0

    def check_entitlement(self, *, org_id: str, scenario_slug: str) -> None:
        """Raise `EntitlementDeniedError` unless the org is entitled to the scenario.

        Cached in-process for `_CACHE_TTL_SECONDS` (see module docstring comment).
        """
        if not (_SAFE_PATH_SEGMENT.match(org_id) and _SAFE_PATH_SEGMENT.match(scenario_slug)):
            raise EntitlementDeniedError(f"Org {org_id!r} is not entitled to scenario {scenario_slug!r}.")
        cache_key = (self.base_url, org_id, scenario_slug)
        entitled = _entitlement_cache.get(cache_key)
        if entitled is None:
            response = _http().get(
                f"{self.base_url}/entitlements/{org_id}/{scenario_slug}",
                timeout=self.timeout_seconds,
            )
            entitled = response.status_code != httpx.codes.NOT_FOUND
            if entitled:
                response.raise_for_status()
            _entitlement_cache.put(cache_key, entitled)

        if not entitled:
            raise EntitlementDeniedError(f"Org {org_id!r} is not entitled to scenario {scenario_slug!r}.")

    def list_scenarios(self, *, org_id: str, authorization: str | None = None) -> list[ScenarioSummary]:
        """Return the scenarios the given org is entitled to.

        Unlike `check_entitlement` above, platform-registry gates this route with
        `require_org_match` (the caller must prove they *are* `org_id`) since it's
        also called directly by `ui-react` to render the scenario picker — so a
        server-to-server caller (e.g. agui-voice resolving a scenario's `kind`) must
        forward the end user's own bearer token here, same as
        `PredictionServiceClient` forwarding it on to `prediction`. Omit
        `authorization` only when calling as a trusted admin/dev caller that already
        cleared its own auth check.
        """
        if not _SAFE_PATH_SEGMENT.match(org_id):
            raise ValueError(f"Invalid org id {org_id!r}.")
        headers = {"Authorization": authorization} if authorization else {}
        response = _http().get(f"{self.base_url}/entitlements/{org_id}", headers=headers, timeout=self.timeout_seconds)
        response.raise_for_status()
        return [ScenarioSummary(**item) for item in response.json()]

    def get_active_llm_model(self, *, admin_api_key: str) -> str:
        """Return the litellm_config.yaml model_name assistant/rag-agent should use for
        their next chat completion — the Settings page's live provider/model picker.
        Raises on failure (network/404/etc); callers decide whether to fall back to a
        static default. Cached in-process for `_CACHE_TTL_SECONDS`.
        """
        cached = _active_model_cache.get(self.base_url)
        if cached is not None:
            return cached

        response = _http().get(
            f"{self.base_url}/llm-settings/active-model",
            headers={"Authorization": f"Bearer {admin_api_key}"},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        model_name = response.json()["model_name"]
        _active_model_cache.put(self.base_url, model_name)
        return model_name

    def get_active_voice_settings(self, *, admin_api_key: str) -> tuple[str, str]:
        """Return `(stt_provider, tts_provider)` agui-voice should use for its next WS
        connection/`/tts` call — the Settings page's live voice-mode picker.
        Raises on failure (network/404/etc); callers decide whether to fall back to a
        static default. Cached in-process for `_CACHE_TTL_SECONDS`.
        """
        cached = _active_voice_settings_cache.get(self.base_url)
        if cached is not None:
            return cached

        response = _http().get(
            f"{self.base_url}/voice-settings/active",
            headers={"Authorization": f"Bearer {admin_api_key}"},
            timeout=self.timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
        settings = (body["stt_provider"], body["tts_provider"])
        _active_voice_settings_cache.put(self.base_url, settings)
        return settings

    def get_llm_provider_display(self, *, admin_api_key: str, model_name: str) -> tuple[str, str, bool] | None:
        """(provider label, real model id, vision-capable) for a litellm_config.yaml
        alias — e.g. `("GroqCloud", "openai/gpt-oss-120b", False)` for `"groq-llama"` —
        so callers can show "model (provider)" instead of the bare, often cryptic
        alias, and know whether an attached image can go straight to this model or
        needs OCR first (see ChatPanel.tsx's attach flow). `None` if `model_name` no
        longer matches any routed provider.

        Each provider (one API key) may route more than one model (see
        platform_registry.core.llm_settings.PROVIDERS["groq"]) — `model_name` is
        matched against each provider's nested `models` list, not the provider itself.

        Reuses the same `/llm-settings/providers` list ui-react's admin Settings page
        renders. Cached in-process for `_CACHE_TTL_SECONDS`.
        """
        providers = _providers_cache.get(self.base_url)
        if providers is None:
            response = _http().get(
                f"{self.base_url}/llm-settings/providers",
                headers={"Authorization": f"Bearer {admin_api_key}"},
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            providers = response.json()
            _providers_cache.put(self.base_url, providers)

        for provider in providers:
            for model_entry in provider.get("models", []):
                if model_entry.get("model_name") == model_name:
                    model = model_entry.get("model")
                    return (
                        provider["label"],
                        model if isinstance(model, str) else model_name,
                        bool(model_entry.get("vision", False)),
                    )
        return None
