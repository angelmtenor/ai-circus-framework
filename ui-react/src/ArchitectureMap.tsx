import { CATALOG, COMPONENTS, REPO_DOCS, STACK, flowOrder, linkedTo, type ArchComponent, type ArchFlow, type ArchLayerId } from "./architectureCatalog";
import "./architecture.css";

/**
 * The platform drawn by layer, from the architecture catalog: the doors at the top, then what people use, the data
 * and the cluster, with observability alongside. A component can be lit (`highlight`: e.g. the ones a scenario uses),
 * selected, or numbered by a request `flow`. Static DOM: nothing animates.
 */
export function ArchitectureMap({
  highlight,
  selected,
  onSelect,
  flow,
  compact = false,
}: {
  highlight?: ReadonlySet<string>;
  selected?: string | null;
  onSelect?: (id: string) => void;
  flow?: ArchFlow | null;
  compact?: boolean;
}) {
  const order = flow ? flowOrder(flow) : null;
  // A followed request numbers its own components; otherwise the selection shows what it talks to.
  const linked = selected && !flow ? linkedTo(selected) : null;
  const lit = (c: ArchComponent) => (order ? order.has(c.id) : highlight ? highlight.has(c.id) : true);

  function card(c: ArchComponent) {
    const classes = [
      "arch-card",
      `arch-card--${c.layer}`,
      c.profile !== "core" ? "arch-card--optional" : "",
      lit(c) ? "arch-card--lit" : "arch-card--dim",
      selected === c.id ? "arch-card--selected" : "",
      linked?.has(c.id) ? "arch-card--linked" : "",
    ].join(" ");
    return (
      <button key={c.id} className={classes} onClick={() => onSelect?.(c.id)} title={c.role} aria-pressed={selected === c.id}>
        {order?.has(c.id) && <span className="arch-card-step">{order.get(c.id)}</span>}
        <span className="arch-card-name">{c.name}</span>
        {!compact && <span className="arch-card-tech">{c.tech}</span>}
        {c.profile === "optional" && !compact && <span className="arch-card-badge">optional</span>}
        {c.profile === "external" && !compact && <span className="arch-card-badge">outside the cluster</span>}
      </button>
    );
  }

  function band(id: ArchLayerId) {
    const layer = CATALOG.layers.find((l) => l.id === id)!;
    const items = CATALOG.components.filter((c) => c.layer === id);
    return (
      <section key={id} className={`arch-band arch-band--${id}`} aria-label={layer.title}>
        <header>
          <h4>{layer.title}</h4>
          {!compact && <p>{layer.text}</p>}
        </header>
        <div className="arch-cards">{items.map(card)}</div>
      </section>
    );
  }

  return (
    <div className={`arch-map${compact ? " arch-map--compact" : ""}`}>
      <div className="arch-people">People in a browser · chat apps and MCP clients · model providers outside the cluster</div>
      <div className="arch-grid">
        <div className="arch-stack">{STACK.map(band)}</div>
        <div className="arch-side">{band("observability")}</div>
      </div>
    </div>
  );
}

/** One component in words: what it does, how it is built, what it talks to, how to switch it on, its ADRs. */
export function ComponentDetail({
  id,
  reason,
  usedBy,
  onSelect,
}: {
  id: string;
  reason?: string | null;
  usedBy?: { slug: string; title: string; icon: string }[];
  onSelect?: (id: string) => void;
}) {
  const c = COMPONENTS.get(id);
  if (!c) return null;
  const incoming = CATALOG.components.filter((o) => o.talks_to.some((l) => l.to === id));
  return (
    <aside className={`arch-detail arch-detail--${c.layer}`}>
      <span className="arch-detail-layer">{CATALOG.layers.find((l) => l.id === c.layer)?.title}</span>
      <h3>{c.name}</h3>
      <p className="arch-detail-role">{c.role}</p>
      {reason && (
        <p className="arch-detail-reason">
          <b>In this scenario:</b> {reason}
        </p>
      )}
      <dl>
        <dt>Built with</dt>
        <dd>{c.tech}</dd>
        <dt>Runs</dt>
        <dd>{c.profile === "core" ? (c.enable ?? "Always, with make k3s-all") : c.profile === "external" ? "Outside the cluster" : (c.enable ?? "Optional")}</dd>
        {c.workloads.length > 0 && (
          <>
            <dt>Kubernetes</dt>
            <dd>{c.workloads.filter((w) => !w.startsWith("__")).join(", ") || "a Job per run"}</dd>
          </>
        )}
        {c.adr.length > 0 && (
          <>
            <dt>Decisions</dt>
            <dd>
              {c.adr.map((n) => {
                const file = CATALOG.adr_files[String(n)];
                const label = `ADR ${String(n).padStart(4, "0")}`;
                return file ? (
                  <a key={n} href={`${REPO_DOCS}adr/${file}`} target="_blank" rel="noreferrer">
                    {label}
                  </a>
                ) : (
                  <span key={n}>{label}</span>
                );
              })}
            </dd>
          </>
        )}
      </dl>
      {c.talks_to.length > 0 && (
        <>
          <h5>Talks to</h5>
          <ul className="arch-links">
            {c.talks_to.map((l) => (
              <li key={l.to}>
                <button onClick={() => onSelect?.(l.to)}>{COMPONENTS.get(l.to)?.name ?? l.to}</button> <span>{l.label}</span>
              </li>
            ))}
          </ul>
        </>
      )}
      {incoming.length > 0 && (
        <>
          <h5>Called by</h5>
          <ul className="arch-links">
            {incoming.map((o) => (
              <li key={o.id}>
                <button onClick={() => onSelect?.(o.id)}>{o.name}</button> <span>{o.talks_to.find((l) => l.to === id)?.label}</span>
              </li>
            ))}
          </ul>
        </>
      )}
      {usedBy && usedBy.length > 0 && (
        <>
          <h5>Used by {usedBy.length} of your scenarios</h5>
          <p className="arch-usedby">{usedBy.map((s) => `${s.icon} ${s.title}`).join(" · ")}</p>
        </>
      )}
    </aside>
  );
}

/** A request flow, step by step. */
export function FlowSteps({ flow, onSelect }: { flow: ArchFlow; onSelect?: (id: string) => void }) {
  return (
    <ol className="arch-steps">
      {flow.steps.map((s, i) => (
        <li key={i}>
          <span className="arch-step-n">{i + 1}</span>
          <span>
            <button onClick={() => onSelect?.(s.from)}>{COMPONENTS.get(s.from)?.name}</button> →{" "}
            <button onClick={() => onSelect?.(s.to)}>{COMPONENTS.get(s.to)?.name}</button>
            <small>{s.label}</small>
          </span>
        </li>
      ))}
    </ol>
  );
}
