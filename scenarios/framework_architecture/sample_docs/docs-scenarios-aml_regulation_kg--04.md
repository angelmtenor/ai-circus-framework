# ⚖️ AML Rulebook Knowledge Graph — GraphRAG over EU anti-money-laundering law - The panel

Source: docs/scenarios/aml_regulation_kg.md (this repository)

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
