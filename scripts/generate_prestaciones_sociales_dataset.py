"""Generate the synthetic `prestaciones_sociales` corpus: 600 resolved applications for an
*Ayuda Económica de Emergencia Social* (AES) of the fictional Ayuntamiento de Villaclara.

No row-level public corpus of social-benefit applications exists (privacy: open data on
benefits is aggregate only), so every row is invented — no real person, household or
case is described. Three stages:

- `structure` (seeded, deterministic, no API): household facts, documents, the latent
  facets of each case (eviction, utility cut-off, undeclared income...) and the
  resolution. `Concedida` is drawn from a latent eligibility score with noise, and some
  facets are only partly visible in the text, so a model can learn the pattern but not
  memorise it (target: CV ROC AUC around 0.85-0.90, not 1.0). About 12% of rows fail a
  business rule (missing document, out-of-range value, requirement not met).
- `texts` (spends LLM tokens, run once): an LLM writes each row's three free-text boxes
  in natural Spanish *from the facets only* — never from the resolution — in batches,
  cached as resumable JSONL under ~/.cache/ai-circus/. A leak guard rejects any text
  that talks about the decision itself; a template fallback fills what is still missing.
- `build`: joins both into `scenarios/prestaciones_sociales/sample_data/
  prestaciones_sociales.csv` and prints an ablation (structured only / text only /
  both, LightGBM, 5-fold CV on the rows the rules let through).

Run from services/training (lightgbm + sklearn for `build`):
`cd services/training && uv run python ../../scripts/generate_prestaciones_sociales_dataset.py structure|texts|build|all`
`texts` needs the cluster's llm-gateway on localhost:4000
(`kubectl -n ai-circus port-forward svc/llm-gateway 4000:4000`) and its key in
`LLM_GATEWAY_API_KEY`.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENARIO_DIR = ROOT / "scenarios" / "prestaciones_sociales"
OUTPUT_CSV = SCENARIO_DIR / "sample_data" / "prestaciones_sociales.csv"
CACHE_DIR = Path.home() / ".cache" / "ai-circus" / "prestaciones_sociales"
STRUCTURE_JSON = CACHE_DIR / "structure.json"
TEXTS_JSONL = CACHE_DIR / "texts.jsonl"

N_ROWS = 600
SEED = 20261001
IPREM = 600.0  # €/month (2026)
MAX_IMPORTE = 1500  # the call's maximum per application

DISTRICTS = ["Centro", "Ensanche", "Ribera", "San Antón", "La Vega", "Las Eras"]
SITUACIONES = {
    # label: (weight, monthly household income range €)
    "Desempleo sin prestación": (0.24, (0, 450)),
    "Desempleo con prestación": (0.16, (480, 1100)),
    "Empleo precario o parcial": (0.22, (450, 1250)),
    "Empleo estable": (0.09, (950, 1700)),
    "Pensionista": (0.16, (560, 1150)),
    "Incapacidad o dependencia": (0.12, (450, 1200)),
}
REGIMENES = {
    # label: (weight, monthly housing cost range €)
    "Alquiler": (0.50, (350, 850)),
    "Hipoteca": (0.12, (300, 700)),
    "Habitación realquilada": (0.14, (180, 360)),
    "Cedida o familiar": (0.18, (0, 0)),
    "Sin hogar": (0.06, (0, 0)),
}
CONCEPTOS = {
    # label: (weight, typical amount range €)
    "Alquiler o hipoteca": (0.30, (300, 1400)),
    "Suministros básicos": (0.22, (80, 520)),
    "Alimentación e higiene": (0.22, (100, 450)),
    "Material escolar": (0.08, (60, 300)),
    "Gastos sanitarios": (0.10, (50, 900)),
    "Otros gastos": (0.08, (100, 1200)),
}
NOMBRES_M = ["Lucía", "María", "Carmen", "Ana", "Laura", "Marta", "Rosa", "Elena", "Fátima", "Nadia", "Paula",
             "Isabel", "Pilar", "Dolores", "Yolanda", "Aicha", "Andrea", "Raquel", "Silvia", "Gloria", "Ioana",
             "Daniela", "Sara", "Nerea", "Inés", "Julia", "Mercedes", "Teresa", "Valentina", "Khadija"]
NOMBRES_H = ["Antonio", "Manuel", "José", "Francisco", "David", "Javier", "Carlos", "Miguel", "Rafael", "Pedro",
             "Ahmed", "Luis", "Sergio", "Jorge", "Alberto", "Juan", "Mohamed", "Iván", "Rubén", "Óscar",
             "Mihai", "Andrés", "Ramón", "Diego", "Youssef", "Fernando", "Emilio", "Joaquín", "Wilson", "Tomás"]
APELLIDOS = ["García", "Fernández", "González", "Rodríguez", "López", "Martínez", "Sánchez", "Pérez", "Gómez",
             "Martín", "Jiménez", "Ruiz", "Hernández", "Díaz", "Moreno", "Muñoz", "Álvarez", "Romero", "Navarro",
             "Torres", "Domínguez", "Vázquez", "Ramos", "Gil", "Serrano", "Blanco", "Molina", "Castro", "Ortega",
             "Delgado", "Rubio", "Marín", "Sanz", "Iglesias", "Medina", "Garrido", "Cortés", "Castillo", "Lozano",
             "El Amrani", "Popescu", "Benali", "Quispe", "Mendoza", "Vargas", "Rojas", "Cabrera", "Fuentes"]

# Latent facets: (description given to the LLM, which box it shows up in, weight in the
# eligibility score). Positive = pushes towards a grant.
FACETS: dict[str, tuple[str, str, float]] = {
    "desahucio": ("tiene abierto un procedimiento de desahucio por impago de la vivienda", "exposicion", 1.1),
    "lanzamiento": ("ya hay fecha de lanzamiento fijada por el juzgado (usa literalmente la palabra 'lanzamiento')",
                    "informe", 0.6),
    "corte_suministros": ("ha recibido aviso de corte de luz, agua o gas por facturas impagadas", "exposicion", 0.9),
    "perdida_empleo": ("ha perdido el empleo hace pocos meses y no tiene otros ingresos estables", "exposicion", 0.6),
    "enfermedad": ("hay una enfermedad grave o crónica en la familia con gastos que la sanidad pública no cubre",
                   "exposicion", 0.7),
    "violencia": ("es víctima de violencia de género, con orden de protección (usa literalmente "
                  "'violencia de género')", "informe", 1.0),
    "monoparental": ("es una familia monoparental: cría sola a sus hijos sin apoyo del otro progenitor",
                     "exposicion", 0.5),
    "sin_red": ("no cuenta con red de apoyo familiar ni social", "informe", 0.4),
    "colabora": ("cumple el plan de intervención social y acude a todas las citas", "informe", 0.6),
    "no_colabora": ("no ha acudido a las citas del plan de intervención ni ha aportado lo que se le pidió",
                    "informe", -1.2),
    "ingresos_no_declarados": ("en la visita se constatan ingresos no declarados (trabajos sin contrato, alquiler "
                               "de una habitación o ayuda de familiares)", "informe", -1.7),
    "gasto_no_justificado": ("el presupuesto o factura aportado no se corresponde con la necesidad descrita",
                             "informe", -1.2),
    "mismo_concepto": ("ya recibió una ayuda municipal por el mismo concepto hace pocos meses", "informe", -0.8),
    "fin_no_basico": ("el dinero es para algo que no es una necesidad básica (un viaje, cambiar de coche, una "
                      "reforma, un móvil nuevo, una celebración)", "destino", -1.8),
}


@dataclass
class Expediente:
    Expediente: str
    Solicitante: str
    Sexo: str
    Nacionalidad: str
    EdadSolicitante: int
    MiembrosUnidad: int
    MenoresACargo: int
    SituacionLaboral: str
    RegimenVivienda: str
    ConceptoAyuda: str
    Discapacidad: str
    Distrito: str
    Canal: str
    IngresosMensualesUnidad: int
    RentaPerCapitaIPREM: float
    GastoViviendaMensual: int
    ImporteSolicitado: int
    MesesEmpadronamiento: int
    AyudasPrevias12m: int
    DeudaSuministros: int
    AportaDNI: str
    AportaPadron: str
    AportaJustificanteIngresos: str
    AportaJustificanteGasto: str
    DeclaracionResponsable: str
    Concedida: int
    facets: list[str] = field(default_factory=list)
    visible: list[str] = field(default_factory=list)  # facets the LLM is told about
    blank_exposicion: bool = False  # a "missing box" rule failure
    score: float = 0.0


def _pick(rng: random.Random, weighted: dict[str, tuple[float, object]]) -> str:
    keys = list(weighted)
    return rng.choices(keys, weights=[weighted[k][0] for k in keys])[0]


def build_structure(rng: random.Random) -> list[Expediente]:
    rows = []
    for i in range(N_ROWS):
        sexo = rng.choices(["Mujer", "Hombre"], weights=[0.62, 0.38])[0]
        nacionalidad = rng.choices(["Española", "UE", "Extracomunitaria"], weights=[0.64, 0.09, 0.27])[0]
        nombre = rng.choice(NOMBRES_M if sexo == "Mujer" else NOMBRES_H)
        solicitante = f"{nombre} {rng.choice(APELLIDOS)} {rng.choice(APELLIDOS)}"
        situacion = _pick(rng, SITUACIONES)
        edad = rng.randint(66, 88) if situacion == "Pensionista" else rng.randint(19, 64)
        miembros = rng.choices([1, 2, 3, 4, 5, 6, 7], weights=[0.24, 0.22, 0.2, 0.17, 0.1, 0.05, 0.02])[0]
        if situacion == "Pensionista":
            miembros = min(miembros, 2)
        menores = 0 if miembros == 1 or edad > 60 else rng.randint(0, min(4, miembros - 1))
        regimen = _pick(rng, REGIMENES)
        concepto = _pick(rng, CONCEPTOS)
        if regimen in {"Cedida o familiar", "Sin hogar"} and concepto == "Alquiler o hipoteca":
            concepto = rng.choice(["Alimentación e higiene", "Suministros básicos", "Gastos sanitarios"])
        if concepto == "Material escolar" and menores == 0:
            concepto = "Alimentación e higiene"
        low, high = SITUACIONES[situacion][1]
        ingresos = 0 if (situacion == "Desempleo sin prestación" and rng.random() < 0.45) else rng.randint(low, high)
        if miembros > 2 and rng.random() < 0.3:
            ingresos += rng.randint(200, 600)  # a second earner or a pension in the household
        if ingresos / miembros > 1.45 * IPREM:
            # Households over the income requirement rarely apply — the few that do are
            # injected as rule failures below; the rest sit just under the limit.
            ingresos = int(miembros * IPREM * rng.uniform(0.95, 1.45))
        gasto_low, gasto_high = REGIMENES[regimen][1]
        gasto = rng.randint(gasto_low, gasto_high) if gasto_high else 0
        importe_low, importe_high = CONCEPTOS[concepto][1]
        importe = int(round(rng.randint(importe_low, importe_high), -1))
        deuda = rng.randint(120, 1300) if concepto == "Suministros básicos" else (
            rng.randint(50, 400) if rng.random() < 0.15 else 0)
        meses = rng.choice([rng.randint(6, 36), rng.randint(36, 240), rng.randint(12, 120)])
        previas = rng.choices([0, 1, 2], weights=[0.62, 0.27, 0.11])[0]

        facets: list[str] = []
        if regimen in {"Alquiler", "Hipoteca", "Habitación realquilada"} and rng.random() < (
            0.35 if concepto == "Alquiler o hipoteca" else 0.08
        ):
            facets.append("desahucio")
            if rng.random() < 0.3:
                facets.append("lanzamiento")
        if concepto == "Suministros básicos" and rng.random() < 0.6:
            facets.append("corte_suministros")
        if situacion.startswith("Desempleo") and rng.random() < 0.45:
            facets.append("perdida_empleo")
        if concepto == "Gastos sanitarios" or (situacion == "Incapacidad o dependencia" and rng.random() < 0.4):
            facets.append("enfermedad")
        if sexo == "Mujer" and edad < 60 and rng.random() < 0.06:
            facets.append("violencia")
        if menores > 0 and miembros == menores + 1 and rng.random() < 0.85:
            facets.append("monoparental")
        if rng.random() < 0.3:
            facets.append("sin_red")
        cooperation = rng.random()
        if cooperation < 0.35:
            facets.append("colabora")
        elif cooperation > 0.9:
            facets.append("no_colabora")
        if ingresos < 900 and rng.random() < 0.09:
            facets.append("ingresos_no_declarados")
        if rng.random() < 0.08:
            facets.append("gasto_no_justificado")
        if previas >= 1 and rng.random() < 0.4:
            facets.append("mismo_concepto")
        if concepto == "Otros gastos" and rng.random() < 0.55:
            facets.append("fin_no_basico")
        discapacidad = rng.choices(["No", "Sí", "En trámite"], weights=[0.78, 0.15, 0.07])[0]
        if situacion == "Incapacidad o dependencia":
            discapacidad = rng.choice(["Sí", "En trámite"])

        rows.append(
            Expediente(
                Expediente=f"AES-2025-{i + 1:05d}",
                Solicitante=solicitante,
                Sexo=sexo,
                Nacionalidad=nacionalidad,
                EdadSolicitante=edad,
                MiembrosUnidad=miembros,
                MenoresACargo=menores,
                SituacionLaboral=situacion,
                RegimenVivienda=regimen,
                ConceptoAyuda=concepto,
                Discapacidad=discapacidad,
                Distrito=rng.choice(DISTRICTS),
                Canal=rng.choices(["Presencial", "Sede electrónica"], weights=[0.6, 0.4])[0],
                IngresosMensualesUnidad=ingresos,
                RentaPerCapitaIPREM=0.0,
                GastoViviendaMensual=gasto,
                ImporteSolicitado=importe,
                MesesEmpadronamiento=meses,
                AyudasPrevias12m=previas,
                DeudaSuministros=deuda,
                AportaDNI="Sí",
                AportaPadron="Sí",
                AportaJustificanteIngresos="Sí",
                AportaJustificanteGasto="Sí",
                DeclaracionResponsable="Sí",
                Concedida=0,
                facets=facets,
            )
        )
    _inject_rule_failures(rng, rows)
    for row in rows:
        row.RentaPerCapitaIPREM = round(row.IngresosMensualesUnidad / row.MiembrosUnidad / IPREM, 2)
        row.score = _score(row) + rng.gauss(0, 0.85)
        # A facet the caseworker did not write down, or the applicant did not mention.
        row.visible = [f for f in row.facets if rng.random() < 0.85]
    _label(rows)
    return rows


def _inject_rule_failures(rng: random.Random, rows: list[Expediente]) -> None:
    """About 12% of applications break a business rule — each kind a handful of times."""
    picked = rng.sample(range(len(rows)), 76)
    kinds = (
        ["dni"] * 9 + ["padron"] * 8 + ["ingresos_doc"] * 9 + ["gasto_doc"] * 9 + ["declaracion"] * 6
        + ["blank"] * 4 + ["importe_alto"] * 5 + ["menor_edad"] * 3 + ["incoherente"] * 4
        + ["empadronamiento"] * 9 + ["renta_alta"] * 5 + ["previas"] * 5
    )
    assert len(kinds) == len(picked)
    for index, kind in zip(picked, kinds, strict=True):
        row = rows[index]
        match kind:
            case "dni":
                row.AportaDNI = "No"
            case "padron":
                row.AportaPadron = "No"
            case "ingresos_doc":
                row.AportaJustificanteIngresos = "No"
            case "gasto_doc":
                row.AportaJustificanteGasto = "No"
            case "declaracion":
                row.DeclaracionResponsable = "No"
            case "blank":
                row.blank_exposicion = True
            case "importe_alto":
                row.ImporteSolicitado = rng.choice([1800, 2200, 2500, 3000])
            case "menor_edad":
                row.EdadSolicitante = 17
            case "incoherente":
                row.MenoresACargo = row.MiembrosUnidad
            case "empadronamiento":
                row.MesesEmpadronamiento = rng.randint(0, 5)
            case "renta_alta":
                row.IngresosMensualesUnidad = int(row.MiembrosUnidad * IPREM * rng.uniform(1.6, 2.3))
                row.SituacionLaboral = "Empleo estable"
            case "previas":
                row.AyudasPrevias12m = 3
    # Rent paid in a home that is lent or nonexistent — an incoherence the rules catch.
    for row in rows:
        if row.RegimenVivienda in {"Cedida o familiar", "Sin hogar"}:
            row.GastoViviendaMensual = 0


def _score(row: Expediente) -> float:
    """Latent eligibility: low income per head, housing burden, children and urgent
    situations push towards a grant; undeclared income, unjustified or non-basic
    expenses and not cooperating push against it."""
    renta = row.IngresosMensualesUnidad / row.MiembrosUnidad / IPREM
    z = 1.1 - 2.6 * (renta - 0.55)
    z += 0.3 * min(row.MenoresACargo, 3)
    if row.IngresosMensualesUnidad > 0 and row.GastoViviendaMensual / row.IngresosMensualesUnidad > 0.45:
        z += 0.5
    if row.RegimenVivienda == "Sin hogar":
        z += 0.9
    if row.Discapacidad == "Sí":
        z += 0.35
    if row.ImporteSolicitado > 1100:
        z -= 0.5
    z -= 0.35 * row.AyudasPrevias12m
    z += sum(FACETS[f][2] for f in row.facets)
    return z


def _label(rows: list[Expediente]) -> None:
    """Grant the top ~58% of latent scores."""
    ranked = sorted(row.score for row in rows)
    cut = ranked[int(len(ranked) * 0.42)]
    for row in rows:
        row.Concedida = int(row.score > cut)


# --- texts (LLM) --------------------------------------------------------------------

LEAK = re.compile(
    r"\b(conced\w*|deneg\w*|aprob\w*|desestim\w*|estim(a|ar|ada|ado)\b|favorable\w*|desfavorable\w*|procede\w*|"
    r"propuesta\w*|recomiend\w*|recomend\w*|resoluci\w+)",
    re.IGNORECASE,
)

PROMPT = """Eres un generador de datos SINTÉTICOS para formar a personal técnico de servicios sociales \
de un ayuntamiento español ficticio (Villaclara). Para cada expediente redacta las tres casillas de \
texto libre de una solicitud de Ayuda Económica de Emergencia Social, en castellano de España:

