"""Pluggable embedding providers, selected at deployment time via env vars.

etl-vectorize (ingestion) and rag-agent (query) both embed text into the same
Qdrant vector space, so both must be built with the identical provider/model — a
mismatch doesn't raise, it just makes cosine similarity search silently return
garbage. `build_embedding_provider` is the single place either service should call
to get one, so switching providers only ever takes an env change plus a re-run of
etl-vectorize (which already drops and recreates each tenant's collection on every
run, so a new vector size needs no separate migration).

"local" runs a sentence-transformers model, but not in this process: it calls
llm-gateway's OpenAI-compatible `/embeddings` route (registered as the `local-embed`
model — see llm-gateway/litellm_config.yaml and llm_gateway/custom_handler.py), the
same way every service already reaches llm-gateway for chat completions. That keeps
torch + sentence-transformers installed in exactly one image instead of once per
caller (etl-vectorize, rag-agent, form-agent all previously imported it directly).
"""

from __future__ import annotations

from typing import Protocol

import httpx

# The model_name llm-gateway's litellm_config.yaml registers the local
# sentence-transformers backend under — not a raw HF model id (that's fixed
# gateway-side; see custom_handler.py's DEFAULT_MODEL).
DEFAULT_LOCAL_MODEL = "local-embed"
DEFAULT_GEMINI_MODEL = "gemini-embedding-001"
DEFAULT_VOYAGE_MODEL = "voyage-3.5-lite"

_HTTP_TIMEOUT_SECONDS = 30.0


class EmbeddingProvider(Protocol):
    """Backs both ingestion and query-time embedding for one provider/model."""

    dimension: int

    def encode_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of document chunks for storage."""
        ...

    def encode_query(self, text: str) -> list[float]:
        """Embed a single search query."""
        ...


class GatewayEmbeddingProvider:
    """Local sentence-transformers embeddings, proxied through llm-gateway's
    OpenAI-compatible `/embeddings` endpoint instead of running in this process.

    `model_name` must be a `model_name` llm-gateway's litellm_config.yaml actually
    registers (default: "local-embed") — an override that doesn't match fails loudly
    with a 4xx from the gateway rather than silently loading some other model.
    """

    def __init__(self, base_url: str, api_key: str, model_name: str = DEFAULT_LOCAL_MODEL) -> None:
        self._model_name = model_name
        self._client = httpx.Client(
            base_url=base_url, headers={"Authorization": f"Bearer {api_key}"}, timeout=_HTTP_TIMEOUT_SECONDS
        )
        # Determined by a live probe call rather than hardcoded — see the other
        # providers' constructors for why this class of bug is worth guarding against.
        self.dimension = len(self.encode_query("dimension probe"))

    def _embed(self, texts: list[str]) -> list[list[float]]:
        response = self._client.post("/embeddings", json={"model": self._model_name, "input": texts})
        response.raise_for_status()
        return [item["embedding"] for item in response.json()["data"]]

    def encode_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of document chunks for storage."""
        return self._embed(texts)

    def encode_query(self, text: str) -> list[float]:
        """Embed a single search query."""
        return self._embed([text])[0]


class GeminiEmbeddingProvider:
    """Google's Gemini embeddings API, called directly over REST (no SDK dependency).

    https://ai.google.dev/api/embeddings
    """

    _BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, api_key: str, model_name: str = DEFAULT_GEMINI_MODEL) -> None:
        self._model_name = model_name
        self._client = httpx.Client(
            base_url=self._BASE_URL, headers={"x-goog-api-key": api_key}, timeout=_HTTP_TIMEOUT_SECONDS
        )
        # Determined by a live probe call rather than hardcoded: the API's default
        # output dimensionality isn't a stable constant we should bake in here.
        self.dimension = len(self.encode_query("dimension probe"))

    def encode_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of document chunks for storage."""
        requests = [
            {
                "model": f"models/{self._model_name}",
                "content": {"parts": [{"text": text}]},
                "taskType": "RETRIEVAL_DOCUMENT",
            }
            for text in texts
        ]
        response = self._client.post(f"/{self._model_name}:batchEmbedContents", json={"requests": requests})
        response.raise_for_status()
        return [item["values"] for item in response.json()["embeddings"]]

    def encode_query(self, text: str) -> list[float]:
        """Embed a single search query."""
        response = self._client.post(
            f"/{self._model_name}:embedContent",
            json={
                "model": f"models/{self._model_name}",
                "content": {"parts": [{"text": text}]},
                "taskType": "RETRIEVAL_QUERY",
            },
        )
        response.raise_for_status()
        return response.json()["embedding"]["values"]


class VoyageEmbeddingProvider:
    """Voyage AI's embeddings API, called directly over REST (no SDK dependency).

    https://docs.voyageai.com/reference/embeddings-api
    """

    _URL = "https://api.voyageai.com/v1/embeddings"

    def __init__(self, api_key: str, model_name: str = DEFAULT_VOYAGE_MODEL) -> None:
        self._model_name = model_name
        self._client = httpx.Client(headers={"Authorization": f"Bearer {api_key}"}, timeout=_HTTP_TIMEOUT_SECONDS)
        self.dimension = len(self.encode_query("dimension probe"))

    def _embed(self, texts: list[str], input_type: str) -> list[list[float]]:
        response = self._client.post(
            self._URL, json={"input": texts, "model": self._model_name, "input_type": input_type}
        )
        response.raise_for_status()
        return [item["embedding"] for item in response.json()["data"]]

    def encode_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of document chunks for storage."""
        return self._embed(texts, "document")

    def encode_query(self, text: str) -> list[float]:
        """Embed a single search query."""
        return self._embed([text], "query")[0]


def build_embedding_provider(
    provider: str,
    model_name: str | None,
    google_api_key: str | None,
    voyage_api_key: str | None,
    llm_gateway_url: str | None = None,
    llm_gateway_api_key: str | None = None,
) -> EmbeddingProvider:
    """Build the embedding provider named by `provider` ("local" | "gemini" | "voyage").

    `model_name` overrides that provider's default model when set. The caller's
    EnvConfig validates `provider` against these three values (see settings.yaml's
    EMBEDDING_PROVIDER regex) — the ValueError branch below is a defense-in-depth
    fallback, not the primary validation path. `llm_gateway_url`/`llm_gateway_api_key`
    are only required for provider="local" (see GatewayEmbeddingProvider).
    """
    if provider == "local":
        if not llm_gateway_url or not llm_gateway_api_key:
            raise RuntimeError("EMBEDDING_PROVIDER=local requires LLM_GATEWAY_URL and LLM_GATEWAY_API_KEY to be set.")
        return GatewayEmbeddingProvider(llm_gateway_url, llm_gateway_api_key, model_name or DEFAULT_LOCAL_MODEL)
    if provider == "gemini":
        if not google_api_key:
            raise RuntimeError("EMBEDDING_PROVIDER=gemini requires GOOGLE_API_KEY to be set.")
        return GeminiEmbeddingProvider(google_api_key, model_name or DEFAULT_GEMINI_MODEL)
    if provider == "voyage":
        if not voyage_api_key:
            raise RuntimeError("EMBEDDING_PROVIDER=voyage requires VOYAGE_API_KEY to be set.")
        return VoyageEmbeddingProvider(voyage_api_key, model_name or DEFAULT_VOYAGE_MODEL)
    raise ValueError(f"Unknown EMBEDDING_PROVIDER={provider!r}; must be 'local', 'gemini', or 'voyage'.")
