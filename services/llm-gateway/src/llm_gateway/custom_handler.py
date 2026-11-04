"""Local sentence-transformers embeddings, registered as a LiteLLM custom provider.

litellm_config.yaml wires this up via `litellm_settings.custom_provider_map` under
the `local-embed` model_name, so callers (etl-vectorize, rag-agent, form-agent) get
local, no-API-key embeddings through this gateway's ordinary OpenAI-compatible
`/embeddings` endpoint — the same call shape they already use for chat completions —
instead of each importing sentence_transformers (and its torch dependency) directly.

See https://docs.litellm.ai/docs/providers/custom_llm_server for the CustomLLM
extension point this subclasses.

Deployed (the `docker` settings profile — compose and k8s) with EMBEDDING_PROVIDER=local,
the model is loaded and warmed when litellm imports this module at proxy start-up, before
the proxy serves anything — so its health checks, and the k8s readiness they gate, only
pass once embeddings are instant. Otherwise the first RAG/form question after every restart
would wait ~30 s for the load (and, on a fresh volume, the ~670 MB download). The weights
live on a persistent cache volume (k8s/base/llm-gateway.yaml, docker-compose.yml), so only
a brand-new cluster downloads them.

Author: Angel Martinez-Tenor
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Final

import httpx
from litellm import CustomLLM
from litellm.types.utils import EmbeddingResponse, Usage

from llm_gateway import get_env_config, get_logger

logger = get_logger(__name__)

# litellm strips the registered model_name/provider prefix before calling this
# handler, so `model` normally arrives empty (no per-request override path exists
# through the OpenAI-compatible /embeddings schema) — this is the actual HF model id
# sentence-transformers loads. Kept identical to the old in-process default so
# EMBEDDING_PROVIDER=local produces the same vector space as before this moved here.
DEFAULT_MODEL: Final = "voyageai/voyage-4-nano"

_loaded_models: dict[str, Any] = {}
_load_lock = threading.Lock()

# Longest text the ONNX path embeds (tokens); beyond it the text is truncated. The model itself allows
# 32768, but a run-away document would cost gigabytes of attention memory on a shared gateway pod.
ONNX_MAX_TOKENS: Final = 8192
_PAD_MARKER: Final = "<|endoftext|>"  # Qwen3's end-of-text, used as padding


def onnx_dir(model_name: str) -> Path:
    """Where `scripts/export_local_embed_onnx.py` + `make k3s-embed-onnx` put this model's ONNX export:
    `$LOCAL_EMBED_ONNX_DIR`, else `<HF_HOME>/onnx/<model name without its org>` on the model volume.
    """
    explicit = os.environ.get("LOCAL_EMBED_ONNX_DIR")
    if explicit:
        return Path(explicit)
    return Path(os.environ.get("HF_HOME", "~/.cache/huggingface")).expanduser() / "onnx" / model_name.rsplit("/", 1)[-1]


class OnnxEmbedder:
    """The same `encode(texts, normalize_embeddings=True)` as sentence-transformers, on ONNX Runtime.

    The exported graph returns per-token states (bidirectional encoder + head); like the original
    pipeline this masks padding, mean-pools and L2-normalises. No torch is imported.
    """

    def __init__(self, directory: Path) -> None:
        """Load `model.onnx` and `tokenizer.json` from `directory`."""
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self._tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
        self._tokenizer.enable_truncation(max_length=ONNX_MAX_TOKENS)
        pad_id = self._tokenizer.token_to_id(_PAD_MARKER)
        self._tokenizer.enable_padding(pad_id=pad_id if pad_id is not None else 0, pad_token=_PAD_MARKER)
        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, min(4, os.cpu_count() or 1))
        options.enable_mem_pattern = False  # lower peak memory; the arena is not worth it for sporadic batches
        self._session = ort.InferenceSession(
            str(directory / "model.onnx"), sess_options=options, providers=["CPUExecutionProvider"]
        )

    def encode(self, texts: list[str], normalize_embeddings: bool = True) -> list[Any]:
        """One vector per text (L2-normalised when asked)."""
        import numpy as np

        encodings = self._tokenizer.encode_batch(texts)
        ids = np.array([e.ids for e in encodings], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        states = np.asarray(self._session.run(None, {"input_ids": ids, "attention_mask": mask})[0])
        weights = mask[:, :, None].astype(states.dtype)
        pooled = (states * weights).sum(axis=1) / np.maximum(weights.sum(axis=1), 1.0)
        if normalize_embeddings:
            pooled = pooled / np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12)
        return list(pooled)


def _get_model(model_name: str) -> Any:
    """Load (and cache) the embedding model, lazily: the ONNX export when it is on the model volume
    (no torch), else sentence-transformers (the fallback until an export is installed).

    Imported lazily, same reasoning as the old LocalEmbeddingProvider: this module
    is only ever exercised when litellm_config.yaml's custom_provider_map is used,
    so services/tests that never hit that path don't pay for importing torch.
    """
    with _load_lock:  # aembedding runs on worker threads: load each model once, not per racer
        if model_name not in _loaded_models:
            directory = onnx_dir(model_name)
            if (directory / "model.onnx").exists() and (directory / "tokenizer.json").exists():
                logger.info("local-embed: ONNX Runtime export found in {}", directory)
                _loaded_models[model_name] = OnnxEmbedder(directory)
            else:
                logger.warning(
                    "local-embed: no ONNX export in {} — falling back to in-process torch (`make k3s-embed-onnx`)",
                    directory,
                )
                from sentence_transformers import SentenceTransformer

                _loaded_models[model_name] = SentenceTransformer(model_name)
        return _loaded_models[model_name]


def _encode(model: Any, texts: list[str]) -> list[Any]:
    """Normalized vectors for `texts`, an all-zero vector for each empty one.

    Some tokenizers (voyage-4-nano's) add no special tokens, so "" is zero tokens: alone
    in a batch it crashes the forward pass, padded among other texts it mean-pools to the
    zero vector. Always answering zeros makes "" batch-independent and matches the
    host-GPU embedding caches the text challengers were trained on.
    """
    filled = [i for i, text in enumerate(texts) if text != ""]
    if len(filled) == len(texts):
        return list(model.encode(texts, normalize_embeddings=True))
    # All empty: one throwaway encode gives the output width — get_sentence_embedding_dimension()
    # reports voyage-4-nano's backbone (2048), not the 1024 its pipeline actually returns.
    encoded = model.encode([texts[i] for i in filled] or ["."], normalize_embeddings=True)
    dimension = len(encoded[0])
    vectors: list[Any] = [[0.0] * dimension for _ in texts]
    for i, vector in zip(filled, encoded[: len(filled)], strict=True):
        vectors[i] = vector
    return vectors


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
        vectors = _encode(_get_model(model or DEFAULT_MODEL), input)
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
        """Async entry point. sentence-transformers has no async API and a CPU encode of
        an etl-vectorize batch takes seconds — run it on a worker thread, or LiteLLM's
        single event loop (which also streams every chat completion) stalls until the
        batch is done.
        """
        return await asyncio.to_thread(
            self.embedding,
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


def preload(model_name: str = DEFAULT_MODEL) -> None:
    """Load `model_name` and run one encode, so the first real call pays for neither
    (torch initializes its kernels lazily: the first encode is much slower than the rest).
    """
    started = time.perf_counter()
    _get_model(model_name).encode(["warm-up"], normalize_embeddings=True)
    logger.success("local-embed model {} loaded and warmed in {:.1f}s", model_name, time.perf_counter() - started)


def preload_enabled() -> bool:
    """Whether this deployment asked for the model to be ready before the proxy serves."""
    config = get_env_config()
    return config.LOCAL_EMBED_PRELOAD == "true" and (config.EMBEDDING_PROVIDER or "local") == "local"


local_embedding_llm: Final = LocalEmbeddingLLM()

# litellm imports this module while loading litellm_config.yaml's custom_provider_map, as
# part of the proxy's start-up — blocking here keeps /health/liveliness (and so the k8s
# startup/readiness probes) from answering until the model is warm. A failure is logged,
# never raised: the proxy must still come up for every other (cloud) model, and the
# first /embeddings call then retries the load — the old lazy behaviour, as a fallback.
if preload_enabled():
    try:
        preload()
    except Exception:
        logger.exception("Pre-loading the local-embed model failed — the first /embeddings call will retry it")
