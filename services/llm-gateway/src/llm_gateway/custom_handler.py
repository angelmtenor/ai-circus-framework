"""Local sentence-transformers embeddings, registered as a LiteLLM custom provider.

litellm_config.yaml wires this up via `litellm_settings.custom_provider_map` under
the `local-embed` model_name, so callers (etl-vectorize, rag-agent, form-agent) get
local, no-API-key embeddings through this gateway's ordinary OpenAI-compatible
`/embeddings` endpoint — the same call shape they already use for chat completions —
instead of each importing sentence_transformers (and its torch dependency) directly.

See https://docs.litellm.ai/docs/providers/custom_llm_server for the CustomLLM
extension point this subclasses.

Author: ai-circus-framework contributors
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

import httpx
from litellm import CustomLLM
from litellm.types.utils import EmbeddingResponse, Usage

# litellm strips the registered model_name/provider prefix before calling this
# handler, so `model` normally arrives empty (no per-request override path exists
# through the OpenAI-compatible /embeddings schema) — this is the actual HF model id
# sentence-transformers loads. Kept identical to the old in-process default so
# EMBEDDING_PROVIDER=local produces the same vector space as before this moved here.
DEFAULT_MODEL: Final = "voyageai/voyage-4-nano"

_loaded_models: dict[str, Any] = {}


def _get_model(model_name: str) -> Any:
    """Load (and cache) the sentence-transformers model, lazily.

    Imported lazily, same reasoning as the old LocalEmbeddingProvider: this module
    is only ever exercised when litellm_config.yaml's custom_provider_map is used,
    so services/tests that never hit that path don't pay for importing torch.
    """
    if model_name not in _loaded_models:
        from sentence_transformers import SentenceTransformer

        _loaded_models[model_name] = SentenceTransformer(model_name)
    return _loaded_models[model_name]


class LocalEmbeddingLLM(CustomLLM):
    """Runs a sentence-transformers model in-process to answer litellm embedding calls."""

    def embedding(
        self,
        model: str,
        input: list,  # ruff: ignore[builtin-argument-shadowing] - name fixed by litellm.CustomLLM's base signature
        model_response: EmbeddingResponse,
        print_verbose: Callable,
        logging_obj: Any,
        optional_params: dict,
        api_key: str | None = None,
        api_base: str | None = None,
        timeout: float | httpx.Timeout | None = None,
        litellm_params: Any = None,
    ) -> EmbeddingResponse:
        """Embed `input` with the sentence-transformers model, normalized like the OpenAI API."""
        vectors = _get_model(model or DEFAULT_MODEL).encode(input, normalize_embeddings=True)
        model_response.data = [
            {"object": "embedding", "index": i, "embedding": [float(v) for v in vector]}
            for i, vector in enumerate(vectors)
        ]
        model_response.model = model or DEFAULT_MODEL
        model_response.usage = Usage(prompt_tokens=0, completion_tokens=0, total_tokens=0)
        return model_response

    async def aembedding(
        self,
        model: str,
        input: list,  # ruff: ignore[builtin-argument-shadowing] - name fixed by litellm.CustomLLM's base signature
        model_response: EmbeddingResponse,
        print_verbose: Callable,
        logging_obj: Any,
        optional_params: dict,
        api_key: str | None = None,
        api_base: str | None = None,
        timeout: float | httpx.Timeout | None = None,
        litellm_params: Any = None,
    ) -> EmbeddingResponse:
        """Async entry point — sentence-transformers has no async API, so just delegate."""
        return self.embedding(
            model,
            input,
            model_response,
            print_verbose,
            logging_obj,
            optional_params,
            api_key,
            api_base,
            timeout,
            litellm_params,
        )


local_embedding_llm: Final = LocalEmbeddingLLM()
