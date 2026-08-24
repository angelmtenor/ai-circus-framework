"""
- Title:    llm-gateway provider status (admin-only view/test)
- Author:   ai-circus-framework contributors

llm-gateway execs the real LiteLLM proxy CLI (see services/llm-gateway/app.py) — this
module talks to *that* running process's own admin API (master-key protected), never
a provider SDK directly.

Why there's no "save a new key from the browser" here: litellm's own `/model/new`
(the only way to add/update a routed model at runtime) requires litellm's DB-backed
proxy mode (Prisma + a `DATABASE_URL`) — confirmed by calling it against this
deployment's gateway, which 500s with "No DB Connected". litellm_config.yaml already
documents why that mode isn't enabled here (the `prisma generate` codegen step needs
a Node.js toolchain this image doesn't run). Without it, every provider's key/base is
env-var-only, set once at container start — so this module reports real, live status
(via `/model/info` and an actual completion call) and tells the caller exactly which
`.env` variable(s) to set, rather than pretending to apply a change that can't take
effect until an operator edits `.env` and restarts llm-gateway anyway.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import httpx


@dataclass(frozen=True)
class ProviderModel:
    """One selectable model within a provider — providers with only one model (the
    common case) still get a one-element `models` tuple, so callers never special-case
    "does this provider have alternatives".
    """

    model_name: str
    """The litellm_config.yaml alias (`model_list[].model_name`), e.g. "groq-llama"."""

    label: str
    """Short, human-readable name for this model within its provider's card, e.g.
    "gpt-oss-120b (accurate)" — distinct from `ProviderSpec.label`, which names the
    provider/API key itself, e.g. "GroqCloud"."""

    vision: bool = False
    """Whether this model accepts image input — lets ui-react's chat attach flow
    decide whether an attached image can go straight to the model (as an AG-UI
    `ImageInputContent` block) or needs OCR text-extraction first (see
    platform_registry.core.document_extraction)."""


@dataclass(frozen=True)
class ProviderSpec:
    """One provider's (i.e. one API key's) routing shape on the gateway, per
    litellm_config.yaml. `models` holds every alias sharing that key — see
    PROVIDERS["groq"] for a provider with more than one.
    """

    key: str
    label: str
    models: tuple[ProviderModel, ...]
    needs_key: bool
    needs_base: bool
    env_vars: tuple[str, ...]
    hint: str


PROVIDERS: dict[str, ProviderSpec] = {
    "openai": ProviderSpec(
        key="openai",
        label="OpenAI",
        models=(ProviderModel(model_name="gpt-4o-mini", label="gpt-4o-mini", vision=True),),
        needs_key=True,
        needs_base=False,
        env_vars=("OPENAI_API_KEY",),
        hint="Set OPENAI_API_KEY in .env, then `docker compose up -d llm-gateway`.",
    ),
    "gemini": ProviderSpec(
        key="gemini",
        label="Google Gemini",
        models=(ProviderModel(model_name="gemini-flash", label="gemini-3.1-flash-lite", vision=True),),
        needs_key=True,
        needs_base=False,
        env_vars=("GOOGLE_API_KEY",),
        hint="Free-tier default. Set GOOGLE_API_KEY in .env, then `docker compose up -d llm-gateway`.",
    ),
    "deepseek": ProviderSpec(
        key="deepseek",
        label="DeepSeek",
        models=(ProviderModel(model_name="deepseek-chat", label="deepseek-chat"),),
        needs_key=True,
        needs_base=False,
        env_vars=("DEEPSEEK_API_KEY",),
        hint="Set DEEPSEEK_API_KEY in .env, then `docker compose up -d llm-gateway`.",
    ),
    "groq": ProviderSpec(
        key="groq",
        label="GroqCloud",
        # Same GROQ_API_KEY backs both — see litellm_config.yaml's groq-llama/
        # groq-oss-20b entries. gpt-oss-120b is more capable but its free-tier
        # tokens-per-minute quota (~8k) 429s once a tool result/history grows the
        # prompt; its smaller sibling gpt-oss-20b trades accuracy for a much higher
        # quota.
        models=(
            ProviderModel(model_name="groq-llama", label="gpt-oss-120b (accurate, low free-tier quota)"),
            ProviderModel(model_name="groq-oss-20b", label="gpt-oss-20b (faster, higher free-tier quota)"),
        ),
        needs_key=True,
        needs_base=False,
        env_vars=("GROQ_API_KEY",),
        hint="Free tier. Set GROQ_API_KEY in .env, then `docker compose up -d llm-gateway`.",
    ),
    "openrouter": ProviderSpec(
        key="openrouter",
        label="OpenRouter",
        models=(ProviderModel(model_name="openrouter", label="openrouter/free"),),
        needs_key=True,
        needs_base=False,
        env_vars=("OPENROUTER_API_KEY",),
        hint="Set OPENROUTER_API_KEY in .env, then `docker compose up -d llm-gateway`.",
    ),
    "anthropic": ProviderSpec(
        key="anthropic",
        label="Anthropic Claude",
        models=(ProviderModel(model_name="claude-haiku", label="claude-haiku-4-5", vision=True),),
        needs_key=True,
        needs_base=False,
        env_vars=("ANTHROPIC_API_KEY",),
        hint="Set ANTHROPIC_API_KEY in .env, then `docker compose up -d llm-gateway`.",
    ),
    "azure_openai": ProviderSpec(
        key="azure_openai",
        label="Azure OpenAI",
        models=(ProviderModel(model_name="azure-gpt4o", label="azure-gpt4o", vision=True),),
        needs_key=True,
        needs_base=True,
        env_vars=("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_API_BASE"),
        hint=(
            "Set AZURE_OPENAI_API_KEY and AZURE_OPENAI_API_BASE in .env. Also edit the "
            "`azure/<deployment>` line in services/llm-gateway/litellm_config.yaml to your "
            "actual deployment name (it can't be env-substituted — see that file's comment), "
            "then `docker compose up -d --build llm-gateway`."
        ),
    ),
    "ollama": ProviderSpec(
        key="ollama",
        label="Ollama (local)",
        models=(ProviderModel(model_name="llama3", label="llama3.2:3b"),),
        needs_key=False,
        needs_base=True,
        env_vars=("OLLAMA_API_BASE",),
        hint=(
            "Optional, no API key needed: run `make ollama-up` to start docker-compose.yml's "
            "`ollama` service (off by default), which auto-pulls llama3.2:3b (~2GB) — the 1B tier "
            "is too inaccurate for real chat use, so this stack doesn't bundle it. First run can "
            "take a minute; retest if it times out. Set OLLAMA_API_BASE in .env to point at a "
            "different (3B+) Ollama model instead."
        ),
    ),
}


def find_model(model_name: str) -> tuple[ProviderSpec, ProviderModel] | None:
    """Look up which provider (and which of its models) a litellm alias belongs to —
    the active-model picker only stores the bare `model_name`, so this is how a caller
    (e.g. `get_llm_provider_display`) recovers the provider label and model label.
    """
    for spec in PROVIDERS.values():
        for model in spec.models:
            if model.model_name == model_name:
                return spec, model
    return None


class LlmGatewayError(RuntimeError):
    """Raised when llm-gateway's admin API call fails outright (network/5xx)."""


