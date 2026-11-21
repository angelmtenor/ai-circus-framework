# ⚖️ AML Rulebook Knowledge Graph — GraphRAG over EU anti-money-laundering law

Source: docs/scenarios/aml_regulation_kg.md (this repository)

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