- "exposicion": la persona solicitante explica su situación, en primera persona, con sus palabras \
(2-5 frases, registro coloquial; a veces algo desordenado o con alguna falta leve). Varía mucho el estilo \
entre expedientes.
- "destino": para qué quiere el dinero, una frase corta escrita por la persona solicitante.
- "informe": observaciones de la trabajadora o trabajador social tras la entrevista, en tercera persona y \
registro técnico (2-4 frases). Describe hechos; NO valora si se debe dar la ayuda.

Reglas estrictas:
- Usa SOLO los hechos dados; menciona cada "hecho a reflejar" en la casilla indicada (puedes parafrasear, \
salvo donde se pide una palabra literal). No inventes hechos graves que no estén en la lista.
- PROHIBIDO hablar de la decisión: nunca uses "conceder", "denegar", "aprobar", "favorable", "procede", \
"propuesta", "recomiendo", "resolución" ni nada que anticipe el resultado.
- No escribas nombres propios de personas, DNI, direcciones ni teléfonos.
- No menciones el sexo, la nacionalidad, el origen ni la edad exacta de nadie (en el informe di "la \
persona solicitante"): son datos protegidos que el modelo no debe poder leer en el texto.
- Si "exposicion_en_blanco" es true, devuelve "exposicion" como cadena vacía.