def _client(base_url: str, master_key: str) -> httpx.Client:
    return httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {master_key}"}, timeout=10.0)


def list_providers(base_url: str, master_key: str) -> list[dict[str, object]]:
    """Real, live status per provider, nested one entry per model it routes: the
    underlying model/deployment and api_base litellm is actually routing to
    (visible), and whether a route exists at all. litellm redacts `api_key` in this
    same admin response for env-substituted keys, so key *presence* is never reported
    here — click "Test" for the real, load-bearing answer to "is this one actually
    working".
    """
    with _client(base_url, master_key) as client:
        try:
            response = client.get("/model/info")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LlmGatewayError(f"Could not reach llm-gateway: {exc}") from exc
    deployments = response.json().get("data", [])
    by_model_name = {d.get("model_name"): d for d in deployments}

    results: list[dict[str, object]] = []
    for spec in PROVIDERS.values():
        models: list[dict[str, object]] = []
        for model in spec.models:
            deployment = by_model_name.get(model.model_name)
            litellm_params = (deployment or {}).get("litellm_params", {}) or {}
            configured_model = litellm_params.get("model")
            models.append({
                "model_name": model.model_name,
                "label": model.label,
                "route_exists": deployment is not None,
                "model": configured_model.split("/", 1)[-1] if configured_model else None,
                "api_base": litellm_params.get("api_base"),
                "vision": model.vision,
            })
        results.append({
            "provider": spec.key,
            "label": spec.label,
            "needs_key": spec.needs_key,
            "needs_base": spec.needs_base,
            "env_vars": list(spec.env_vars),
            "hint": spec.hint,
            "models": models,
        })
    return results


