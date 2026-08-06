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
class ProviderSpec:
    """One provider's routing shape on the gateway, per litellm_config.yaml."""

    key: str
    label: str
    model_name: str
    needs_key: bool
    needs_base: bool
    env_vars: tuple[str, ...]
    hint: str


PROVIDERS: dict[str, ProviderSpec] = {
    "openai": ProviderSpec(
        key="openai",
        label="OpenAI",
        model_name="gpt-4o-mini",
        needs_key=True,
        needs_base=False,
        env_vars=("OPENAI_API_KEY",),
        hint="Set OPENAI_API_KEY in .env, then `docker compose up -d llm-gateway`.",
    ),
    "gemini": ProviderSpec(
        key="gemini",
        label="Google Gemini (2.5 Flash Lite)",
        model_name="gemini-flash",
        needs_key=True,
        needs_base=False,
        env_vars=("GOOGLE_API_KEY",),
        hint=("Free-tier default. Set GOOGLE_API_KEY in .env, then `docker compose up -d llm-gateway`."),
    ),
    "deepseek": ProviderSpec(
        key="deepseek",
        label="DeepSeek",
        model_name="deepseek-chat",
        needs_key=True,
        needs_base=False,
        env_vars=("DEEPSEEK_API_KEY",),
        hint="Set DEEPSEEK_API_KEY in .env, then `docker compose up -d llm-gateway`.",
    ),
    "groq": ProviderSpec(
        key="groq",
        label="GroqCloud",
        model_name="groq-llama",
        needs_key=True,
        needs_base=False,
        env_vars=("GROQ_API_KEY",),
        hint="Free tier. Set GROQ_API_KEY in .env, then `docker compose up -d llm-gateway`.",
    ),
    "openrouter": ProviderSpec(
        key="openrouter",
        label="OpenRouter",
        model_name="openrouter",
        needs_key=True,
        needs_base=False,
        env_vars=("OPENROUTER_API_KEY",),
        hint="Set OPENROUTER_API_KEY in .env, then `docker compose up -d llm-gateway`.",
    ),
    "azure_openai": ProviderSpec(
        key="azure_openai",
        label="Azure OpenAI",
        model_name="azure-gpt4o",
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
        model_name="llama3",
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


class LlmGatewayError(RuntimeError):
    """Raised when llm-gateway's admin API call fails outright (network/5xx)."""


def _client(base_url: str, master_key: str) -> httpx.Client:
    return httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {master_key}"}, timeout=10.0)


def list_providers(base_url: str, master_key: str) -> list[dict[str, object]]:
    """Real, live status per provider: the underlying model/deployment and api_base
    litellm is actually routing to (visible), and whether a route exists at all.
    litellm redacts `api_key` in this same admin response for env-substituted keys, so
    key *presence* is never reported here — click "Test" for the real, load-bearing
    answer to "is this one actually working".
    """
    with _client(base_url, master_key) as client:
        try:
            response = client.get("/model/info")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LlmGatewayError(f"Could not reach llm-gateway: {exc}") from exc
    deployments = response.json().get("data", [])
    by_model_name = {d.get("model_name"): d for d in deployments}

    results = []
    for spec in PROVIDERS.values():
        deployment = by_model_name.get(spec.model_name)
        litellm_params = (deployment or {}).get("litellm_params", {}) or {}
        configured_model = litellm_params.get("model")
        results.append({
            "provider": spec.key,
            "label": spec.label,
            "route_exists": deployment is not None,
            "model": configured_model.split("/", 1)[-1] if configured_model else None,
            "api_base": litellm_params.get("api_base"),
            "needs_key": spec.needs_key,
            "needs_base": spec.needs_base,
            "env_vars": list(spec.env_vars),
            "hint": spec.hint,
        })
    return results


def test_provider(base_url: str, master_key: str, provider: str) -> dict[str, object]:
    """Round-trip a minimal real chat completion through the given provider's model —
    the actual, live answer to "is this provider working right now".
    """
    spec = PROVIDERS.get(provider)
    if spec is None:
        raise ValueError(f"Unknown provider {provider!r}")

    started = time.monotonic()
    with _client(base_url, master_key) as client:
        try:
            response = client.post(
                "/chat/completions",
                json={
                    "model": spec.model_name,
                    "messages": [{"role": "user", "content": "Reply with exactly one word: ok"}],
                    "max_tokens": 5,
                },
                timeout=30.0,
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


def test_all_providers(base_url: str, master_key: str) -> dict[str, dict[str, object]]:
    """Round-trip every provider concurrently (one thread per provider, each opening
    its own httpx.Client) — the Settings page's "Test All" button. Providers without
    a configured key fail individually (surfaced per-provider below) rather than
    blocking the rest; this always returns one result per entry in PROVIDERS.
    """
    with ThreadPoolExecutor(max_workers=len(PROVIDERS)) as pool:
        futures = {key: pool.submit(test_provider, base_url, master_key, key) for key in PROVIDERS}
        return {key: future.result() for key, future in futures.items()}
