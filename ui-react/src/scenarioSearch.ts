// Ranked scenario search: a phrase in your own words (English or Spanish), not exact keywords. Words are scored
// over each scenario's fields (title first, then its story, description, domain, techniques, questions), with
// prefixes ("forecast" finds "forecasting") and a small bilingual dictionary of concepts; platform-registry's
// semantic ranking (embeddings, GET /scenario-search/{org}) is blended in when it is available. Pure: no React.
import type { ScenarioSummary } from "./apiClient";

export type SearchFields = { slug: string; fields: [text: string, weight: number][] };
export type Ranked = { slug: string; score: number; matched: string[] };

// Case- and accent-insensitive ("prestacion" finds "prestación").
export function normalize(text: string): string {
  return text.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase();
}

const STOP = new Set(
  "a an and are as at be by can could de del do el en for from has have how i in is it la las los me my need needs of on or our para por que quiere quieren se should show the their this to un una want wants we what when which who will with would y".split(
    " ",
  ),
);

// Concepts a client may say in other words: each key also searches its values (normalized, prefixes allowed).
const CONCEPTS: Record<string, string[]> = {
  fraude: ["fraud"],
  fraud: ["suspicious", "mule", "launder"],
  blanqueo: ["launder", "aml", "money"],
  laundering: ["aml", "mule", "money trail"],
  mula: ["mule"],
  banco: ["bank"],
  bank: ["banking", "finance"],
  seguro: ["insurance", "policy"],
  seguros: ["insurance", "policy"],
  insurer: ["insurance"],
  abandono: ["churn", "lapse", "retention"],
  baja: ["churn", "lapse"],
  leave: ["churn", "lapse", "retention"],
  retener: ["retention", "churn"],
  retention: ["churn", "lapse"],
  cliente: ["customer"],
  clientes: ["customer"],
  agua: ["water", "reservoir"],
  embalse: ["reservoir"],
  embalses: ["reservoir"],
  sequia: ["drought"],
  lluvia: ["rain"],
  prevision: ["forecast"],
  predecir: ["predict", "forecast"],
  demanda: ["demand", "forecast"],
  energia: ["energy", "electricity"],
  electricidad: ["electricity", "energy"],
  luz: ["electricity"],
  gas: ["gas"],
  mantenimiento: ["maintenance", "failure"],
  averia: ["failure", "maintenance"],
  maquina: ["machine", "maintenance"],
  defecto: ["defect", "inspection"],
  calidad: ["quality", "defect", "inspection"],
  imagen: ["image", "vision", "inspection"],
  imagenes: ["image", "vision"],
  camara: ["camera", "vision"],
  incendio: ["fire", "wildfire", "smoke"],
  fuego: ["fire", "smoke"],
  salud: ["health", "patient", "medical"],
  hospital: ["health", "patient"],
  paciente: ["patient", "triage"],
  medico: ["medical", "health"],
  logistica: ["logistics", "shipping", "delivery"],
  envio: ["shipping", "delivery", "shipment"],
  entrega: ["delivery"],
  retraso: ["late", "delay"],
  tienda: ["retail", "shop", "store"],
  ventas: ["sales", "demand"],
  venta: ["sales"],
  recomendar: ["recommend", "next best offer"],
  oferta: ["offer"],
  documento: ["document", "rag"],
  documentos: ["document", "rag"],
  normativa: ["regulation", "rulebook"],
  regulation: ["rulebook", "law"],
  ley: ["regulation", "law"],
  formulario: ["form"],
  tramite: ["form", "public sector"],
  ayuda: ["aid", "benefit"],
  publico: ["public"],
  red: ["network", "graph"],
  grafo: ["graph", "network"],
  texto: ["text", "nlp"],
  datos: ["data"],
  plataforma: ["platform"],
  tutorial: ["tutorial"],
  aprender: ["tutorial", "learn"],
  chat: ["assistant", "rag"],
  preguntar: ["assistant", "rag"],
};

function tokens(text: string): string[] {
  return normalize(text)
    .split(/[^\p{L}\p{N}]+/u)
    .filter((w) => w.length > 1 && !STOP.has(w));
}

/** The query's words, each with what else it means (from the concept dictionary). */
function expand(query: string): { word: string; alternatives: string[] }[] {
  return tokens(query).map((word) => ({ word, alternatives: (CONCEPTS[word] ?? []).flatMap(tokens) }));
}

