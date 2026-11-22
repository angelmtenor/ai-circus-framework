# Architecture details - Knowledge graphs

Source: docs/architecture-details.md (this repository)

A `conversational_rag` scenario may ship a **knowledge graph** (`documents.knowledge_graph`, same
`NetworkGraph` contract: typed concept nodes, relations with `citation` + verbatim `evidence`, one node
per source document linked by `cited_in`): extracted *once, offline* by an LLM and committed
(`scripts/prepare_aml_regulation_kg.py` — quotes not found in the text are dropped; never extract in
the pipeline), bootstrapped by etl-vectorize into the tenant's bucket (`KNOWLEDGE_GRAPH_KEY`, re-synced when the committed
seed's SHA-256 changes; a tenant's own graph is never overwritten) and
indexed node-by-node and relation-by-relation in `kg_collection_name`. rag-agent then swaps
`retrieve_docs` for `graph_search` (HippoRAG 2-style: the question *plus 2-4 `key_concepts` the agent
names in the same call* → node/relation seeds weighted by specificity → Personalized PageRank within
`hops` over relations, `cited_in` and etl-vectorize's inferred `similar_to` synonymy edges → lines
ranked by degree-normalised mass, capped at `max_triples` + `max_passages` chunks and at
`MAX_GRAPH_SEARCHES_PER_RUN` — keep the caps, they are the per-question token bill) and `graph_path`, emits a `knowledge_graph_trace` AG-UI custom event
before `RUN_FINISHED`, and serves `GET /knowledge-graph/{slug}`; `ui_extras: knowledge_graph` splits
RagView into chat | `KnowledgeGraphView` (colour = role, glyph = class) lighting up each answer's subgraph.
