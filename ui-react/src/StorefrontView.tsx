import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BoardArt } from "./BoardArt";
import "./storefront.css";
import { optionLabel, type RecordDetail, type StorefrontApp } from "./dataApi";
import type { DataProductProps } from "./DataProductView";
import { Icon } from "./Icon";

type Product = {
  id: string;
  name: string;
  price: number;
  group: string;
  brand: string;
  subtitle: string;
  rank: number | null;
  available: boolean;
  shape: string;
  colour: string;
  length: number | null;
  details: [string, string][];
};
type Rule = { rec: string; together: number; confidence: number; lift: number };
type CartLine = { id: string; qty: number };

const EUR = new Intl.NumberFormat("en-IE", { style: "currency", currency: "EUR" });
const euros = (cents: number) => EUR.format(cents / 100);
const STAGES: [string, string][] = [
  ["produced", "Web shop"],
  ["event_log", "Kafka"],
  ["validated", "Contract"],
  ["bronze", "Bronze"],
  ["lake", "Lake"],
  ["gold", "Gold"],
];
const LABELS: Record<string, string> = { model: "Model", construction: "Construction", volume_l: "Volume (L)", rider_group: "For" };

function toProduct(row: Record<string, unknown>, app: StorefrontApp): Product {
  const f = app.fields;
  const pick = (col: string | null) => (col ? row[col] : undefined);
  return {
    id: String(row[f.id]),
    name: String(row[f.name] ?? row[f.id]),
    price: Number(row[f.price] ?? 0),
    group: String(row[f.group] ?? "Other"),
    brand: String(pick(f.brand) ?? ""),
    subtitle: String(pick(f.subtitle) ?? ""),
    rank: pick(f.rank) == null ? null : Number(pick(f.rank)),
    available: f.available ? Boolean(row[f.available]) : true,
    shape: String(pick(f.image_shape) ?? "board"),
    colour: String(pick(f.image_colour) ?? "#2bb3c0"),
    length: pick(f.image_length) == null ? null : Number(pick(f.image_length)),
    details: f.details.filter((d) => row[d] != null && row[d] !== "").map((d) => [LABELS[d] ?? d, String(row[d])]),
  };
}

/**
 * A fictional web shop on top of a data product (`data_product.apps.storefront`): the catalogue
 * is a governed gold dataset, "Riders also bought" are association rules computed by dbt from
 * every basket, and checkout runs a hidden simulator action — so the order is a real web order
 * through the contract, Kafka, bronze, dbt and gold, tracked here stage by stage. No payment,
 * nothing for sale: the disclaimer says so on every screen.
 */