Devuelve SOLO un objeto JSON: {{"items": [{{"id": "...", "exposicion": "...", "destino": "...", \
"informe": "..."}}, ...]}} con un elemento por expediente, en el mismo orden.

Expedientes:
{cases}
"""


def _case_brief(row: Expediente) -> dict[str, object]:
    return {
        "id": row.Expediente,
        "solicitante": f"{row.Sexo.lower()}, {row.EdadSolicitante} años, nacionalidad {row.Nacionalidad.lower()}",
        "unidad_convivencia": f"{row.MiembrosUnidad} miembros, {row.MenoresACargo} menores",
        "situacion_laboral": row.SituacionLaboral,
        "vivienda": row.RegimenVivienda,
        "ingresos_mensuales_hogar_eur": row.IngresosMensualesUnidad,
        "gasto_vivienda_mensual_eur": row.GastoViviendaMensual,
        "concepto_de_la_ayuda": row.ConceptoAyuda,
        "importe_solicitado_eur": row.ImporteSolicitado,
        "discapacidad": row.Discapacidad,
        "hechos_a_reflejar": [
            {"hecho": FACETS[f][0], "casilla": FACETS[f][1]} for f in row.visible
        ],
        "exposicion_en_blanco": row.blank_exposicion,
    }


def call_llm(prompt: str, model: str, attempt: int) -> dict[str, object]:
    import httpx  # noqa: PLC0415 — only the `texts` stage needs it

    base_url = os.environ.get("LLM_GATEWAY_URL", "http://localhost:4000")
    api_key = os.environ.get("LLM_GATEWAY_API_KEY")
    if not api_key:
        raise SystemExit("Set LLM_GATEWAY_API_KEY (llm-gateway's LITELLM_MASTER_KEY) to run `texts`.")
    response = httpx.post(
        f"{base_url.rstrip('/')}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": model,
            "temperature": 0.9 if attempt == 0 else 1.0,  # variety of voices is the point
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=240,
    )
    response.raise_for_status()
    return json.loads(response.json()["choices"][0]["message"]["content"])


def _clean(text: object, limit: int) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()[:limit]


def _valid(row: Expediente, item: dict[str, object]) -> bool:
    texts = [str(item.get(k) or "") for k in ("exposicion", "destino", "informe")]
    if any(LEAK.search(t) for t in texts):
        return False
    if not texts[1] or not texts[2] or (not texts[0]) != row.blank_exposicion:
        return False
    # The two literal phrases the protection rules look for must be there when asked for.
    informe = texts[2].lower()
    if "violencia" in row.visible and "violencia de género" not in informe:
        return False
    return "lanzamiento" not in row.visible or "lanzamiento" in informe


def load_texts() -> dict[str, dict[str, str]]:
    texts: dict[str, dict[str, str]] = {}
    if TEXTS_JSONL.exists():
        for line in TEXTS_JSONL.read_text().splitlines():
            item = json.loads(line)
            texts[item["id"]] = item
    return texts


def generate_texts(rows: list[Expediente], model: str, batch_size: int, limit: int | None = None) -> None:
    done = load_texts()
    pending = [row for row in rows if row.Expediente not in done][:limit]
    print(f"{len(done)} cached, {len(pending)} to write with {model}")
    with TEXTS_JSONL.open("a") as cache:
        for start in range(0, len(pending), batch_size):
            batch = pending[start : start + batch_size]
            for attempt in range(4):
                todo = [row for row in batch if row.Expediente not in done]
                if not todo:
                    break
                cases = json.dumps([_case_brief(row) for row in todo], ensure_ascii=False, indent=1)
                try:
                    reply = call_llm(PROMPT.format(cases=cases), model, attempt)
                except Exception as exc:  # noqa: BLE001 — network/JSON: retry the batch
                    print(f"  batch {start}: attempt {attempt} failed ({exc}); retrying")
                    time.sleep(3 * (attempt + 1))
                    continue
                by_id = {str(item.get("id")): item for item in reply.get("items", []) if isinstance(item, dict)}
                for row in todo:
                    item = by_id.get(row.Expediente)
                    if item is None or not _valid(row, item):
                        continue
                    record = {
                        "id": row.Expediente,
                        "exposicion": _clean(item.get("exposicion"), 1000),
                        "destino": _clean(item.get("destino"), 300),
                        "informe": _clean(item.get("informe"), 1000),
                    }
                    done[row.Expediente] = record
                    cache.write(json.dumps(record, ensure_ascii=False) + "\n")
                cache.flush()
            print(f"  {min(start + batch_size, len(pending))}/{len(pending)} — {len(done)} cached")


# --- template fallback --------------------------------------------------------------

FALLBACK = {
    "exposicion": {
        "desahucio": "Me han abierto un procedimiento de desahucio porque no he podido pagar.",
        "corte_suministros": "Me ha llegado el aviso de que nos cortan la luz por las facturas que debemos.",
        "perdida_empleo": "Perdí el trabajo hace unos meses y no entra dinero fijo en casa.",
        "enfermedad": "En casa hay una enfermedad crónica y los gastos de farmacia no los cubre nadie.",
        "monoparental": "Estoy sola con mis hijos y su padre no aporta nada.",
    },
    "informe": {
        "lanzamiento": "Consta fecha de lanzamiento fijada por el juzgado.",
        "violencia": "Se trata de una víctima de violencia de género con orden de protección en vigor.",
        "sin_red": "No dispone de red de apoyo familiar.",
        "colabora": "Cumple el plan de intervención y acude a las citas.",
        "no_colabora": "No ha acudido a las últimas citas del plan de intervención.",
        "ingresos_no_declarados": "En la visita se constatan ingresos no declarados.",
        "gasto_no_justificado": "El presupuesto aportado no se corresponde con la necesidad descrita.",
        "mismo_concepto": "Recibió una ayuda por el mismo concepto hace pocos meses.",
    },
    "destino": {"fin_no_basico": "Para cambiar el coche, que ya está muy viejo."},
}


def fallback_texts(row: Expediente) -> dict[str, str]:
    exposicion = " ".join(FALLBACK["exposicion"][f] for f in row.visible if f in FALLBACK["exposicion"])
    informe = " ".join(FALLBACK["informe"][f] for f in row.visible if f in FALLBACK["informe"])
    destino = next((FALLBACK["destino"][f] for f in row.visible if f in FALLBACK["destino"]), None)
    return {
        "exposicion": "" if row.blank_exposicion else (exposicion or "Pasamos por una situación económica difícil."),
        "destino": destino or f"Para {row.ConceptoAyuda.lower()}.",
        "informe": informe or f"Unidad de convivencia de {row.MiembrosUnidad} miembros; situación: "
        f"{row.SituacionLaboral.lower()}.",
    }


# --- build --------------------------------------------------------------------------

COLUMNS = [
    "Expediente", "Solicitante", "Sexo", "Nacionalidad",
    "EdadSolicitante", "MiembrosUnidad", "MenoresACargo", "SituacionLaboral", "RegimenVivienda",
    "ConceptoAyuda", "Discapacidad", "Distrito", "Canal", "IngresosMensualesUnidad", "RentaPerCapitaIPREM",
    "GastoViviendaMensual", "ImporteSolicitado", "MesesEmpadronamiento", "AyudasPrevias12m",
    "DeudaSuministros", "ExposicionMotivos", "DestinoAyuda", "InformeSocial",
    "AportaDNI", "AportaPadron", "AportaJustificanteIngresos", "AportaJustificanteGasto",
    "DeclaracionResponsable", "Concedida",
]
TEXT_COLUMNS = ["ExposicionMotivos", "DestinoAyuda", "InformeSocial"]


def build(rows: list[Expediente]) -> None:
    import pandas as pd  # noqa: PLC0415

    texts = load_texts()
    missing = [row.Expediente for row in rows if row.Expediente not in texts]
    if missing:
        print(f"WARNING: {len(missing)} rows use the template fallback (run `texts` again to retry them)")
    records = []
    for row in rows:
        text = texts.get(row.Expediente) or fallback_texts(row)
        record = asdict(row)
        record["ExposicionMotivos"] = text["exposicion"]
        record["DestinoAyuda"] = text["destino"]
        record["InformeSocial"] = text["informe"]
        records.append({column: record[column] for column in COLUMNS})
    df = pd.DataFrame(records)
    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(OUTPUT_CSV, index=False)
    print(f"Wrote {OUTPUT_CSV.relative_to(ROOT)}: {len(df)} rows, granted {df['Concedida'].mean():.0%}")
    leaks = sum(bool(LEAK.search(str(t))) for column in TEXT_COLUMNS for t in df[column])
    print(f"Leak guard: {leaks} texts mention the decision")
    ablation(df)


def ablation(df: object) -> None:
    """5-fold CV ROC AUC of LightGBM (small-data settings) on the rows the business
    rules let through — structured only, text only, both — using the scenario's own
    rules and the platform's own TF-IDF settings."""
    import numpy as np  # noqa: PLC0415
    import pandas as pd  # noqa: PLC0415
    from ai_circus_shared.business_rules import passes  # noqa: PLC0415
    from ai_circus_shared.scenario_schema import ScenarioDefinition  # noqa: PLC0415
    from ai_circus_shared.tabular_ml import SPANISH_STOP_WORDS, TEXT_VECTORIZER_PARAMS  # noqa: PLC0415
    from lightgbm import LGBMClassifier  # noqa: PLC0415
    from sklearn.compose import ColumnTransformer  # noqa: PLC0415
    from sklearn.feature_extraction.text import TfidfVectorizer  # noqa: PLC0415
    from sklearn.model_selection import StratifiedKFold, cross_val_score  # noqa: PLC0415
    from sklearn.pipeline import Pipeline  # noqa: PLC0415
    from sklearn.preprocessing import OneHotEncoder  # noqa: PLC0415

    assert isinstance(df, pd.DataFrame)
    scenario_yaml = SCENARIO_DIR / "scenario.yaml"
    if not scenario_yaml.exists():
        print("(no scenario.yaml yet — skipping the ablation)")
        return
    definition = ScenarioDefinition.load(scenario_yaml)
    assert definition.dataset is not None
    rules = definition.dataset.business_rules
    records = df.to_dict("records")
    kept = df[[passes(rules, r) for r in records]]
    print(f"Rules let {len(kept)}/{len(df)} rows through ({1 - len(kept) / len(df):.0%} stopped)")
    features = definition.dataset.feature_columns
    text = [c for c in features if c in TEXT_COLUMNS]
    categorical = [c for c in features if not pd.api.types.is_numeric_dtype(kept[c]) and c not in text]
    numeric = [c for c in features if c not in text and c not in categorical]
    y = kept["Concedida"]
    cv = StratifiedKFold(5, shuffle=True, random_state=0)
    model = {"n_estimators": 300, "learning_rate": 0.03, "num_leaves": 15, "min_child_samples": 20,
             "subsample": 0.8, "subsample_freq": 1, "colsample_bytree": 0.8, "reg_lambda": 1.0,
             "random_state": 0, "verbosity": -1}

    def run(name: str, use_structured: bool, use_text: bool) -> None:
        steps = []
        if use_structured:
            steps += [("num", "passthrough", numeric),
                      ("cat", OneHotEncoder(handle_unknown="ignore"), categorical)]
        if use_text:
            steps += [(f"text_{c}", TfidfVectorizer(stop_words=sorted(SPANISH_STOP_WORDS), **TEXT_VECTORIZER_PARAMS), c)
                      for c in text]
        pipeline = Pipeline([("pre", ColumnTransformer(steps)), ("model", LGBMClassifier(**model))])
        scores = cross_val_score(pipeline, kept, y, cv=cv, scoring="roc_auc")
        print(f"  {name:<22} ROC AUC {np.mean(scores):.3f} ± {np.std(scores):.3f}")

    print("Ablation (LightGBM small-data, 5-fold CV):")
    run("structured only", True, False)
    run("text only", False, True)
    run("structured + text", True, True)


# --- CLI ----------------------------------------------------------------------------


def structure() -> list[Expediente]:
    rows = build_structure(random.Random(SEED))
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    STRUCTURE_JSON.write_text(json.dumps([asdict(r) for r in rows], ensure_ascii=False))
    granted = sum(r.Concedida for r in rows) / len(rows)
    print(f"Structure: {len(rows)} rows, granted {granted:.0%} -> {STRUCTURE_JSON}")
    return rows


def load_structure() -> list[Expediente]:
    if not STRUCTURE_JSON.exists():
        return structure()
    return [Expediente(**r) for r in json.loads(STRUCTURE_JSON.read_text())]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", choices=["structure", "texts", "build", "all"])
    parser.add_argument("--model", default="gpt-4o-mini", help="an llm-gateway model_name")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--limit", type=int, default=None, help="write at most this many rows (a quick look)")
    args = parser.parse_args()
    if args.stage in {"structure", "all"}:
        rows = structure()
    else:
        rows = load_structure()
    if args.stage in {"texts", "all"}:
        generate_texts(rows, args.model, args.batch_size, args.limit)
    if args.stage in {"build", "all"}:
        build(rows)


if __name__ == "__main__":
    main()
