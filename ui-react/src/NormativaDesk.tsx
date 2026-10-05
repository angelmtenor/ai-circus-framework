import { useEffect, useMemo, useState } from "react";
import type { Message as AguiMessage } from "@ag-ui/core";
import {
  createConversation,
  getConversationMessages,
  listConversations,
  referenceDocuments,
  type DocumentTool,
  type ReferenceDocument,
  type ScenarioSummary,
} from "./apiClient";
import { documentForLegalBasis, WORDS } from "./caseDeskLogic";
import { ChatPanel } from "./ChatPanel";
import { sourceName } from "./chatProvenance";
import { config } from "./config";
import { useScenarioAgent } from "./useScenarioAgent";

/**
 * The case desk's reading room for a scenario whose assistant searches reference
 * documents (`documents.tool`): every document the tool can return, grouped by law and
 * marked official or fictional, a reading pane, and a chat pinned to the documents-only
 * scope (the assistant gets that one tool — enforced server-side). The articles the
 * latest answer cited light up in the index. A rule's legal basis opened from a case
 * ("📖") lands here on its article, with the question already asked.
 */

export type LawFocus = { id: number; basis: string; question: string };

// One conversation per user for this chat, found again by its title — so the reading
// room's history survives tab switches and does not collide with the dock's.
function useReadingRoomConversation(slug: string, title: string, accessToken: string | null) {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [history, setHistory] = useState<AguiMessage[]>([]);
  useEffect(() => {
    let cancelled = false;
    listConversations(config.assistantUrl, slug, accessToken)
      .then((list) => list.find((c) => c.title === title) ?? createConversation(config.assistantUrl, slug, title, accessToken))
      .then(async (conversation) => {
        if (cancelled) return;
        const messages = await getConversationMessages(config.assistantUrl, slug, conversation.id, accessToken).catch(() => []);
        if (cancelled) return;
        setHistory(messages.map((m) => ({ id: m.id, role: m.role, content: m.content }) as AguiMessage));
        setConversationId(conversation.id);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [slug, title, accessToken]);
  return { conversationId, history };
}

/** "Bases AES de Villaclara · Artículo 4. Requisitos" → ["Bases AES de Villaclara", "Artículo 4. Requisitos"]. */
function splitTitle(title: string): [string, string] {
  const at = title.indexOf(" · ");
  return at < 0 ? ["", title] : [title.slice(0, at), title.slice(at + 3)];
}

const isFictional = (doc: ReferenceDocument) => /ficticio|fictional|fictitious/i.test(doc.note);

function Article({ doc }: { doc: ReferenceDocument }) {
  const [group, heading] = splitTitle(doc.title);
  const blocks = doc.text
    .split(/\n\s*\n/)
    .map((b) => b.trim())
    .filter((b) => b && !b.startsWith("# ") && !b.startsWith(">"));
  return (
    <article className="nd-article">
      <small className="nd-article-group">{group}</small>
      <h4>{heading}</h4>
      <p className={`nd-note${isFictional(doc) ? " nd-note--fictional" : ""}`}>{doc.note}</p>
      {blocks.map((block, i) =>
        block.startsWith("## ") ? <h5 key={i}>{block.slice(3)}</h5> : <p key={i}>{block}</p>,
      )}
    </article>
  );
}

export function NormativaDesk({
  scenario,
  documentTool,
  locale,
  accessToken,
  focus,
}: {
  scenario: ScenarioSummary;
  documentTool: DocumentTool;
  locale: "es" | "en";
  accessToken: string | null;
  focus: LawFocus | null;
}) {
  const w = WORDS[locale];
  const [docs, setDocs] = useState<ReferenceDocument[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  // The article the user opened — it wins until a newer `focus` (a "📖" from a case) arrives.
  const [userPick, setUserPick] = useState<{ name: string; focusId: number | null } | null>(null);
  const [filter, setFilter] = useState("");
  const [cited, setCited] = useState<string[]>([]);
  const { conversationId, history } = useReadingRoomConversation(scenario.slug, `📚 ${documentTool.label}`, accessToken);
  const agent = useScenarioAgent(config.assistantUrl, scenario.slug, conversationId ?? scenario.slug, accessToken);

  useEffect(() => {
    let cancelled = false;
    referenceDocuments(config.assistantUrl, scenario.slug, accessToken)
      .then((list) => !cancelled && setDocs(list))
      .catch((e: Error) => !cancelled && setError(e.message));
    return () => {
      cancelled = true;
    };
  }, [scenario.slug, accessToken]);

  const citedNames = useMemo(() => new Set(cited.map(sourceName)), [cited]);
  const focusDoc = useMemo(() => (focus && docs ? documentForLegalBasis(docs, focus.basis) : null), [focus, docs]);
  const focusId = focus?.id ?? null;
  // Shown: the user's pick (unless a newer legal basis was opened), else that legal
  // basis's article, else the first article the latest answer cited.
  const selected =
    (userPick && userPick.focusId === focusId ? userPick.name : null) ??
    focusDoc?.name ??
    userPick?.name ??
    docs?.find((d) => citedNames.has(sourceName(d.name)))?.name ??
    null;
  const setSelected = (name: string) => setUserPick({ name, focusId });

  const groups = useMemo(() => {
    const q = filter.trim().toLowerCase();
    const out = new Map<string, ReferenceDocument[]>();
    for (const doc of docs ?? []) {
      if (q && !doc.title.toLowerCase().includes(q) && !doc.text.toLowerCase().includes(q)) continue;
      const [group] = splitTitle(doc.title);
      out.set(group, [...(out.get(group) ?? []), doc]);
    }
    // Fictional local rules first (what the case is judged by), then the laws.
    return [...out.entries()].sort(([, a], [, b]) => Number(isFictional(b[0])) - Number(isFictional(a[0])));
  }, [docs, filter]);

  const open = docs?.find((d) => d.name === selected) ?? null;

  return (
    <div className="nd">
      <p className="cd-note nd-intro">{w.lawIntro}</p>
      <div className="nd-grid">
        <nav className="nd-index" aria-label={documentTool.label}>
          <input type="search" value={filter} placeholder={w.filterLaw} onChange={(e) => setFilter(e.target.value)} />
          {error && <div className="cd-error">{error}</div>}
          {!docs && !error && <span className="cd-spinner" />}
          {groups.map(([group, members]) => (
            <div key={group} className="nd-group">
              <h6>
                {group}
                <span className={`nd-badge${isFictional(members[0]) ? " nd-badge--fictional" : ""}`}>
                  {isFictional(members[0]) ? w.fictional : w.official}
                </span>
              </h6>
              <ul>
                {members.map((doc) => {
                  const isCited = citedNames.has(sourceName(doc.name));
                  return (
                    <li key={doc.name}>
                      <button
                        className={`${doc.name === selected ? "active " : ""}${isCited ? "cited" : ""}`}
                        onClick={() => setSelected(doc.name)}
                        title={isCited ? w.cited : undefined}
                      >
                        {isCited && <span aria-label={w.cited}>●</span>} {splitTitle(doc.title)[1]}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </nav>
        <div className="nd-reader">{open ? <Article doc={open} /> : <div className="cd-empty cd-empty--inline">{w.pickArticle}</div>}</div>
        <div className="nd-chat">
          <ChatPanel
            agent={agent}
            baseUrl={config.assistantUrl}
            scenarioSlug={scenario.slug}
            sampleQuestions={documentTool.sample_questions}
            accessToken={accessToken}
            variant="full"
            title={`📚 ${documentTool.label}`}
            initialMessages={history}
            conversationReady={conversationId !== null}
            documentTool={documentTool}
            locale={locale}
            lockedScope="documents"
            prompt={focus ? { id: focus.id, text: focus.question } : null}
            onReplySources={setCited}
          />
        </div>
      </div>
    </div>
  );
}