export function StorefrontView({ scenario, client, overview, navigate, openChat }: DataProductProps) {
  const dp = scenario.data_product!;
  const app = dp.apps!.storefront!;
  const [products, setProducts] = useState<Product[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [aisle, setAisle] = useState<string>("Best sellers");
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState<Product | null>(null);
  const [cart, setCart] = useState<CartLine[]>([]);
  const rules = useRef(new Map<string, Rule[]>());
  const [rulesTick, setRulesTick] = useState(0);
  const [order, setOrder] = useState<{ id: string; total: number } | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    client
      .datasetRows(app.catalogue, 500, controller.signal)
      .then((r) => setProducts(r.rows.map((row) => toProduct(row, app)).filter((p) => p.available)))
      .catch((e: Error) => e.name !== "AbortError" && setError(e.message));
    return () => controller.abort();
  }, [client, app]);

  const byId = useMemo(() => new Map((products ?? []).map((p) => [p.id, p])), [products]);
  const aisles = useMemo(() => {
    const groups = new Map<string, number>();
    for (const p of products ?? []) groups.set(p.group, (groups.get(p.group) ?? 0) + 1);
    return [...groups.entries()];
  }, [products]);

  const loadRules = useCallback(
    (id: string) => {
      if (!app.recommendations || rules.current.has(id)) return;
      rules.current.set(id, []);
      client
        .datasetRows(app.recommendations, 8, undefined, { [app.fields.id]: id })
        .then((r) => {
          rules.current.set(
            id,
            r.rows.map((row) => ({
              rec: String(row[app.rec_id]),
              together: Number(row[app.rec_evidence[0]] ?? 0),
              confidence: Number(row[app.rec_evidence[1]] ?? 0),
              lift: Number(row[app.rec_evidence[2]] ?? 0),
            })),
          );
          setRulesTick((t) => t + 1);
        })
        .catch(() => undefined);
    },
    [client, app],
  );

  useEffect(() => {
    if (open) loadRules(open.id);
  }, [open, loadRules]);
  useEffect(() => {
    for (const line of cart) loadRules(line.id);
  }, [cart, loadRules]);

  const shown = useMemo(() => {
    const all = products ?? [];
    const q = search.trim().toLowerCase();
    if (q) return all.filter((p) => `${p.name} ${p.group} ${p.brand}`.toLowerCase().includes(q));
    if (aisle === "Best sellers") return all.filter((p) => p.rank !== null && p.rank <= 2).sort((a, b) => b.price - a.price);
    return all.filter((p) => p.group === aisle).sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99));
  }, [products, aisle, search]);

  // "Complete your setup": the cart's rules added up, without what is already in it.
  const suggestions = useMemo(() => {
    void rulesTick;
    const inCart = new Set(cart.map((l) => l.id));
    const score = new Map<string, { score: number; because: string; rule: Rule }>();
    for (const line of cart) {
      for (const rule of rules.current.get(line.id) ?? []) {
        if (inCart.has(rule.rec) || !byId.has(rule.rec)) continue;
        const s = rule.confidence * rule.lift;
        const prev = score.get(rule.rec);
        if (!prev || s > prev.score) score.set(rule.rec, { score: (prev?.score ?? 0) + s, because: line.id, rule });
        else prev.score += s;
      }
    }
    return [...score.entries()].sort((a, b) => b[1].score - a[1].score).slice(0, 4);
  }, [cart, byId, rulesTick]);

  const add = (id: string) => setCart((c) => (c.some((l) => l.id === id) ? c.map((l) => (l.id === id ? { ...l, qty: Math.min(10, l.qty + 1) } : l)) : [...c, { id, qty: 1 }]));
  const setQty = (id: string, qty: number) => setCart((c) => (qty <= 0 ? c.filter((l) => l.id !== id) : c.map((l) => (l.id === id ? { ...l, qty: Math.min(10, qty) } : l))));
  const total = cart.reduce((s, l) => s + (byId.get(l.id)?.price ?? 0) * l.qty, 0);

  return (
    <div className="tab-panel shop">
      <div className="shop-hero">
        <svg className="shop-hero-waves" viewBox="0 0 1200 160" preserveAspectRatio="none" aria-hidden="true">
          <path d="M0 110 C 150 70, 300 150, 450 110 S 750 70, 900 110 S 1100 150, 1200 110 L 1200 160 L 0 160 Z" />
          <path d="M0 130 C 200 100, 350 165, 600 130 S 1000 100, 1200 135 L 1200 160 L 0 160 Z" />
        </svg>
        <div className="shop-hero-text">
          <h2>{app.title}</h2>
          <p>{app.tagline}</p>
        </div>
        <div className="shop-search">
          <input placeholder="Search boards, fins, kites…" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Search the catalogue" />
        </div>
      </div>
      <p className="shop-disclaimer">
        <Icon name="info" size={14} /> {app.disclaimer}
      </p>
      {error && <p className="panel-hint dp-restricted">{error}</p>}
      <div className="shop-body">
        <nav className="shop-aisles" aria-label="Aisles">
          {["Best sellers", ...aisles.map(([g]) => g)].map((g) => (
            <button key={g} className={aisle === g && !search ? "active" : ""} onClick={() => { setAisle(g); setSearch(""); }}>
              {g}
              {g !== "Best sellers" && <span>{aisles.find(([a]) => a === g)?.[1]}</span>}
            </button>
          ))}
        </nav>
        <div className="shop-grid">
          {!products && !error && <div style={{ height: 300 }} aria-busy="true" />}
          {shown.map((p) => (
            <article key={p.id} className="shop-card" onClick={() => setOpen(p)}>
              {p.rank !== null && p.rank <= 2 && <span className="shop-badge">Best seller</span>}
              <div className="shop-card-art">
                <BoardArt shape={p.shape} colour={p.colour} lengthIn={p.length} brand={p.brand} title={p.name} />
              </div>
              <div className="shop-card-info">
                <span className="shop-brand">{p.brand}</span>
                <b>{p.name}</b>
                <span className="shop-price">{euros(p.price)}</span>
              </div>
              <button
                className="shop-add"
                onClick={(e) => {
                  e.stopPropagation();
                  add(p.id);
                }}
              >
                <Icon name="plus" size={14} /> Add
              </button>
            </article>
          ))}
          {products && shown.length === 0 && <p className="panel-hint">Nothing matches “{search}”.</p>}
        </div>
        <Cart
          app={app}
          cart={cart}
          byId={byId}
          total={total}
          suggestions={suggestions}
          setQty={setQty}
          add={add}
          client={client}
          overview={overview}
          scenario={scenario}
          onOrder={(o) => {
            setOrder(o);
            setCart([]);
          }}
        />
      </div>
      {open && (
        <ProductSheet
          product={open}
          rules={rules.current.get(open.id) ?? null}
          byId={byId}
          onClose={() => setOpen(null)}
          add={add}
          openOther={setOpen}
          tick={rulesTick}
        />
      )}
      {order && <OrderTracker app={app} client={client} order={order} overview={overview} onClose={() => setOrder(null)} navigate={navigate} openChat={openChat} />}
    </div>
  );
}

