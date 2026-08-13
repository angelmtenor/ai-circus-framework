"""Pluggable embedding providers, selected at deployment time via env vars.

etl-vectorize (ingestion) and rag-agent (query) both embed text into the same
Qdrant vector space, so both must be built with the identical provider/model — a
mismatch doesn't raise, it just makes cosine similarity search silently return
garbage. `build_embedding_provider` is the single place either service should call
to get one, so switching providers only ever takes an env change plus a re-run of
etl-vectorize (which already drops and recreates each tenant's collection on every
run, so a new vector size needs no separate migration).
"""

from __future__ import annotations

from typing import Protocol

import httpx

DEFAULT_LOCAL_MODEL = "voyageai/voyage-4-nano"
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


class LocalEmbeddingProvider:
    """Runs a sentence-transformers model in-process — no API key, no network call.

    Imports `sentence_transformers` lazily so services that never select this
    provider (or don't even depend on the package, e.g. platform-registry) don't
    pay for pulling in torch just by importing this module.
    """

    def __init__(self, model_name: str = DEFAULT_LOCAL_MODEL) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)
        # Determined by a live probe encode() call rather than trusting
        # get_sentence_embedding_dimension()'s reported metadata: for at least one
        # real model (voyageai/voyage-4-nano) that value disagreed with what encode()
        # actually returns (2048 reported vs 1024 produced), which surfaces downstream
        # as a Qdrant "Vector dimension error" only once real documents are upserted.
        self.dimension = len(self.encode_query("dimension probe"))

    def encode_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of document chunks for storage."""
        return [[float(v) for v in vector] for vector in self._model.encode(texts, normalize_embeddings=True)]

    def encode_query(self, text: str) -> list[float]:
        """Embed a single search query."""
        return [float(v) for v in self._model.encode(text, normalize_embeddings=True)]


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
) -> EmbeddingProvider:
    """Build the embedding provider named by `provider` ("local" | "gemini" | "voyage").

    `model_name` overrides that provider's default model when set. The caller's
    EnvConfig validates `provider` against these three values (see settings.yaml's
    EMBEDDING_PROVIDER regex) — the ValueError branch below is a defense-in-depth
    fallback, not the primary validation path.
    """
    if provider == "local":
        return LocalEmbeddingProvider(model_name or DEFAULT_LOCAL_MODEL)
    if provider == "gemini":
        if not google_api_key:
            raise RuntimeError("EMBEDDING_PROVIDER=gemini requires GOOGLE_API_KEY to be set.")
        return GeminiEmbeddingProvider(google_api_key, model_name or DEFAULT_GEMINI_MODEL)
    if provider == "voyage":
        if not voyage_api_key:
            raise RuntimeError("EMBEDDING_PROVIDER=voyage requires VOYAGE_API_KEY to be set.")
        return VoyageEmbeddingProvider(voyage_api_key, model_name or DEFAULT_VOYAGE_MODEL)
    raise ValueError(f"Unknown EMBEDDING_PROVIDER={provider!r}; must be 'local', 'gemini', or 'voyage'.")
