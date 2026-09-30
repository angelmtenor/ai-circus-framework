# ⚖️ AML Rulebook Knowledge Graph — GraphRAG over EU anti-money-laundering law

> Domain: **Banking & finance** · Kind: `conversational_rag` + a knowledge graph · Scenario: [`scenarios/aml_regulation_kg/scenario.yaml`](../../scenarios/aml_regulation_kg/scenario.yaml)

The sibling of [Follow the Money](aml_money_trail.md). There, the graph is made of **entities**
(banks, account holders, payments) and a model scores them. Here, the graph is made of
**knowledge**: concepts typed by a small ontology and the relations between them, taken from the
law a compliance officer works under. Once the Money Trail tab has flagged an account holder,
the next question is *"what must the bank do now, towards whom, and how fast?"*. This scenario
answers that from Regulation (EU) 2024/1624 (AMLR). Each answer comes from a small, cited
subgraph, and the panel next to the chat lights that subgraph up.

| | |
|---|---|
| **Corpus** | 18 of the AMLR's 90 articles, unmodified (8,468 words): obliged entities (3), compliance functions (11), customer due diligence (19–21), ongoing monitoring (26), enhanced due diligence (34), shell banks (39), politically exposed persons (42, 45, 46), beneficial ownership (52), reporting suspicions (69), refraining from transactions (71), tipping-off (73), record retention (77), anonymous accounts (79), the cash limit (80) |
| **Ontology** | 7 classes: *Obliged entity*, *Obligation*, *Trigger*, *Party*, *Authority*, *Record*, *Limit*. 9 relations: `must_perform`, `must_not`, `may`, `triggered_by`, `applies_to`, `reports_to`, `requires`, `has_limit`, `is_a`. Plus one *Article* node per source document |
| **Graph** | 135 nodes (117 concepts + 18 articles), 144 relations, 159 `cited_in` links. **Every relation cites its article and paragraph and carries a verbatim quote** |
| **Retrieval** | HippoRAG 2-style GraphRAG inside rag-agent's LangGraph agent (see below) |
| **Source** | EUR-Lex / Publications Office, CELEX 32024R1624, SHA-256 pinned. © European Union; reuse authorised by Decision 2011/833/EU |

## How the graph is built (once, offline)

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

## How a question is answered (every time, capped)

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

## The panel

The workspace is split in two: the chat on the left and the knowledge graph on the right.

- **Colour = role, glyph = class.** Colour marks the role: actors (obliged entities ⬢, parties ◆,
  authorities ■) in blue, obligations ● in orange, conditions (triggers ▲, limits ⬭, records ■) in
  aqua. Articles are recessive grey. Only three hues pass the palette validator for a spatial
  graph where any two nodes can touch, so the glyph tells classes apart and colour is never the
  only cue.
- **After each answer** the retrieved subgraph lights up and everything else dims. Seed concepts
  pulse, particles run along each relation in its direction, and the camera frames the subgraph.
  A chip reports how many relations the answer walked and which articles it drew on.
- **Click a concept** to see its relations, each with its article citation and the verbatim
  quote behind it. Neighbouring concepts and articles in the list are clickable too.
- With reduced motion the highlight is static.

*Machine-extracted from the regulation's text, with a quote behind every relation: a study aid,
not legal advice.*