function ProductSheet({
  product,
  rules,
  byId,
  onClose,
  add,
  openOther,
}: {
  product: Product;
  rules: Rule[] | null;
  byId: Map<string, Product>;
  onClose: () => void;
  add: (id: string) => void;
  openOther: (p: Product) => void;
  tick: number;
}) {
  const recs = (rules ?? []).filter((r) => byId.has(r.rec)).slice(0, 4);
  return (
    <div className="shop-sheet-backdrop" onClick={onClose}>
      <div className="shop-sheet" role="dialog" aria-label={product.name} onClick={(e) => e.stopPropagation()}>
        <button className="shop-sheet-close" onClick={onClose} aria-label="Close">
          <Icon name="close" />
        </button>
        <div className="shop-sheet-art">
          <BoardArt shape={product.shape} colour={product.colour} lengthIn={product.length} brand={product.brand} title={product.name} />
        </div>
        <div className="shop-sheet-info">
          <span className="shop-brand">
            {product.brand} · {product.group}
          </span>
          <h3>{product.name}</h3>
          <span className="shop-price shop-price--big">{euros(product.price)}</span>
          <dl className="shop-details">
            {product.subtitle && (
              <>
                <dt>Size</dt>
                <dd>{product.subtitle}</dd>
              </>
            )}
            {product.details.map(([k, v]) => (
              <span key={k} className="shop-detail">
                <dt>{k}</dt>
                <dd>{v}</dd>
              </span>
            ))}
            <dt>SKU</dt>
            <dd>
              <code>{product.id}</code>
            </dd>
          </dl>
          <button className="shop-cta" onClick={() => add(product.id)}>
            <Icon name="cart" size={16} /> Add to cart
          </button>
          <h4 className="shop-recs-title">Riders also bought</h4>
          {rules === null && <p className="panel-hint">Reading the baskets…</p>}
          {rules !== null && recs.length === 0 && <p className="panel-hint">Not enough baskets with this product yet.</p>}
          <div className="shop-recs">
            {recs.map((r) => {
              const p = byId.get(r.rec)!;
              return (
                <div key={r.rec} className="shop-rec" onClick={() => openOther(p)}>
                  <div className="shop-rec-art">
                    <BoardArt shape={p.shape} colour={p.colour} lengthIn={p.length} brand={p.brand} />
                  </div>
                  <div>
                    <b>{p.name}</b>
                    <span className="shop-price">{euros(p.price)}</span>
                    <span className="shop-why" title="Association rule over the last year of baskets">
                      in {r.together.toLocaleString()} baskets together · {(r.confidence * 100).toFixed(0)}% of buyers · {r.lift.toFixed(1)}× more likely
                    </span>
                  </div>
                  <button
                    className="shop-add"
                    onClick={(e) => {
                      e.stopPropagation();
                      add(p.id);
                    }}
                    aria-label={`Add ${p.name}`}
                  >
                    <Icon name="plus" size={14} />
                  </button>
                </div>
              );
            })}
          </div>
          <p className="panel-hint">
            Recommendations are association rules computed by dbt from every receipt and online order (gold <code>mart_product_affinity</code>), served by data-api.
          </p>
        </div>
      </div>
    </div>
  );
}

