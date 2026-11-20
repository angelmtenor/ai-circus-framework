"""A threshold rulebook: one YAML is the source of truth; the markdown plan, the rules engine and the measure
packages are all generated from it, so the three cannot disagree.

The YAML declares an indicator, ordered states with their lower bounds, the measures of each state, the
hysteresis, and some free-text articles, each in every language the plan is published in. `paragraphs(lang)` yields
numbered paragraphs ("Art. 6.2") that the markdown renders and that a retrieval tool can index and cite.

The plan is *illustrative*: a fictional text inspired by the structure of typical drought plans, written for
a reference implementation. `markdown` always opens with that statement.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_circus_shared.decision_support import Package
from ai_circus_shared.threshold_states import Band, StateModel

Text = dict[str, str]  # language -> text


class Measures(BaseModel):
    """What a state asks of each sector. A cap of None means no cap."""

    model_config = ConfigDict(extra="forbid")

    agriculture_pct: float = Field(default=0, ge=0, le=100)
    industry_pct: float = Field(default=0, ge=0, le=100)
    urban_cap_lpd: float | None = Field(default=None, gt=0)
    actions: dict[str, list[str]] = Field(default_factory=dict)  # language -> free-text actions


class StateSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    label: Text
    min_pct: float | None = None
    measures: Measures = Field(default_factory=Measures)


class Article(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: Text
    paragraphs: list[Text]


class Demand(BaseModel):
    """Illustrative split of demand, used to turn a state's sector measures into one savings figure."""

    model_config = ConfigDict(extra="forbid")

    shares: dict[str, float]
    urban_baseline_lpd: float = Field(gt=0)

    @model_validator(mode="after")
    def _sums_to_one(self) -> Demand:
        if set(self.shares) != {"agriculture", "industry", "urban"} or abs(sum(self.shares.values()) - 1) > 1e-6:
            raise ValueError("demand.shares must name agriculture, industry and urban and sum to 1")
        return self


