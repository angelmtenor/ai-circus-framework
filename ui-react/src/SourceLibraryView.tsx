/**
 * A conversational_rag scenario's documents tab (`ui_extras: {kind: source_library}`): what the assistant
 * can really search, read back from the tenant's collection, so a document that was not indexed
 * simply is not here. Plain DOM with nothing running; a document's passages load when it is opened.
 * They are the indexed chunks, as plain text: chunking flattens a document's lines, so markdown would
 * misrender, and they overlap. This is what the assistant reads, not a pretty copy of the document.
 */
import { useEffect, useMemo, useState } from "react";
import { indexedSources, sourcePassages, type IndexedSources, type SourceLibraryExtra } from "./apiClient";
import { config } from "./config";
import "./sourceLibrary.css";

const folderOf = (source: string) => (source.includes("/") ? source.slice(0, source.lastIndexOf("/")) : "");
const nameOf = (source: string) => source.slice(source.lastIndexOf("/") + 1).replace(/\.(md|txt|pdf)$/i, "");

export function SourceLibraryView({ scenarioSlug, extras, accessToken }: { scenarioSlug: string; extras: SourceLibraryExtra; accessToken: string | null }) {
  const [data, setData] = useState<IndexedSources | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<{ source: string; passages: string[] | null } | null>(null);

  useEffect(() => {
    let live = true;
    indexedSources(config.ragAgentUrl, scenarioSlug, accessToken)
      .then((d) => live && setData(d))
      .catch((e: Error) => live && setError(e.message));
    return () => {
      live = false;
    };
  }, [scenarioSlug, accessToken]);

  const folders = useMemo(() => {
    const q = query.trim().toLowerCase();
    const groups = new Map<string, { source: string; passages: number }[]>();
    for (const item of data?.sources ?? []) {
      if (q && !item.source.toLowerCase().includes(q)) continue;
      const folder = folderOf(item.source);
      const members = groups.get(folder);
      if (members) members.push(item);
      else groups.set(folder, [item]);
    }
    return [...groups.entries()].sort((a, b) => a[0].localeCompare(b[0], undefined, { numeric: true }));
  }, [data, query]);

  function read(source: string) {
    setOpen({ source, passages: null });
    setError(null);
    sourcePassages(config.ragAgentUrl, scenarioSlug, source, accessToken)
      .then((r) => setOpen((current) => (current?.source === source ? { source, passages: r.passages } : current)))
      .catch((e: Error) => setError(e.message));
  }

  if (error && !data) return <div className="error">Could not load the documents: {error}</div>;
  if (!data) return <div className="panel-hint">Loading the documents…</div>;
  const total = data.sources.reduce((sum, s) => sum + s.passages, 0);

  return (
    <div className="library">
      <div className="library-list panel-card">
        <h3>{extras.title}</h3>
        <p className="panel-hint">
          {data.sources.length} documents, {total} passages{data.truncated ? " (the list is cut short)" : ""}.
        </p>
        <input type="search" placeholder="Filter by name…" aria-label="Filter documents" value={query} onChange={(e) => setQuery(e.target.value)} />
        {data.sources.length === 0 && <p className="panel-hint">Nothing has been indexed for your organisation yet.</p>}
        <div className="library-folders">
          {folders.map(([folder, members]) => {
            const docs = members.map((m) => (
              <button key={m.source} className={`library-doc${open?.source === m.source ? " library-doc--on" : ""}`} title={m.source} onClick={() => read(m.source)} aria-pressed={open?.source === m.source}>
                <span>{nameOf(m.source)}</span>
                <small>{m.passages}</small>
              </button>
            ));
            // One folder says nothing: just the documents.
            if (folders.length === 1) return <div key={folder}>{docs}</div>;
            return (
              <details key={folder} open={folders.length <= 4 || query !== ""}>
                <summary>
                  {folder || "Documents"} <span>{members.length}</span>
                </summary>
                {docs}
              </details>
            );
          })}
        </div>
      </div>
      <div className="library-reader panel-card">
        {error && <p className="error">{error}</p>}
        {!open && <p className="panel-hint">Open a document to read the passages the assistant searches.</p>}
        {open && (
          <>
            <h3>{nameOf(open.source)}</h3>
            <p className="panel-hint">{open.source}: the passages as indexed, which overlap, not the formatted document.</p>
            {open.passages === null && !error && <p className="panel-hint">Loading…</p>}
            {open.passages?.map((text, i) => (
              <section key={i} className="library-passage">
                <span className="library-passage-index">{i + 1}</span>
                <p>{text}</p>
              </section>
            ))}
          </>
        )}
      </div>
    </div>
  );
}