def test_provider(base_url: str, master_key: str, provider: str, model_name: str) -> dict[str, object]:
    """Round-trip a minimal real chat completion through one of the given provider's
    models — the actual, live answer to "is this model working right now". `model_name`
    must be one of `provider`'s `ProviderSpec.models` (a provider's other models share
    its API key but can behave differently — e.g. hit a different free-tier quota).
    """
    spec = PROVIDERS.get(provider)
    if spec is None:
        raise ValueError(f"Unknown provider {provider!r}")
    if not any(model.model_name == model_name for model in spec.models):
        raise ValueError(f"Model {model_name!r} does not belong to provider {provider!r}")

    started = time.monotonic()
    with _client(base_url, master_key) as client:
        try:
            response = client.post(
                "/chat/completions",
                json={
                    "model": model_name,
                    "messages": [{"role": "user", "content": "Reply with exactly one word: ok"}],
                    "max_tokens": 5,
                },
                # 10s, not litellm's own (much longer) provider-level retry/timeout budget —
                # an unreachable Ollama base is the common case this guards against: without
                # this, a single dead local Ollama container can make "Test"/"Test All" hang
                # far longer than any of the real cloud providers ever would.
                timeout=10.0,
            )
        except httpx.HTTPError as exc:
            return {"ok": False, "error": str(exc), "latency_ms": None}

    latency_ms = round((time.monotonic() - started) * 1000, 1)
    if response.status_code != 200:
        try:
            detail = str(response.json().get("error", {}).get("message", response.text))
        except ValueError:
            detail = response.text
        return {"ok": False, "error": detail[:400], "latency_ms": latency_ms}

    reply = response.json()["choices"][0]["message"]["content"]
    return {"ok": True, "error": None, "latency_ms": latency_ms, "reply": reply[:200]}


def test_all_providers(base_url: str, master_key: str) -> dict[str, dict[str, dict[str, object]]]:
    """Round-trip every model of every provider concurrently (one thread per model,
    each opening its own httpx.Client) — the Settings page's "Test All" button.
    Returns `{provider: {model_name: result}}`, one entry per `ProviderSpec.models`
    entry across all of `PROVIDERS`; models without a configured key fail
    individually (surfaced per-model) rather than blocking the rest.
    """
    jobs = [(spec.key, model.model_name) for spec in PROVIDERS.values() for model in spec.models]
    with ThreadPoolExecutor(max_workers=len(jobs) or 1) as pool:
        futures = {
            (provider, model_name): pool.submit(test_provider, base_url, master_key, provider, model_name)
            for provider, model_name in jobs
        }
        results: dict[str, dict[str, dict[str, object]]] = {}
        for (provider, model_name), future in futures.items():
            results.setdefault(provider, {})[model_name] = future.result()
        return results