/** The fields a scenario is searched by, with their weights. */
export function searchFields(s: ScenarioSummary, extras: string[] = []): SearchFields {
  const story = s.story;
  return {
    slug: s.slug,
    fields: [
      [s.title, 4],
      [story ? `${story.headline} ${story.audience.join(" ")}` : "", 3],
      [story?.problem ?? "", 2],
      [s.description, 2],
      [`${s.industry.replaceAll("_", " ")} ${extras.join(" ")}`, 2],
      [s.slug.replaceAll("_", " "), 1],
      [(s.sample_questions ?? []).join(" "), 1],
      [Object.values(s.feature_schema ?? {}).map((f) => f.label ?? "").join(" "), 0.5],
    ],
  };
}

function hit(word: string, haystack: string[]): boolean {
  if (word.length < 4) return haystack.includes(word);
  const stem = word.slice(0, Math.max(4, Math.min(word.length - 1, 6)));
  return haystack.some((t) => t === word || t.startsWith(stem) || (t.length >= 5 && word.startsWith(t)));
}

/** Every scenario scored against the query by words, best first (those that match nothing are left out). */
export function rankByWords(query: string, docs: SearchFields[]): Ranked[] {
  const words = expand(query);
  if (words.length === 0) return [];
  const tokenized = docs.map((d) => ({ slug: d.slug, fields: d.fields.map(([text, weight]) => [tokens(text), weight] as const) }));
  // Rarer words count more: a word every scenario has ("model") says little.
  const df = new Map<string, number>();
  for (const w of words) {
    const all = [w.word, ...w.alternatives];
    df.set(w.word, tokenized.filter((d) => d.fields.some(([t]) => all.some((a) => hit(a, t)))).length);
  }
  const out: Ranked[] = [];
  for (const d of tokenized) {
    let score = 0;
    const matched: string[] = [];
    for (const w of words) {
      const idf = Math.log(1 + docs.length / (1 + (df.get(w.word) ?? 0)));
      let best = 0;
      for (const [t, weight] of d.fields) {
        if (hit(w.word, t)) best = Math.max(best, weight);
        else if (w.alternatives.some((a) => hit(a, t))) best = Math.max(best, weight * 0.8);
      }
      if (best > 0) {
        score += best * idf;
        matched.push(w.word);
      }
    }
    // Scenarios that match more of the query's words rise above those matching one word strongly.
    if (score > 0) out.push({ slug: d.slug, score: score * (0.5 + matched.length / words.length / 2), matched });
  }
  return out.sort((a, b) => b.score - a.score);
}

/**
 * Words and meaning together, each from 0 to 1, words weighted a little more. Meaning counts only above the average
 * scenario (an embedding finds every text a little similar, and a query in Spanish a little closer to the scenarios
 * written in Spanish); words count against the best match by words.
 */
export function blend(words: Ranked[], semantic: { slug: string; score: number }[] | null): Ranked[] {
  const topWords = words[0]?.score ?? 0;
  const byWords = new Map(words.map((r) => [r.slug, r]));
  if (!semantic || semantic.length === 0) return words.map((r) => ({ ...r, score: topWords ? r.score / topWords : 0 }));
  const max = semantic[0].score;
  const mean = semantic.reduce((sum, s) => sum + s.score, 0) / semantic.length;
  const span = max - mean || 1;
  const out = semantic.map((s) => {
    const w = byWords.get(s.slug);
    const meaning = Math.max(0, (s.score - mean) / span);
    const lexical = w && topWords ? w.score / topWords : 0;
    return { slug: s.slug, score: 0.4 * meaning + 0.6 * lexical, lexical, matched: w?.matched ?? [] };
  });
  // Ties go to the scenario that also matches the words.
  return out.sort((a, b) => b.score - a.score || b.lexical - a.lexical).map((r) => ({ slug: r.slug, score: r.score, matched: r.matched }));
}

/** The results worth showing: those close enough to the best one (a share of its score), at most `limit`. */
export function relevant(ranked: Ranked[], share = 0.5, limit = 12): Ranked[] {
  const top = ranked[0]?.score ?? 0;
  return ranked.filter((r) => r.score > 0 && r.score >= top * share).slice(0, limit);
}