function Cart({
  app,
  cart,
  byId,
  total,
  suggestions,
  setQty,
  add,
  client,
  overview,
  scenario,
  onOrder,
}: {
  app: StorefrontApp;
  cart: CartLine[];
  byId: Map<string, Product>;
  total: number;
  suggestions: [string, { score: number; because: string; rule: Rule }][];
  setQty: (id: string, qty: number) => void;
  add: (id: string) => void;
  client: DataProductProps["client"];
  overview: DataProductProps["overview"];
  scenario: DataProductProps["scenario"];
  onOrder: (o: { id: string; total: number }) => void;
}) {
  const action = scenario.data_product!.simulator?.actions.find((a) => a.name === app.checkout_action);
  const stores = action?.params.find((p) => p.name === "store_id")?.options ?? [];
  const [fulfilment, setFulfilment] = useState("home_delivery");
  const [store, setStore] = useState(stores[0] ?? "");
  const [loyalty, setLoyalty] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canOrder = Boolean(overview?.access.can_simulate);

  const checkout = () => {
    setBusy(true);
    setError(null);
    client
      .simulate(app.checkout_action, {
        cart: cart.map((l) => ({ id: l.id, qty: l.qty })),
        fulfilment,
        store_id: fulfilment === "click_collect" ? store : null,
        loyalty_id: loyalty.trim() || null,
      })
      .then((r) => onOrder({ id: String(r.record_id), total: Number(r.total_cents ?? total) }))
      .catch((e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  };

  return (
    <aside className="shop-cart" aria-label="Cart">
      <h4>
        <Icon name="cart" /> Your cart
      </h4>
      {cart.length === 0 && <p className="panel-hint">Empty — pick a board, then let the shop suggest the rest.</p>}
      {cart.map((l) => {
        const p = byId.get(l.id);
        if (!p) return null;
        return (
          <div key={l.id} className="shop-cart-line">
            <div className="shop-cart-art">
              <BoardArt shape={p.shape} colour={p.colour} lengthIn={p.length} />
            </div>
            <div className="shop-cart-name">
              <b>{p.name}</b>
              <span className="panel-hint">{euros(p.price)}</span>
            </div>
            <div className="shop-qty">
              <button onClick={() => setQty(l.id, l.qty - 1)} aria-label="One less">
                −
              </button>
              <span>{l.qty}</span>
              <button onClick={() => setQty(l.id, l.qty + 1)} aria-label="One more">
                +
              </button>
            </div>
          </div>
        );
      })}
      {suggestions.length > 0 && (
        <div className="shop-suggest">
          <h5>Complete your setup</h5>
          {suggestions.map(([id, s]) => {
            const p = byId.get(id)!;
            return (
              <button key={id} className="shop-suggest-item" onClick={() => add(id)} title={`${(s.rule.confidence * 100).toFixed(0)}% of buyers of ${byId.get(s.because)?.name} also took it (${s.rule.lift.toFixed(1)}× lift)`}>
                <span className="shop-cart-art">
                  <BoardArt shape={p.shape} colour={p.colour} lengthIn={p.length} />
                </span>
                <span>
                  <b>{p.name}</b>
                  <span className="shop-why">with your {byId.get(s.because)?.name}</span>
                </span>
                <span className="shop-price">+ {euros(p.price)}</span>
              </button>
            );
          })}
        </div>
      )}
      {cart.length > 0 && (
        <div className="shop-checkout">
          <div className="shop-total">
            <span>Total</span>
            <b>{euros(total)}</b>
          </div>
          <div className="dp-segmented">
            <button className={fulfilment === "home_delivery" ? "active" : ""} onClick={() => setFulfilment("home_delivery")}>
              Home delivery
            </button>
            <button className={fulfilment === "click_collect" ? "active" : ""} onClick={() => setFulfilment("click_collect")}>
              Click &amp; collect
            </button>
          </div>
          {fulfilment === "click_collect" && (
            <select value={store} onChange={(e) => setStore(e.target.value)} aria-label="Collect at">
              {stores.map((s) => (
                <option key={s} value={s}>
                  {optionLabel(s, overview?.row_filter_options)}
                </option>
              ))}
            </select>
          )}
          <input value={loyalty} onChange={(e) => setLoyalty(e.target.value)} placeholder="Loyalty card (optional, e.g. LOY-00000042)" aria-label="Loyalty card" />
          <button className="shop-cta" disabled={busy || !canOrder} onClick={checkout} title={canOrder ? undefined : "Only the platform admin (not viewing as) may place demo orders"}>
            {busy ? "Placing…" : "Place demo order"}
          </button>
          <span className="panel-hint">No payment — a simulated order through the real pipeline.</span>
          {error && <p className="panel-hint dp-restricted">{error}</p>}
        </div>
      )}
    </aside>
  );
}

function OrderTracker({
  app,
  client,
  order,
  overview,
  onClose,
  navigate,
  openChat,
}: {
  app: StorefrontApp;
  client: DataProductProps["client"];
  order: { id: string; total: number };
  overview: DataProductProps["overview"];
  onClose: () => void;
  navigate?: DataProductProps["navigate"];
  openChat?: () => void;
}) {
  const [record, setRecord] = useState<RecordDetail | null>(null);
  const [liveBusy, setLiveBusy] = useState(false);
  useEffect(() => {
    let stop = false;
    const poll = () =>
      client
        .record(app.order_entity, order.id)
        .then((r) => !stop && setRecord(r))
        .catch(() => undefined);
    poll();
    const timer = window.setInterval(poll, 3000);
    return () => {
      stop = true;
      window.clearInterval(timer);
    };
  }, [client, app.order_entity, order.id]);

  const current = record?.current;
  const statuses = (record?.versions ?? []).map((v) => String(v.payload.status ?? v.summary.Status ?? ""));
  const trail = current?.trail ?? {};
  const live = Boolean(overview?.simulator?.live);
  return (
    <div className="shop-order">
      <div className="shop-order-head">
        <div>
          <span className="shop-brand">Order placed · {euros(order.total)}</span>
          <h3>
            <code>{order.id}</code>
          </h3>
        </div>
        <button className="shop-sheet-close" onClick={onClose} aria-label="Close">
          <Icon name="close" />
        </button>
      </div>
      <div className="shop-order-statuses">
        {statuses.length === 0 && <span className="panel-hint">Waiting for the order to land in bronze…</span>}
        {statuses.map((s, i) => (
          <span key={`${s}-${i}`} className="shop-status">
            {s.replaceAll("_", " ")}
          </span>
        ))}
      </div>
      <div className="shop-trail">
        {STAGES.map(([key, label]) => (
          <div key={key} className={`shop-trail-step${trail[key as keyof typeof trail] ? " is-done" : ""}`} title={trail[key as keyof typeof trail] ?? "not yet"}>
            <i />
            <span>{label}</span>
          </div>
        ))}
      </div>
      <p className="panel-hint">
        The same order the shop just sent, read back from the platform: contract-checked, stored once in bronze, archived to the lake and — after the next dbt run — counted in gold.
        {!live && " Status updates (picked, shipped…) are published while the live simulation runs."}
      </p>
      <div className="shop-order-actions">
        {!live && overview?.access.can_simulate && (
          <button
            disabled={liveBusy}
            onClick={() => {
              setLiveBusy(true);
              client.live(true, 15).finally(() => setLiveBusy(false));
            }}
          >
            <Icon name="pulse" size={14} /> Start live activity (15 min)
          </button>
        )}
        {navigate && (
          <button onClick={() => navigate("flow")}>
            <Icon name="wave" size={14} /> Watch it in Live flow
          </button>
        )}
        {openChat && (
          <button onClick={openChat}>
            <Icon name="chat" size={14} /> Ask the assistant about {order.id}
          </button>
        )}
      </div>
    </div>
  );
}
