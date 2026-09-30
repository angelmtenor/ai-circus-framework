import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useCopilotReadable } from "@copilotkit/react-core";
import { predict, type NetworkExplorerExtra, type ScenarioSummary } from "./apiClient";
import { config } from "./config";
import { explain, loadNetworkCached, Timeline, type Detail, type Loaded } from "./NetworkExplorerView";
import {
  exposureOf,
  hypotheticalSubject,
  layoutEgo,
  monthlyVolume,
  nearestTargets,
  peerShares,
  rankShare,
  realSubject,
  tieDerivedFeatures,
  type Draft,
  type Subject,
} from "./networkInvestigator";
import { EgoScene } from "./networkInvestigatorScene";
import { formatPush, periodLabel, pct, plural, searchNodes, shapScale, tierOf, compact, type NxNode } from "./networkModel";
import { FeatureInput, featureLabel, initialRecord, type Record_ } from "./predictUtils";
import { NETWORK_PALETTE, RISK_RAMP, surfaceMode, TIER_GLYPHS, tierColor } from "./riskPalette";
import { useTheme } from "./useTheme";
import "./networkExplorer.css";
import "./networkInvestigator.css";

/**
 * "Investigate a person" — the second workspace of the Network tab (NetworkTab.tsx).
 * Pick anyone (or invent a new individual) and see their ego network: who they
 * exchange mail with, month by month, how many hops away the nearest persons of
 * interest are and by which route, how exposed they are next to their peers — beside
 * the same out-of-fold score and SHAP explanation as the map. A *new individual* is
 * scored live by the deployed model from a profile the analyst sets and the contacts
 * they pick (features the scenario declares as `tie_features` are derived from those
 * ties). Nothing here is stored. A link to a person of interest is proximity, never
 * evidence: the wording below says so wherever it matters.
 */

type TargetMode = "record" | "model";
type Score = { probability: number; contributions: Record<string, number> };

const MAX_VOLUME = 600;

/** "person of interest" -> "persons of interest": only the head noun takes the s. */
function pluralPhrase(phrase: string): string {
  const [head, ...rest] = phrase.split(" of ");
  return rest.length ? `${plural(head)} of ${rest.join(" of ")}` : plural(phrase);
}

