"""The weekly drought bulletin, as markdown, built only from computed outlooks and the rulebook.

Every number in the text is read from an outlook dict (`ai_circus_shared.drought_outlook.outlook`) or from the data-
quality notes passed in; nothing is typed here. Applicable measures are the rulebook's own paragraphs, each with its
article citation. English and Spanish are built in; the rulebook decides which of its languages are available.

The bulletin is decision support and communication material. It declares nothing: the closing line says so.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from ai_circus_shared.rulebook import Rulebook, paragraphs

T: dict[str, dict[str, str]] = {
    "en": {
        "title": "Weekly drought bulletin",
        "week": "Week of {day}",
        "banner": "Reference implementation on synthetic data. Not an operational tool.",
        "summary": "Situation by supply system",
        "head": "| System | Storage | State | Change (7 days) | Forecast 30 days (p10-p90) | "
        "Forecast 90 days (p10-p90) |",
        "next": "Next threshold",
        "next_line": "{system}: {threshold:g} % ({state}) is reached in {low:g}-{high:g} days (p50: {mid:g}).",
        "next_partial": "{system}: {threshold:g} % ({state}): median path in {mid:g} days; "
        "pessimistic path in {low:g} days.",
        "next_low_only": "{system}: {threshold:g} % ({state}): only the pessimistic path reaches it, in {low:g} days.",
        "next_none": "{system}: no threshold below the current state is reached within the forecast horizon "
        "on the median or pessimistic path.",
        "whatif": "What-if headline",
        "whatif_line": "{system}, dry scenario, {horizon} days: probability of storage below {thr:g} % is "
        "{prob:.0%} with no measures; least restrictive package that brings it to {target:.0%} or less: {pkg}.",
        "whatif_none": "{system}, dry scenario, {horizon} days: no package brings the probability below "
        "{thr:g} % to {target:.0%} or less (most restrictive leaves {prob:.0%}).",
        "dq": "Data-quality notes",
        "dq_none": "No readings need review this week.",
        "measures": "Applicable measures",
        "measures_none": "No system is above the pre-alert state: no restrictions apply.",
        "measures_for": "{system} ({state}):",
        "none_value": "n/a",
        "foot": "This bulletin is illustrative decision support. It declares nothing; a person decides. "
        "Forecasts carry the stated uncertainty.",
        "sources": "Sources",
    },
    "es": {
        "title": "Boletín semanal de sequía",
        "week": "Semana del {day}",
        "banner": "Implementación de referencia con datos sintéticos. No es una herramienta operativa.",
        "summary": "Situación por sistema de suministro",
        "head": "| Sistema | Almacenamiento | Estado | Variación (7 días) | Previsión 30 días (p10-p90) | "
        "Previsión 90 días (p10-p90) |",
        "next": "Próximo umbral",
        "next_line": "{system}: el {threshold:g} % ({state}) se alcanza en {low:g}-{high:g} días (p50: {mid:g}).",
        "next_partial": "{system}: {threshold:g} % ({state}): trayectoria mediana en {mid:g} días; "
        "trayectoria pesimista en {low:g} días.",
        "next_low_only": "{system}: {threshold:g} % ({state}): solo la trayectoria pesimista lo alcanza, "
        "en {low:g} días.",
        "next_none": "{system}: ningún umbral inferior al estado actual se alcanza dentro del horizonte de previsión "
        "en la trayectoria mediana ni en la pesimista.",
        "whatif": "Titular del escenario",
        "whatif_line": "{system}, escenario seco, {horizon} días: probabilidad de almacenamiento por debajo del "
        "{thr:g} % de {prob:.0%} sin medidas; paquete menos restrictivo que la reduce a {target:.0%} o menos: {pkg}.",
        "whatif_none": "{system}, escenario seco, {horizon} días: ningún paquete reduce a {target:.0%} o menos la "
        "probabilidad de bajar del {thr:g} % (el más restrictivo deja {prob:.0%}).",
        "dq": "Notas de calidad de datos",
        "dq_none": "Esta semana no hay lecturas pendientes de revisión.",
        "measures": "Medidas aplicables",
        "measures_none": "Ningún sistema supera el estado de prealerta: no se aplican restricciones.",
        "measures_for": "{system} ({state}):",
        "none_value": "n/d",
        "foot": "Este boletín es apoyo a la decisión ilustrativo. No declara nada; decide una persona. "
        "Las previsiones llevan la incertidumbre indicada.",
        "sources": "Fuentes",
    },
}


def _band(q: Mapping[str, float] | None) -> str:
    return "n/a" if not q else f"{q['p50']:.1f} % ({q['p10']:.1f}-{q['p90']:.1f})"


def _delta(value: float | None, none: str) -> str:
    return none if value is None else f"{value:+.1f} pp"


def build(
    book: Rulebook,
    outlooks: Sequence[Mapping[str, Any]],
    forecasts: Mapping[str, Mapping[int, Mapping[str, float]]],
    *,
    lang: str,
    week_of: date,
    system_names: Mapping[str, str],
    dq_notes: Mapping[str, Sequence[str]],
    attribution: Sequence[str],
) -> str:
    """The bulletin. `forecasts` maps system id -> horizon -> {p10, p50, p90} (the same the outlooks came from)."""
    if lang not in T or lang not in book.languages:
        raise ValueError(f"no bulletin in {lang!r}")
    t = T[lang]
    labels = {s.id: s.label[lang] for s in book.states}
    out = [f"# {t['title']}", "", f"**{t['week'].format(day=week_of.isoformat())}**", "", f"> {t['banner']}", ""]

    out += [f"## {t['summary']}", "", t["head"], "|---|---|---|---|---|---|"]
    for o in outlooks:
        f = forecasts[o["system"]]
        out.append(
            f"| {system_names.get(o['system'], o['system'])} | {o['current_pct']:.1f} % | {labels[o['state']]} "
            f"| {_delta(o.get('change_7d_pp'), t['none_value'])} | {_band(f.get(30))} | {_band(f.get(90))} |"
        )
    out.append("")

    out += [f"## {t['next']}", ""]
    for o in outlooks:
        name = system_names.get(o["system"], o["system"])
        c = o["crossings"][0] if o["crossings"] else None
        if c is None or (c["median"] is None and c["pessimistic"] is None):
            out.append(f"- {t['next_none'].format(system=name)}")
            continue
        args = {
            "system": name,
            "threshold": c["threshold_pct"],
            "state": labels[c["state_below"]],
            "low": c["pessimistic"],
            "mid": c["median"],
            "high": c["optimistic"],
        }
        if c["pessimistic"] is not None and c["median"] is not None and c["optimistic"] is not None:
            out.append(f"- {t['next_line'].format(**args)}")
        elif c["median"] is not None:
            out.append(f"- {t['next_partial'].format(**args)}")
        else:
            out.append(f"- {t['next_low_only'].format(**args)}")
    out.append("")

    out += [f"## {t['whatif']}", ""]
    for o in outlooks:
        name = system_names.get(o["system"], o["system"])
        opt = o["optimiser"]
        by_package = {x["package"]: x for x in opt["outcomes"]}
        none_prob = opt["outcomes"][0]["probability_below"]
        if opt["meets_target"]:
            pkg = labels[opt["chosen"]]
            out.append(
                "- "
                + t["whatif_line"].format(
                    system=name,
                    horizon=opt["horizon"],
                    thr=opt["emergency_threshold_pct"],
                    prob=none_prob,
                    target=opt["target"],
                    pkg=pkg,
                )
            )
        else:
            worst = by_package[opt["outcomes"][-1]["package"]]["probability_below"]
            out.append(
                "- "
                + t["whatif_none"].format(
                    system=name,
                    horizon=opt["horizon"],
                    thr=opt["emergency_threshold_pct"],
                    target=opt["target"],
                    prob=worst,
                )
            )
    out.append("")

    out += [f"## {t['dq']}", ""]
    notes = [(system_names.get(s, s), n) for s, ns in dq_notes.items() for n in ns]
    out += [f"- {s}: {n}" for s, n in notes] or [t["dq_none"]]
    out.append("")

    out += [f"## {t['measures']}", ""]
    worse = [o for o in outlooks if o["state"] not in (book.states[0].id,)]
    if not worse:
        out.append(t["measures_none"])
    by_state: dict[str, list[Any]] = {}
    for p in paragraphs(book, lang):
        if p.state:
            by_state.setdefault(p.state, []).append(p)
    for o in worse:
        name = system_names.get(o["system"], o["system"])
        out.append(f"**{t['measures_for'].format(system=name, state=labels[o['state']])}**")
        out.append("")
        out += [f"- {p.text} ({p.citation})" for p in by_state[o["state"]]]
        out.append("")

    out += ["---", "", t["foot"], "", f"**{t['sources']}:** " + " ".join(attribution), ""]
    return "\n".join(out)