class Rulebook(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: Text
    languages: list[str]
    disclaimer: Text
    indicator: Text
    hysteresis_days: int = Field(ge=1)
    demand: Demand
    states: list[StateSpec] = Field(min_length=2)
    intro_articles: list[Article]
    closing_articles: list[Article]

    @model_validator(mode="after")
    def _complete(self) -> Rulebook:
        langs = set(self.languages)
        for spec in self.states:
            if set(spec.label) != langs:
                raise ValueError(f"state {spec.id}: label must be given in {sorted(langs)}")
            for lang in spec.measures.actions:
                if lang not in langs:
                    raise ValueError(f"state {spec.id}: unknown language {lang!r}")
        for article in [*self.intro_articles, *self.closing_articles]:
            for text in [article.title, *article.paragraphs]:
                if set(text) != langs:
                    raise ValueError(f"article {article.title}: every text needs {sorted(langs)}")
        if set(self.title) != langs or set(self.disclaimer) != langs or set(self.indicator) != langs:
            raise ValueError("title, disclaimer and indicator need every language")
        self.state_model()  # validates the bounds
        return self

    def state_model(self) -> StateModel:
        return StateModel(bands=[Band(id=s.id, min_value=s.min_pct) for s in self.states])

    def savings(self, spec: StateSpec) -> float:
        """The share of the net drawdown this state's measures are estimated to save (sector shares x cuts)."""
        m, d = spec.measures, self.demand
        urban = 0.0 if m.urban_cap_lpd is None else max(0.0, 1 - m.urban_cap_lpd / d.urban_baseline_lpd)
        total = (
            d.shares["agriculture"] * m.agriculture_pct / 100
            + d.shares["industry"] * m.industry_pct / 100
            + d.shares["urban"] * urban
        )
        return round(total, 4)

    def packages(self) -> list[Package]:
        """One measure package per state, least restrictive first (the states are listed best to worst)."""
        return [Package(id=s.id, label=s.label[self.languages[0]], savings=self.savings(s)) for s in self.states]


class Paragraph(BaseModel):
    """One citable unit of the plan."""

    article: int
    number: str  # "6.2"
    title: str
    text: str
    state: str | None = None  # the state a measures paragraph belongs to

    @property
    def citation(self) -> str:
        return f"Art. {self.number}"


# Small sentence templates for the generated articles (states and measures). Free text lives in the YAML.
PHRASES: dict[str, dict[str, str]] = {
    "en": {
        "states_title": "States of drought",
        "band_top": "{label}: {indicator} at or above {lo:g} %.",
        "band_mid": "{label}: {indicator} below {hi:g} % and at or above {lo:g} %.",
        "band_bottom": "{label}: {indicator} below {hi:g} %.",
        "changes_title": "Changes of state",
        "enter": "The system moves to a worse state on the same day its {indicator} falls below the threshold "
        "of that state.",
        "leave": "The system moves to a better state only after {n} consecutive days with its {indicator} in a better "
        "state, and then to the worst of those days' states. Each further improvement needs a new run of days.",
        "measures_title": "Measures in state {label}",
        "agriculture": "Agriculture: allocations reduced by {pct:g} %.",
        "industry": "Industry: consumption reduced by {pct:g} %.",
        "urban": "Urban supply: capped at {cap:g} litres per person per day.",
        "none": "No restrictions apply.",
    },
    "es": {
        "states_title": "Estados de sequía",
        "band_top": "{label}: {indicator} igual o superior al {lo:g} %.",
        "band_mid": "{label}: {indicator} inferior al {hi:g} % e igual o superior al {lo:g} %.",
        "band_bottom": "{label}: {indicator} inferior al {hi:g} %.",
        "changes_title": "Cambios de estado",
        "enter": "El sistema pasa a un estado peor el mismo día en que su {indicator} cae por debajo del umbral "
        "de ese estado.",
        "leave": "El sistema pasa a un estado mejor solo tras {n} días consecutivos con su {indicator} en un estado "
        "mejor, y entonces al peor de los estados de esos días. Cada mejora adicional requiere una nueva serie "
        "de días.",
        "measures_title": "Medidas en estado de {label}",
        "agriculture": "Agricultura: dotaciones reducidas un {pct:g} %.",
        "industry": "Industria: consumo reducido un {pct:g} %.",
        "urban": "Abastecimiento urbano: limitado a {cap:g} litros por persona y día.",
        "none": "No se aplican restricciones.",
    },
}


def load(path: Path) -> Rulebook:
    """Read and validate the rulebook YAML."""
    return Rulebook.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def paragraphs(book: Rulebook, lang: str) -> list[Paragraph]:
    """Every numbered paragraph of the plan in `lang`: intro articles, the generated states and changes of state,
    one measures article per state, then the closing articles. Numbers follow the order, so adding a state or an
    article renumbers the whole plan consistently."""
    if lang not in book.languages:
        raise ValueError(f"{lang!r} is not one of {book.languages}")
    ph = PHRASES[lang]
    indicator = book.indicator[lang]
    out: list[Paragraph] = []

    def add(title: str, texts: list[str], state: str | None = None) -> None:
        article = len(out_titles) + 1
        out_titles.append(title)
        out.extend(
            Paragraph(article=article, number=f"{article}.{i}", title=title, text=t, state=state)
            for i, t in enumerate(texts, 1)
        )

    out_titles: list[str] = []
    for art in book.intro_articles:
        add(art.title[lang], [p[lang] for p in art.paragraphs])

    bands: list[str] = []
    for i, s in enumerate(book.states):
        hi = book.states[i - 1].min_pct if i > 0 else None
        if s.min_pct is None:
            bands.append(ph["band_bottom"].format(label=s.label[lang], indicator=indicator, hi=hi))
        elif hi is None:
            bands.append(ph["band_top"].format(label=s.label[lang], indicator=indicator, lo=s.min_pct))
        else:
            bands.append(ph["band_mid"].format(label=s.label[lang], indicator=indicator, hi=hi, lo=s.min_pct))
    add(ph["states_title"], bands)
    add(
        ph["changes_title"],
        [ph["enter"].format(indicator=indicator), ph["leave"].format(n=book.hysteresis_days, indicator=indicator)],
    )
    for s in book.states:
        m = s.measures
        lines: list[str] = []
        if m.agriculture_pct:
            lines.append(ph["agriculture"].format(pct=m.agriculture_pct))
        if m.industry_pct:
            lines.append(ph["industry"].format(pct=m.industry_pct))
        if m.urban_cap_lpd:
            lines.append(ph["urban"].format(cap=m.urban_cap_lpd))
        lines.extend(m.actions.get(lang, []))
        add(ph["measures_title"].format(label=s.label[lang]), lines or [ph["none"]], state=s.id)
    for art in book.closing_articles:
        add(art.title[lang], [p[lang] for p in art.paragraphs])
    return out


def markdown(book: Rulebook, lang: str) -> str:
    """The plan as a markdown document, generated: edit the YAML, never this output."""
    out = [
        f"# {book.title[lang]}",
        "",
        f"> {book.disclaimer[lang]}",
        "",
        "<!-- Generated from rulebook.yaml. Do not edit. -->",
        "",
    ]
    current = 0
    for p in paragraphs(book, lang):
        if p.article != current:
            current = p.article
            out += [f"## Art. {p.article}. {p.title}", ""]
        out.append(f"**{p.citation}** {p.text}")
        out.append("")
    return "\n".join(out)


def expected_files(book: Rulebook, directory: Path) -> dict[Path, str]:
    """The generated files and their content, so a script can write them and a test can compare."""
    return {directory / f"plan_{lang}.md": markdown(book, lang) for lang in book.languages}


def as_dict(book: Rulebook) -> dict[str, Any]:
    """A plain summary for tools and the UI: states with bounds, savings and measures."""
    return {
        "id": book.id,
        "hysteresis_days": book.hysteresis_days,
        "states": [
            {
                "id": s.id,
                "min_pct": s.min_pct,
                "label": s.label,
                "savings": book.savings(s),
                "agriculture_pct": s.measures.agriculture_pct,
                "industry_pct": s.measures.industry_pct,
                "urban_cap_lpd": s.measures.urban_cap_lpd,
            }
            for s in book.states
        ],
    }