export function NetworkInvestigatorView({
  scenario,
  accessToken,
  focusId,
  onFocus,
  draft,
  onDraft,
  onOpenMap,
}: {
  scenario: ScenarioSummary;
  accessToken: string | null;
  focusId: string | null;
  onFocus: (id: string | null) => void;
  draft: Draft;
  onDraft: (update: (d: Draft) => Draft) => void;
  onOpenMap: (id: string | null) => void;
}) {
  const extras = scenario.ui_extras as NetworkExplorerExtra;
  const { theme } = useTheme();
  const surface = surfaceMode(theme.cssVars["--bg"]);
  const ramp = RISK_RAMP[surface];
  const palette = NETWORK_PALETTE[surface];
  const noun = extras.entity_noun;
  const features = useMemo(() => scenario.feature_columns ?? [], [scenario.feature_columns]);
  const schema = scenario.feature_schema;

  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  const [targetMode, setTargetMode] = useState<TargetMode>("record");
  const [history, setHistory] = useState<string[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hover, setHover] = useState<{ id: string; x: number; y: number } | null>(null);
  const [routeTarget, setRouteTarget] = useState<string | null>(null);
  const [period, setPeriod] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [contactQuery, setContactQuery] = useState("");
  const [draftScore, setDraftScore] = useState<Score | null>(null);
  const [draftBusy, setDraftBusy] = useState(false);
  const [draftError, setDraftError] = useState<string | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);

  const stageRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const sceneRef = useRef<EgoScene | null>(null);
  const selectRef = useRef<(id: string | null) => void>(() => undefined);
  const reduced = typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

  const flagTier = useMemo(() => {
    const i = extras.tiers.findIndex((t) => t.label === extras.flag_from_tier);
    return i >= 0 ? i : extras.tiers.length - 1;
  }, [extras.tiers, extras.flag_from_tier]);

  // ── data ─────────────────────────────────────────────────────────────────
  useEffect(() => {
    const { promise, drop } = loadNetworkCached(scenario, extras, accessToken);
    let cancelled = false;
    promise
      .then((data) => !cancelled && setLoaded(data))
      .catch((e: Error) => {
        drop();
        if (!cancelled) setError(e.message);
      });
    return () => {
      cancelled = true;
    };
  }, [scenario, extras, accessToken]);

  const net = loaded?.net ?? null;
  const scale = shapScale(loaded?.card?.model_name);
  const isTarget = useCallback(
    (n: NxNode) => n.kind === "row" && !!n.row && (targetMode === "record" ? n.row.actual === 1 : n.row.tier >= flagTier),
    [targetMode, flagTier],
  );
  const targetNoun = targetMode === "record" ? "person of interest" : `${extras.tiers[flagTier].label}-tier ${noun}`;
  const ranked = useMemo(() => (net ? [...net.rows].filter((n) => n.degree > 0).sort((a, b) => b.row!.probability - a.row!.probability) : []), [net]);
  const targetIds = useMemo(() => new Set(net ? net.rows.filter(isTarget).map((n) => n.id) : []), [net, isTarget]);
  const sourceRecords = useMemo(() => new Map((net?.rows ?? []).map((n) => [n.id, n.row!.record])), [net]);
  const peers = useMemo(() => (net ? peerShares(net, isTarget) : []), [net, isTarget]);

  // ── the hypothetical individual ──────────────────────────────────────────
  const tieMap = extras.tie_features ?? null;
  const defaults = useMemo(() => (schema ? initialRecord(features, schema) : {}), [features, schema]);
  const derived = useMemo(() => (net && tieMap ? tieDerivedFeatures(net, draft.contacts, tieMap, sourceRecords) : {}), [net, tieMap, draft.contacts, sourceRecords]);
  const draftRecord = useMemo(() => {
    const record: Record_ = { ...defaults, ...draft.record };
    for (const [feature, value] of Object.entries(derived)) {
      if (draft.manual.includes(feature)) continue;
      const spec = schema?.[feature];
      record[feature] = spec?.type === "numeric" ? Math.min(spec.max, Math.max(spec.min, value)) : value;
    }
    return record;
  }, [defaults, draft.record, draft.manual, derived, schema]);

  useEffect(() => {
    if (!draft.active) return;
    let cancelled = false;
    setDraftBusy(true);
    const timer = window.setTimeout(() => {
      predict(config.predictionUrl, scenario.slug, [draftRecord], accessToken)
        .then((response) => {
          if (cancelled) return;
          const p = response.predictions[0];
          setDraftScore({ probability: p.prediction, contributions: p.contributions });
          setDraftError(null);
        })
        .catch((e: Error) => !cancelled && setDraftError(e.message))
        .finally(() => !cancelled && setDraftBusy(false));
    }, 300);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [draft.active, draftRecord, scenario.slug, accessToken]);

  // ── the subject ──────────────────────────────────────────────────────────
  const subject: Subject | null = useMemo(() => {
    if (!net) return null;
    if (draft.active) {
      const score = draftScore ? { probability: draftScore.probability, tier: tierOf(draftScore.probability, extras.tiers) } : null;
      return hypotheticalSubject(net, draft.name, draft.contacts, score);
    }
    const node = (focusId && net.byId.get(focusId)) || ranked[0];
    return node ? realSubject(net, node) : null;
  }, [net, draft.active, draft.name, draft.contacts, draftScore, focusId, ranked, extras.tiers]);

  const routes = useMemo(() => (net && subject ? nearestTargets(net, subject, isTarget, 6) : []), [net, subject, isTarget]);
  const exposure = useMemo(() => (net && subject ? exposureOf(net, subject, isTarget) : null), [net, subject, isTarget]);
  const layout = useMemo(() => (net && subject ? layoutEgo(subject, routes, isTarget, net) : null), [net, subject, routes, isTarget]);
  const monthly = useMemo(() => (net && subject && !subject.virtual ? monthlyVolume(net, subject, isTarget) : null), [net, subject, isTarget]);
  const highlight = useMemo(() => {
    const route = routes.find((r) => r.target.id === routeTarget);
    return new Set(route ? [subject?.id ?? "", ...route.path.map((n) => n.id)] : []);
  }, [routes, routeTarget, subject?.id]);

  // the explanation: out-of-fold SHAP for a real row, the live prediction for a draft
  useEffect(() => {
    setDetail(null);
    if (!subject) return;
    if (subject.virtual) {
      if (draftScore) setDetail(explain(features, draftScore.probability, draftScore.contributions, scale));
      return;
    }
    const row = subject.node?.row;
    if (!row) return;
    if (row.contributions) {
      setDetail(explain(features, row.probability, row.contributions, scale));
      return;
    }
    let cancelled = false;
    predict(config.predictionUrl, scenario.slug, [row.record], accessToken)
      .then((r) => !cancelled && setDetail(explain(features, r.predictions[0].prediction, r.predictions[0].contributions, scale)))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [subject, draftScore, features, scale, scenario.slug, accessToken]);

  const pillarBars = useMemo(() => {
    if (!detail) return [];
    const byFeature = new Map(detail.items.map((i) => [i.feature, i.value]));
    const inPillar = new Set(extras.pillars.flatMap((p) => p.features));
    const bars = extras.pillars.map((p) => ({ label: p.label, value: p.features.reduce((s, f) => s + (byFeature.get(f) ?? 0), 0) }));
    const rest = detail.items.filter((i) => !inPillar.has(i.feature)).reduce((s, i) => s + i.value, 0);
    if (Math.abs(rest) > 1e-6) bars.push({ label: "Other", value: rest });
    return bars;
  }, [detail, extras.pillars]);
  const pillarMax = Math.max(1e-6, ...pillarBars.map((b) => Math.abs(b.value)));

  // ── the stage ────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!net || !canvasRef.current || !stageRef.current) return;
    const scene = new EgoScene(canvasRef.current, {
      onHover: (id, x, y) => setHover(id ? { id, x, y } : null),
      onSelect: (id) => selectRef.current(id),
    });
    sceneRef.current = scene;
    const stage = stageRef.current;
    scene.resize(stage.clientWidth, stage.clientHeight);
    const observer = new ResizeObserver(() => scene.resize(stage.clientWidth, stage.clientHeight));
    observer.observe(stage);
    setReady(true);
    return () => {
      observer.disconnect();
      scene.destroy();
      sceneRef.current = null;
    };
  }, [net]);

  useEffect(() => {
    if (layout && subject && net) sceneRef.current?.setLayout(layout, subject, net.periods.length);
  }, [layout, subject, net, ready]);

  useEffect(() => {
    sceneRef.current?.setView({ surface, period, hoveredId: hover?.id ?? null, selectedId, highlight, targetIds, reducedMotion: reduced });
  }, [surface, period, hover, selectedId, highlight, targetIds, reduced, layout, ready]);

  // Who is on the stage changes → forget the contact and route that were picked.
  useEffect(() => {
    setSelectedId(null);
    setRouteTarget(null);
  }, [subject?.id]);

  const recentre = useCallback(
    (id: string) => {
      if (!net?.byId.get(id)?.row) return;
      setHistory((h) => (subject && !subject.virtual ? [...h.slice(-12), subject.id] : h));
      onDraft((d) => ({ ...d, active: false }));
      onFocus(id);
    },
    [net, subject, onFocus, onDraft],
  );

  selectRef.current = (id) => {
    if (!id || !subject || id === subject.id) {
      setSelectedId(null);
      return;
    }
    const node = net?.byId.get(id);
    if (id === selectedId && node?.row) recentre(id);
    else setSelectedId(id);
  };

  // ── replay ───────────────────────────────────────────────────────────────
  const periods = net?.periods ?? [];
  useEffect(() => {
    if (!playing) return;
    const timer = window.setInterval(
      () => {
        setPeriod((p) => {
          const next = p === null ? 0 : p + 1;
          if (next >= periods.length) {
            setPlaying(false);
            return periods.length - 1;
          }
          return next;
        });
      },
      reduced ? 1100 : 640,
    );
    return () => window.clearInterval(timer);
  }, [playing, periods.length, reduced]);
  const togglePlay = () => {
    if (playing) return setPlaying(false);
    setPeriod((p) => (p === null || p >= periods.length - 1 ? 0 : p));
    setPlaying(true);
  };

  // ── search ───────────────────────────────────────────────────────────────
  const results = useMemo(() => (net && query ? searchNodes(net, query).filter((n) => n.kind === "row") : []), [net, query]);
  const contactResults = useMemo(
    () => (net && contactQuery ? searchNodes(net, contactQuery, 6).filter((n) => n.kind === "row" && !draft.contacts.some((c) => c.id === n.id)) : []),
    [net, contactQuery, draft.contacts],
  );

  function startDraft(from?: NxNode) {
    onDraft((d) => {
      if (from?.row) {
        const copied = realSubject(net!, from).contacts.filter((c) => c.node.kind === "row").slice(0, 8);
        return { ...d, active: true, name: `What if: ${from.label}`, record: { ...from.row.record }, manual: [], contacts: copied.map((c) => ({ id: c.node.id, volume: Math.min(MAX_VOLUME, Math.round(c.total)) })) };
      }
      return { ...d, active: true, name: d.name || "New individual" };
    });
    setSelectedId(null);
  }

  // ── chat context ─────────────────────────────────────────────────────────
  useCopilotReadable({
    description: `${scenario.title} "Investigate" workspace: one ${noun}'s ego network — their ties, how far the nearest ${pluralPhrase(targetNoun)} are and by which route, and (for a hypothetical new individual) the profile the analyst invented. Proximity to a person of interest is not evidence of wrongdoing; follow the scenario's guardrails when discussing named people.`,
    value: subject
      ? {
          subject: { name: subject.label, hypothetical: subject.virtual, score: subject.probability !== null ? Number(subject.probability.toFixed(3)) : null, tier: subject.tier !== null ? extras.tiers[subject.tier].label : null },
          targets_are: targetNoun,
          exposure,
          nearest_targets: routes.map((r) => ({ name: r.target.label, hops: r.hops, via: r.path.slice(1, -1).map((n) => (n.kind === "context" ? extras.context_label : n.label)) })),
          strongest_ties: subject.contacts.slice(0, 8).map((c) => ({ with: c.node.kind === "context" ? extras.context_label : c.node.label, [extras.link_noun]: c.total })),
          ...(subject.virtual ? { invented_profile: draftRecord } : {}),
        }
      : null,
  });

  if (error) return <div className="nx-empty">Could not load the network: {error}</div>;
  if (!loaded || !net || !subject || !exposure) {
    return (
      <div className="nx-empty nx-loading">
        <span className="nx-spinner" /> Building the network — scoring every {noun}…
      </div>
    );
  }

  const hoverNode = hover ? net.byId.get(hover.id) : null;
  const hoverContact = hover ? subject.contacts.find((c) => c.node.id === hover.id) : null;
  const q = query.trim();
  const peerPct = peers.length && subject.contacts.length ? rankShare(peers, exposure.targetShare) : null;
  const tier = subject.tier !== null ? extras.tiers[subject.tier] : null;
  const selectedNode = selectedId ? (net.byId.get(selectedId) ?? null) : null;
  const selectedContact = selectedId ? (subject.contacts.find((c) => c.node.id === selectedId) ?? null) : null;

  const glyph = (node: NxNode) => (node.kind === "entity" ? "⬢" : node.row ? TIER_GLYPHS[node.row.tier] : "·");
  const glyphColor = (node: NxNode) => (node.kind === "entity" ? palette.entity : node.row ? tierColor(surface, node.row.tier) : palette.context);
  const nameOf = (node: NxNode) => (node.kind === "context" ? extras.context_label : node.label);

  const hero = (
    <>
      <div className="nx-hero">
        <span className="nx-hero-value">{subject.probability !== null ? pct(subject.probability) : "…"}</span>
        {tier && (
          <span className="nx-hero-badge">
            {TIER_GLYPHS[subject.tier!]} {tier.label}
          </span>
        )}
        {subject.virtual && draftBusy && <span className="nx-spinner" aria-label="Scoring" />}
      </div>
      <p className="nx-muted nx-hero-note">
        {subject.virtual
          ? `Scored live by the deployed model from the profile and ties below — a hypothetical ${noun}, never stored.`
          : loaded.source === "out_of_fold"
            ? `Scored out of fold: the ${loaded.folds}-fold model that produced this score never saw this ${noun}'s own outcome.`
            : `Scored by the deployed model, which saw this ${noun} in training.`}
        {tier?.description ? ` ${tier.description}` : ""}
      </p>
      {!subject.virtual && subject.node?.row?.actual === 1 && targetMode === "record" && <p className="nx-outcome nx-outcome--on">◯ {extras.outcome_label}</p>}
      {draftError && subject.virtual && <p className="nx-muted">{draftError}</p>}
    </>
  );

  const exposurePanel = (
    <section>
      <h5>Exposure to {pluralPhrase(targetNoun)}</h5>
      <div className="ni-cards">
        <div className="ni-card" title={`Distinct two-way contacts on the map; how many of them are ${pluralPhrase(targetNoun)}`}>
          <b>{exposure.contacts}</b>
          <span>
            direct ties · <em>{exposure.targetContacts}</em> {exposure.targetContacts === 1 ? targetNoun : pluralPhrase(targetNoun)}
          </span>
        </div>
        <div className="ni-card" title={`Share of this ${noun}'s ${extras.link_noun} exchanged with ${pluralPhrase(targetNoun)}${peerPct !== null ? `; ${Math.round(peerPct * 100)}% of ${plural(noun)} with contacts have a share at or below it` : ""}`}>
          <b>{pct(exposure.targetShare)}</b>
          <span>of {extras.link_noun} with them{peerPct !== null ? ` · p${Math.round(peerPct * 100)} of peers` : ""}</span>
        </div>
        <div className="ni-card">
          <b>{exposure.nearest === null ? "—" : exposure.nearest}</b>
          <span>{exposure.nearest === null ? "no route to one" : exposure.nearest === 1 ? "hop to the nearest" : "hops to the nearest"}</span>
        </div>
        <div className="ni-card">
          <b>{exposure.within2}</b>
          <span>within two hops</span>
        </div>
      </div>
    </section>
  );

  const routePanel = (
    <section>
      <h5>Routes to the nearest {pluralPhrase(targetNoun)}</h5>
      {!routes.length && <p className="nx-muted">No route to a {targetNoun} on the map.</p>}
      <ul className="ni-routes">
        {routes.map((route) => {
          const on = routeTarget === route.target.id;
          const via = route.path.slice(subject.virtual ? 0 : 1, -1);
          return (
            <li key={route.target.id}>
              <button type="button" className={on ? "ni-route--on" : ""} aria-pressed={on} onClick={() => setRouteTarget(on ? null : route.target.id)}>
                <span className="nx-glyph" style={{ color: glyphColor(route.target) }}>
                  {glyph(route.target)}
                </span>
                <span className="ni-route-name">{route.target.label}</span>
                <span className="ni-route-hops">
                  {route.hops} hop{route.hops === 1 ? "" : "s"}
                </span>
              </button>
              <span className="ni-route-via">{via.length ? `via ${via.map(nameOf).join(" → ")}` : "a direct tie"}</span>
            </li>
          );
        })}
      </ul>
      {routes.length > 0 && <p className="nx-key">Click a route to light it up on the stage. Proximity is not evidence — colleagues write to each other.</p>}
    </section>
  );

  const tieChart = selectedContact && monthly && selectedContact.sentSeries && selectedContact.receivedSeries && <TieChart net={net} contact={selectedContact} palette={palette} period={period} />;

  const tiePanel = selectedNode && (
    <section className="ni-selected">
      <div className="nx-path-head">
        <h5>{selectedContact ? "Tie" : selectedNode.kind === "entity" ? "Documented entity" : "On the route"}</h5>
        <button type="button" onClick={() => setSelectedId(null)} aria-label="Close">
          ✕
        </button>
      </div>
      <p className="ni-selected-name">
        <span style={{ color: glyphColor(selectedNode) }}>{glyph(selectedNode)}</span> <b>{nameOf(selectedNode)}</b>
        {selectedNode.row && (
          <span className="nx-muted">
            {" "}
            · {selectedNode.detail} · score {pct(selectedNode.row.probability)}
            {targetIds.has(selectedNode.id) ? ` · ◯ ${targetNoun}` : ""}
          </span>
        )}
      </p>
      {selectedNode.kind === "entity" && selectedNode.description && <p className="nx-muted">{selectedNode.description}</p>}
      {selectedContact ? (
        <>
          <p>
            <b>{subject.label}</b> sent <b>{compact(selectedContact.sent)}</b> and received <b>{compact(selectedContact.received)}</b> {extras.link_noun} ({compact(selectedContact.total)} in all).
          </p>
          {tieChart}
        </>
      ) : (
        (() => {
          const rel = subject.node ? net.adjacency.get(subject.id)?.find((x) => x.node.id === selectedNode.id && !x.link.flow) : null;
          return rel ? (
            <p>
              {rel.link.source.id === subject.id ? "" : "← "}
              {rel.link.label ?? rel.link.kind}
              {rel.link.citation && <cite className="ni-cite">{rel.link.citation}</cite>}
            </p>
          ) : (
            <p className="nx-muted">No direct tie to {subject.label} — part of a route drawn on the stage.</p>
          );
        })()
      )}
      {selectedNode.row && (
        <div className="nx-dossier-actions ni-actions">
          <button type="button" onClick={() => recentre(selectedNode.id)}>
            Re-centre on {selectedNode.label.split(" ")[0]} ›
          </button>
          <button type="button" onClick={() => onOpenMap(selectedNode.id)}>
            Show on the map
          </button>
        </div>
      )}
    </section>
  );

  const ties = subject.contacts.filter((c) => c.node.kind === "row").slice(0, 8);
  const tiesPanel = ties.length > 0 && !subject.virtual && (
    <section>
      <h5>Strongest ties</h5>
      <ul className="nx-ties">
        {ties.map((c) => {
          const share = c.total ? c.sent / c.total : 0.5;
          return (
            <li key={c.node.id}>
              <button type="button" onClick={() => setSelectedId(c.node.id === selectedId ? null : c.node.id)}>
                <span className="nx-glyph" style={{ color: glyphColor(c.node) }}>
                  {glyph(c.node)}
                </span>
                <span className="nx-tie-name">
                  {c.node.label}
                  {targetIds.has(c.node.id) && <span className="ni-poi" title={targetNoun}> ◯</span>}
                </span>
                <span className="nx-tie-count" title={`sent ${c.sent} · received ${c.received}`}>
                  {compact(c.total)} {extras.link_noun}
                </span>
                <span className="ni-dir" aria-hidden title={`${Math.round(share * 100)}% sent by ${subject.label}`}>
                  <i style={{ width: `${share * 100}%`, background: palette.path }} />
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      <p className="nx-key">
        <span style={{ color: palette.path }}>■</span> sent by {subject.label.split(" ")[0]} <span style={{ color: palette.dim }}>■</span> received
      </p>
    </section>
  );

  const pillarsPanel = (
    <section>
      <h5>What drives the score — by pillar</h5>
      {!detail && <p className="nx-muted">Explaining…</p>}
      {detail && (
        <>
          <div className="nx-pillars">
            {pillarBars.map((bar) => {
              const w = (Math.abs(bar.value) / pillarMax) * 50;
              const up = bar.value >= 0;
              return (
                <div key={bar.label} className="nx-pillar" title={`${bar.label}: ${formatPush(bar.value, scale)}`}>
                  <span className="nx-pillar-label">{bar.label}</span>
                  <span className="nx-pillar-track">
                    <span className="nx-pillar-axis" />
                    <span className="nx-pillar-bar" style={{ width: `${w}%`, left: up ? "50%" : `${50 - w}%`, background: up ? ramp.up : ramp.down, borderRadius: up ? "0 4px 4px 0" : "4px 0 0 4px" }} />
                  </span>
                  <span className="nx-pillar-value">{formatPush(bar.value, scale)}</span>
                </div>
              );
            })}
          </div>
          <p className="nx-key">
            <span style={{ color: ramp.up }}>■</span> towards the profile <span style={{ color: ramp.down }}>■</span> away from it · average score {pct(detail.base, 1)}
          </p>
        </>
      )}
    </section>
  );

  // ── the new individual's form ────────────────────────────────────────────
  const setFeature = (feature: string, value: number | string) => onDraft((d) => ({ ...d, record: { ...d.record, [feature]: value } }));
  const tieFeatureSet = new Set(tieMap ? Object.values(tieMap).filter((f): f is string => !!f) : []);
  const draftForm = draft.active && (
    <>
      <section>
        <h5>Who they e-mail</h5>
        <div className="nx-search ni-contact-search">
          <input
            type="search"
            placeholder={`Add a contact — search a ${noun}…`}
            value={contactQuery}
            onChange={(e) => setContactQuery(e.target.value)}
            aria-label="Add a contact"
            onKeyDown={(e) => {
              if (e.key === "Enter" && contactResults[0]) {
                onDraft((d) => ({ ...d, contacts: [...d.contacts, { id: contactResults[0].id, volume: 40 }] }));
                setContactQuery("");
              }
            }}
          />
          {contactQuery.trim() && (
            <ul className="nx-search-list" role="listbox">
              {contactResults.map((node) => (
                <li
                  key={node.id}
                  role="option"
                  aria-selected={false}
                  onMouseDown={() => {
                    onDraft((d) => ({ ...d, contacts: [...d.contacts, { id: node.id, volume: 40 }] }));
                    setContactQuery("");
                  }}
                >
                  <span className="nx-glyph" style={{ color: glyphColor(node) }}>
                    {glyph(node)}
                  </span>
                  <span className="nx-search-name">{node.label}</span>
                  <span className="nx-search-detail">{node.detail}</span>
                  {targetIds.has(node.id) && <span className="nx-search-score">◯</span>}
                </li>
              ))}
              {!contactResults.length && <li className="nx-search-none">No match</li>}
            </ul>
          )}
        </div>
        {draft.contacts.length === 0 && <p className="nx-muted">No ties yet — pick the people this {noun} would exchange {extras.link_noun} with.</p>}
        <ul className="ni-contacts">
          {draft.contacts.map((c) => {
            const node = net.byId.get(c.id);
            if (!node) return null;
            return (
              <li key={c.id}>
                <span className="nx-glyph" style={{ color: glyphColor(node) }}>
                  {glyph(node)}
                </span>
                <span className="ni-contact-name">
                  {node.label}
                  {targetIds.has(c.id) && <span className="ni-poi"> ◯</span>}
                </span>
                <input
                  type="range"
                  min={2}
                  max={MAX_VOLUME}
                  step={2}
                  value={c.volume}
                  aria-label={`${extras.link_noun} with ${node.label}`}
                  onChange={(e) => onDraft((d) => ({ ...d, contacts: d.contacts.map((x) => (x.id === c.id ? { ...x, volume: Number(e.target.value) } : x)) }))}
                />
                <span className="ni-contact-volume">{c.volume}</span>
                <button type="button" aria-label={`Remove ${node.label}`} onClick={() => onDraft((d) => ({ ...d, contacts: d.contacts.filter((x) => x.id !== c.id) }))}>
                  ✕
                </button>
              </li>
            );
          })}
        </ul>
        {draft.contacts.length > 0 && <p className="nx-key">Volume = {extras.link_noun} exchanged, half each way.</p>}
      </section>

      <section>
        <h5>Profile</h5>
        <p className="nx-muted nx-small">Features built from the ties above are filled in for you; override any of them by hand.</p>
        {extras.pillars.map((pillar) => (
          <details key={pillar.label} className="ni-pillar" open={pillar === extras.pillars[0]}>
            <summary>{pillar.label}</summary>
            {pillar.features.map((feature) => {
              const spec = schema?.[feature];
              if (!spec) return null;
              const auto = tieFeatureSet.has(feature) && !draft.manual.includes(feature);
              if (auto) {
                return (
                  <div key={feature} className="ni-auto">
                    <span>{featureLabel(scenario, feature)}</span>
                    <b>{compact(Number(draftRecord[feature]))}</b>
                    <em>from ties</em>
                    <button type="button" onClick={() => onDraft((d) => ({ ...d, manual: [...d.manual, feature], record: { ...d.record, [feature]: draftRecord[feature] } }))}>
                      Override
                    </button>
                  </div>
                );
              }
              return (
                <div key={feature} className="ni-feature">
                  <FeatureInput feature={feature} spec={spec} value={draftRecord[feature]} onChange={(v) => setFeature(feature, v)} />
                  {tieFeatureSet.has(feature) && (
                    <button type="button" className="ni-reset" onClick={() => onDraft((d) => ({ ...d, manual: d.manual.filter((f) => f !== feature) }))}>
                      Use the ties
                    </button>
                  )}
                </div>
              );
            })}
          </details>
        ))}
      </section>
    </>
  );

  const fromOptions = ranked.slice(0, 40);
  const series = monthly && subject.contacts.length ? monthly : null;

  return (
    <div className={`nx ni nx--${surface}`} data-nx-ready={ready && layout ? "true" : "false"}>
      <header className="nx-head">
        <div>
          <h3>Investigate a {noun}</h3>
          <p>
            Pick anyone to see who they exchange {extras.link_noun} with, month by month — and how far the nearest {pluralPhrase(targetNoun)} are. Or invent a new individual: set their profile,
            choose who they write to, and watch their score and exposure move.
          </p>
        </div>
      </header>

      <div className="nx-toolbar">
        <div className="nx-search">
          <input
            type="search"
            role="combobox"
            aria-expanded={searchOpen && results.length > 0}
            aria-controls="ni-search-list"
            aria-autocomplete="list"
            placeholder={`Search a ${noun} to investigate…`}
            value={query}
            onChange={(e) => {
              setQuery(e.target.value);
              setSearchOpen(true);
            }}
            onFocus={() => setSearchOpen(true)}
            onBlur={() => window.setTimeout(() => setSearchOpen(false), 150)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && results[0]) {
                recentre(results[0].id);
                setQuery("");
              }
            }}
          />
          {searchOpen && q && (
            <ul id="ni-search-list" className="nx-search-list" role="listbox">
              {results.map((node) => (
                <li
                  key={node.id}
                  role="option"
                  aria-selected={false}
                  onMouseDown={() => {
                    recentre(node.id);
                    setQuery("");
                  }}
                >
                  <span className="nx-glyph" style={{ color: glyphColor(node) }}>
                    {glyph(node)}
                  </span>
                  <span className="nx-search-name">{node.label}</span>
                  <span className="nx-search-detail">{node.detail}</span>
                  {node.row && <span className="nx-search-score">{pct(node.row.probability)}</span>}
                </li>
              ))}
              {!results.length && <li className="nx-search-none">No match</li>}
            </ul>
          )}
        </div>
        <button type="button" className="ni-btn" disabled={!history.length || draft.active} onClick={() => history.length && (onFocus(history[history.length - 1]), setHistory((h) => h.slice(0, -1)))}>
          ‹ Back
        </button>
        <button type="button" className={`ni-btn ni-btn--new${draft.active ? " ni-btn--on" : ""}`} onClick={() => (draft.active ? onDraft((d) => ({ ...d, active: false })) : startDraft())}>
          {draft.active ? "✕ Close new individual" : "＋ New individual"}
        </button>
        <div className="nx-seg" role="radiogroup" aria-label="What counts as a target">
          {(
            [
              ["record", "Persons of interest"],
              ["model", "Model-flagged"],
            ] as [TargetMode, string][]
          ).map(([m, label]) => (
            <button key={m} type="button" role="radio" aria-checked={targetMode === m} className={targetMode === m ? "nx-seg--on" : ""} onClick={() => setTargetMode(m)} title={m === "record" ? extras.outcome_label : `${extras.tiers[flagTier].label} tier or above, by the out-of-fold score`}>
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="ni-chips" role="group" aria-label="Start from">
        <span className="nx-muted">Highest scores:</span>
        {ranked.slice(0, 9).map((node) => (
          <button key={node.id} type="button" className={`nx-chip${!draft.active && subject.id === node.id ? " nx-chip--on" : ""}`} onClick={() => recentre(node.id)}>
            <span style={{ color: glyphColor(node) }}>{glyph(node)}</span> {node.label}
          </button>
        ))}
      </div>

      <div className="nx-main">
        <div className="nx-stage ni-stage" ref={stageRef}>
          <canvas ref={canvasRef} role="img" aria-label={`Ego network of ${subject.label}: ${exposure.contacts} direct ties. ${routes.length ? `The nearest ${targetNoun} is ${routes[0].hops} hop${routes[0].hops === 1 ? "" : "s"} away.` : `No route to a ${targetNoun}.`} The panel beside it lists every tie and route.`} />
          {(!ready || !layout) && (
            <div className="nx-stage-loading">
              <span className="nx-spinner" /> Drawing the ties…
            </div>
          )}
          {hoverNode && hover && (
            <div className="nx-tooltip" style={{ left: hover.x > (stageRef.current?.clientWidth ?? 0) - 290 ? hover.x - 274 : hover.x + 14, top: hover.y + 14 }}>
              <b>{hover.id === subject.id ? subject.label : nameOf(hoverNode)}</b>
              {hoverNode.row && hover.id !== subject.id && (
                <>
                  <span>
                    {hoverNode.detail} · <span style={{ color: tierColor(surface, hoverNode.row.tier) }}>{TIER_GLYPHS[hoverNode.row.tier]}</span> {extras.tiers[hoverNode.row.tier].label} · {pct(hoverNode.row.probability)}
                  </span>
                  {targetIds.has(hoverNode.id) && <span>◯ {targetNoun}</span>}
                </>
              )}
              {hoverContact && (
                <span>
                  {compact(hoverContact.sent)} sent · {compact(hoverContact.received)} received
                </span>
              )}
              {hoverNode.kind === "entity" && <span>{hoverNode.type}</span>}
              {hover.id !== subject.id && <span className="nx-muted">click to inspect · click again to re-centre</span>}
            </div>
          )}
          <div className="nx-legend" aria-hidden>
            <span>
              <i className="nx-dot" style={{ background: palette.path }} /> sent ·<i className="nx-dot" style={{ background: palette.ink }} /> received
            </span>
            <span>
              <i className="nx-dot" style={{ background: ramp.tiers[2] }} /> score, by tier
            </span>
            <span>
              <span className="ni-ring" /> {targetNoun}
            </span>
            <span>
              <i className="nx-hex" style={{ borderColor: palette.entity }} /> {extras.entity_label}
            </span>
            <span>
              <i className="nx-line nx-line--dash" style={{ borderColor: palette.entity }} /> documented relation
            </span>
            {subject.virtual && <span>┄ dashed ring = hypothetical</span>}
          </div>
        </div>

        <aside className="nx-dossier" aria-live="polite">
          <div className="nx-dossier-body" style={{ ["--tier" as string]: subject.tier !== null ? tierColor(surface, subject.tier) : palette.dim }} key={subject.id}>
            <div className="nx-dossier-head">
              <span className="nx-kind">{subject.virtual ? `hypothetical ${noun}` : noun}</span>
              {subject.virtual ? (
                <input className="ni-name" value={draft.name} aria-label="Name" maxLength={60} onChange={(e) => onDraft((d) => ({ ...d, name: e.target.value }))} />
              ) : (
                <h4>{subject.label}</h4>
              )}
              {!subject.virtual && (
                <p className="nx-muted">
                  {subject.detail} · id <code>{subject.id}</code>
                </p>
              )}
              <div className="nx-dossier-actions">
                {subject.virtual ? (
                  <label className="ni-from">
                    Start from
                    <select
                      value=""
                      onChange={(e) => {
                        const node = net.byId.get(e.target.value);
                        if (node) startDraft(node);
                      }}
                    >
                      <option value="">an average profile…</option>
                      {fromOptions.map((n) => (
                        <option key={n.id} value={n.id}>
                          {n.label}
                        </option>
                      ))}
                    </select>
                  </label>
                ) : (
                  <>
                    <button type="button" onClick={() => onOpenMap(subject.id)}>
                      Show on the map
                    </button>
                    <button type="button" onClick={() => startDraft(subject.node!)}>
                      What if… ›
                    </button>
                  </>
                )}
              </div>
            </div>
            {hero}
            {exposurePanel}
            {routePanel}
            {tiePanel}
            {draftForm}
            {tiesPanel}
            {pillarsPanel}
            {!subject.virtual && subject.relations.length > 0 && (
              <section>
                <h5>Documented relations</h5>
                <div className="nx-chips">
                  {subject.relations.map((n) => (
                    <button key={n.id} type="button" className="nx-chip" onClick={() => setSelectedId(n.id)}>
                      <span style={{ color: palette.entity }}>⬢</span> {n.label}
                    </button>
                  ))}
                </div>
              </section>
            )}
          </div>
        </aside>
      </div>

      {series ? (
        <Timeline
          net={net}
          extras={extras}
          period={period}
          playing={playing}
          totals={series.all}
          overlay={series.target}
          caption={`${subject.label}'s ${extras.link_noun} per month · the red part is with ${pluralPhrase(targetNoun)}`}
          onPeriod={(p) => {
            setPlaying(false);
            setPeriod(p);
          }}
          onTogglePlay={togglePlay}
        />
      ) : (
        <p className="nx-muted ni-note">
          {subject.virtual ? `A hypothetical ${noun} has no mail history — their ties are drawn at the volume you set.` : `No ${extras.link_noun} on the map for this ${noun}.`}
        </p>
      )}
      {period !== null && subject.contacts.length > 0 && <p className="nx-muted ni-note">Showing {periodLabel(net.periods[period])} — ties fade and flow with that month's {extras.link_noun}.</p>}

      {extras.disclaimer && <p className="nx-disclaimer">{extras.disclaimer}</p>}
    </div>
  );
}

// ── one tie's months ───────────────────────────────────────────────────────

function TieChart({ net, contact, palette, period }: { net: import("./networkModel").Network; contact: import("./networkInvestigator").Contact; palette: (typeof NETWORK_PALETTE)["dark"] | (typeof NETWORK_PALETTE)["light"]; period: number | null }) {
  const sent = contact.sentSeries!;
  const received = contact.receivedSeries!;
  const n = net.periods.length;
  let max = 1;
  let peak = 0;
  for (let i = 0; i < n; i++) {
    max = Math.max(max, sent[i], received[i]);
    if (sent[i] + received[i] > sent[peak] + received[peak]) peak = i;
  }
  const w = 1000 / n;
  return (
    <div className="ni-tiechart">
      <svg viewBox="0 0 1000 80" preserveAspectRatio="none" aria-hidden>
        <line x1={0} x2={1000} y1={40} y2={40} className="ni-axis" vectorEffect="non-scaling-stroke" />
        {Array.from({ length: n }, (_, i) => (
          <g key={i} opacity={period === null || i === period ? 1 : 0.35}>
            <rect x={i * w + w * 0.12} width={w * 0.76} y={40 - (sent[i] / max) * 38} height={(sent[i] / max) * 38} fill={palette.path} />
            <rect x={i * w + w * 0.12} width={w * 0.76} y={40} height={(received[i] / max) * 38} fill={palette.dim} />
          </g>
        ))}
        {period !== null && <rect x={period * w} width={w} y={0} height={80} className="ni-cursor" />}
      </svg>
      <p className="nx-key">
        <span style={{ color: palette.path }}>▲</span> sent <span style={{ color: palette.dim }}>▼</span> received · busiest month <b>{sent[peak] + received[peak] ? periodLabel(net.periods[peak]) : "—"}</b> ({Math.round(sent[peak] + received[peak])})
      </p>
    </div>
  );
}
