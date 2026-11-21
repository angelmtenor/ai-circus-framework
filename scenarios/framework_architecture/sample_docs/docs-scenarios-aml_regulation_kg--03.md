# ⚖️ AML Rulebook Knowledge Graph — GraphRAG over EU anti-money-laundering law - How a question is answered (every time, capped)

Source: docs/scenarios/aml_regulation_kg.md (this repository)

etl-vectorize bootstraps the graph into the tenant's bucket (`kg/graph.json`). When the committed
graph changes, the next run replaces that copy; a graph a tenant uploaded itself is never
overwritten. It then indexes
every node and every relation (e.g. *"Report suspicion to FIU reports to FIU"*) as one point in
the tenant's `aml_regulation_kg_kg__<org>` Qdrant collection. It also adds **synonymy edges**
(`similar_to`, as in HippoRAG 2) between concepts of the same class whose embeddings are ≥ 0.85
apart, for example *"Report suspicion to FIU"* (art. 69) and *"Report suspicious transactions to
FIU"* (art. 11). That gave 13 edges here. Labels with digits are left out, because
*"5 years"* and *"1 year"* embed at 0.91. These edges are never merges and never citable facts:
relevance only flows along them. rag-agent's `graph_search` tool answers in four steps:

1. **Seed linking.** The question embedding is matched against nodes *and* relations
   (query-to-triple matching, as in HippoRAG 2). The agent also passes 2–4 **key concepts** in
   the same tool call, one per distinct thing asked (e.g. `["report suspicious transaction",
   "FIU", "deadline"]`), and each is linked separately. A multi-part question's single embedding
   lands on one part only: for *"what must the bank do, towards whom, and how fast?"* it found
   only due-diligence concepts. With the key concepts, the reporting articles (69, 71, 73) came
   back with 13 relations. Each seed is divided by √(number of articles it appears in), HippoRAG's
   node specificity.
2. **Personalized PageRank** from those seeds (damping 0.5), limited to 2 hops. Relevance spreads
   along relations, synonymy edges and article nodes, so a concept several seeds point at
   outranks a lone near-match.
3. **Capped context.** The top **30** relations are returned, one line each
   (`A --relation--> B [AMLR Art. 69(1)(a)]`). They are ranked by the PageRank mass of their two
   ends divided by √degree; without that, a hub such as *Credit institution* fills every line with
   its own obligations. The best **2** chunks from the highest-ranked articles are added, so the
   answer can quote the law.
4. **Trace.** Every node and relation returned is sent to the UI as a `knowledge_graph_trace`
   AG-UI event.

`graph_path` shows how two concepts are connected: up to 3 shortest chains of relations,
avoiding article nodes when a concept-only chain exists. Both tools run inside the same LangGraph
agent as every RAG scenario (`create_agent` + AG-UI). The prompt asks the agent to call
`graph_search` **once**, and the code enforces it. Models don't always listen: one retention
question made several searches (12.6k characters, 25 s). So a third call in the same turn gets a
one-line refusal, which brought that question down to 9.5k characters and 7.6 s.

**Measured on the five sample questions** (gemini-3.1-flash-lite, k3s):

- Retrieval context is 3.9–5k characters (≈ 1–1.3k tokens) per search.
- 5–12 s per answer.
- Every citation in the answers points to an article that is actually in the graph.
- Embedding a key concept costs about 0.07 s; the concepts themselves are a few tokens of the
  same tool call.
