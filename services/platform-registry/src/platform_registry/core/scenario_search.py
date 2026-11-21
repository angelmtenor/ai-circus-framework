"""
- Title:    Semantic scenario search: find a scenario by describing a problem in your own words
- Author:   Angel Martinez-Tenor

The scenario gallery's search box ranks scenarios by meaning ("a bank wants to catch accounts that launder money"
finds the money-mule and AML scenarios though they share few words with it). Each scenario is embedded once, from
what it already declares (title, description, domain, story, sample questions), through llm-gateway's `local-embed`
like every other embedding of the platform; a query is embedded when asked and compared by cosine.

The index is built in a background thread at start-up so the registry, which every service depends on, never waits
for the gateway; until it is ready the route says so and the UI ranks by words alone. Results are always limited to
the caller's entitled scenarios. Content-free: scenario texts are public catalogue content, queries are not logged.
"""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from typing import Any

from platform_registry.core.logger import get_logger

logger = get_logger(__name__)

MAX_CACHED_QUERIES = 256
# Small batches of short texts: llm-gateway's local embedding model shares a pod with the gateway itself, and one
# batch of every scenario's full text was enough to push that pod over its memory limit.
EMBED_BATCH = 4
MAX_TEXT_CHARS = 600
BUILD_ATTEMPTS = 40  # about ten minutes of retries while llm-gateway starts
BUILD_RETRY_SECONDS = 15.0

Embed = Callable[[list[str]], list[list[float]]]
EmbedOne = Callable[[str], list[float]]


def _unit(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


def scenario_text(scenario: Any) -> str:
    """What a scenario is about, in words: the text it is embedded from (an ORM row or a summary)."""
    story = getattr(scenario, "story", None) or {}
    parts = [
        scenario.title,
        scenario.description,
        str(getattr(scenario, "industry", "")).replace("_", " "),
        story.get("headline", ""),
        story.get("problem", ""),
        " ".join(story.get("audience", [])),
        " ".join((getattr(scenario, "sample_questions", None) or [])[:4]),
    ]
    return "\n".join(p for p in parts if p)[:MAX_TEXT_CHARS]


class ScenarioSearch:
    """An in-memory index of scenario embeddings (a few dozen vectors), thread-safe."""

    def __init__(self) -> None:
        """An empty index: `ready` stays false until `build`."""
        self._lock = threading.Lock()
        self._vectors: dict[str, list[float]] = {}
        self._embed_one: EmbedOne | None = None
        self._cache: OrderedDict[str, list[float]] = OrderedDict()

    @property
    def ready(self) -> bool:
        """Whether the scenarios are embedded and a query can be ranked."""
        return bool(self._vectors) and self._embed_one is not None

    def build(self, texts: dict[str, str], embed_documents: Embed, embed_query: EmbedOne) -> None:
        """Embed every scenario's text (one batch) and keep the query embedder."""
        slugs = sorted(texts)
        vectors: list[list[float]] = []
        for start in range(0, len(slugs), EMBED_BATCH):
            vectors += embed_documents([texts[s][:MAX_TEXT_CHARS] for s in slugs[start : start + EMBED_BATCH]])
        with self._lock:
            self._vectors = {slug: _unit(v) for slug, v in zip(slugs, vectors, strict=True)}
            self._embed_one = embed_query
            self._cache.clear()

    def _query_vector(self, query: str) -> list[float]:
        key = " ".join(query.lower().split())
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            embed = self._embed_one
        assert embed is not None
        vector = _unit(embed(query))
        with self._lock:
            self._cache[key] = vector
            while len(self._cache) > MAX_CACHED_QUERIES:
                self._cache.popitem(last=False)
        return vector

    def search(self, query: str, allowed: set[str]) -> list[tuple[str, float]]:
        """Every allowed scenario with its cosine similarity to the query, best first."""
        if not self.ready:
            return []
        q = self._query_vector(query)
        with self._lock:
            scored = [
                (slug, sum(a * b for a, b in zip(q, v, strict=True)))
                for slug, v in self._vectors.items()
                if slug in allowed
            ]
        return sorted(scored, key=lambda item: -item[1])


SEARCH = ScenarioSearch()


def start_background_build(
    load_texts: Callable[[], dict[str, str]], make_embedder: Callable[[], Any]
) -> threading.Thread:
    """Build SEARCH in a daemon thread, retrying while llm-gateway starts; a failure only leaves search lexical.

    The embedder is made inside the thread too: building it may wait for the gateway, which start-up must not.
    """

    def run() -> None:
        for attempt in range(1, BUILD_ATTEMPTS + 1):
            try:
                embedder = make_embedder()
                SEARCH.build(load_texts(), embedder.encode_documents, embedder.encode_query)
                logger.info("Scenario search ready: {} scenarios embedded", len(SEARCH._vectors))
                return
            except Exception as exc:  # the gateway may still be starting; any failure is retried, then given up
                if attempt == BUILD_ATTEMPTS:
                    logger.warning("Scenario search stays lexical: the scenarios could not be embedded ({})", exc)
                    return
                time.sleep(BUILD_RETRY_SECONDS)

    thread = threading.Thread(target=run, name="scenario-search-build", daemon=True)
    thread.start()
    return thread
