# ⚖️ AML Rulebook Knowledge Graph — GraphRAG over EU anti-money-laundering law - How the graph is built (once, offline)

Source: docs/scenarios/aml_regulation_kg.md (this repository)

[`scripts/prepare_aml_regulation_kg.py`](../../scripts/prepare_aml_regulation_kg.py) runs in two steps:

1. **`fetch`** (free, deterministic) downloads the regulation's XHTML, refuses it if its SHA-256
   differs from the reviewed copy, and cuts the 18 articles into `sample_docs/amlr-art-NNN-*.md`.
2. **`extract`** (spends LLM tokens, run once) sends each article through llm-gateway with the
   closed ontology. The LLM returns entities and relations, and every relation must include the
   paragraph it comes from and a quote from the article. The script then checks the output
   without calling the LLM again:
   - **Quote guard.** A relation whose quote is not literally in the article is dropped. An
     elided quote (`a … b`) passes only if every fragment appears in the article, in order.
     2 relations were dropped this way.
   - **Signatures.** An endpoint the LLM forgot to declare takes the class its relation implies.
     For example, whatever an entity `must_perform` is an *Obligation*.
   - **Stable ids.** Node ids are `class:slug`, so the same concept found in two articles
     becomes one node.

Each article's answer is cached, so a re-run costs nothing. The resulting
   `knowledge_graph.json` is committed after a human review. **The cluster pipeline never calls
   an LLM to build the graph.** One full pass cost roughly 80k tokens on Groq's `gpt-oss-120b`
   (4–5k per article).

Adding a knowledge graph to any `conversational_rag` scenario takes the same `documents.knowledge_graph`
block, no new code.
