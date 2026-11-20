"""
- Title:    Drought-watch toolset: the assistant's forecast, state, what-if, rulebook, data-quality and bulletin tools
- Author:   Angel Martinez-Tenor

A `tabular_ml` scenario with `analysis_toolset: drought_watch` gives its chat these tools, all reading the scenario's
own committed artifacts (the synthetic snapshot, `forecast.json`, `drought_outlook.json`, the rulebook and a
read-only SQLite file). The data is synthetic and identical for every tenant; what the tenant check guards is the chat
itself, which is only reached through the entitled scenario route.

`DroughtToolkit` holds the logic as plain functions returning dicts, so tests, the golden-prompt checks and the bulletin
route all use the same code the LangChain tools wrap. The model never names a path, a tenant or a table the toolkit has
not allowed. Every number a tool returns is read from an artifact or computed by `ai_circus_shared`; none is typed here.
"""

from __future__ import annotations

import json
import re
import tempfile
from collections import Counter
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from ai_circus_shared.decision_support import Package, evaluate, optimise
from ai_circus_shared.drought_bulletin import build as build_bulletin
from ai_circus_shared.readonly_sql import ReadOnlyDatabase, SqlRefusedError, unpack
from ai_circus_shared.rulebook import Paragraph, Rulebook, load, paragraphs
from ai_circus_shared.series_quality import DESCRIPTIONS
from ai_circus_shared.threshold_states import run_states
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

TABLES = {"systems", "reservoirs", "readings", "storage_daily", "dq_flags"}
MAX_HORIZON = 180
FULL = 100.0
RULEBOOK_TOP_K = 4
_WORD = re.compile(r"[\wáéíóúüñç]+", re.IGNORECASE)
_STOP = {
    "the", "a", "an", "of", "in", "to", "for", "and", "or", "is", "are", "what", "which", "does", "do", "apply",
    "el", "la", "los", "las", "de", "del", "en", "y", "o", "que", "qué", "cuál", "se", "un", "una", "por",
}  # fmt: skip


class ToolError(ValueError):
    """A request the toolkit cannot answer; the message is for the model to read and relay."""


class DroughtToolkit:
    """The logic behind the tools, over one scenario directory."""

    def __init__(self, scenario_dir: Path, *, cache_dir: Path | None = None) -> None:
        """Load the scenario's artifacts and unpack its SQLite file once into `cache_dir` (default: a temp folder)."""
        self.dir = scenario_dir
        self.book: Rulebook = load(scenario_dir / "rulebook" / "rulebook.yaml")
        self.forecast = json.loads((scenario_dir / "forecast.json").read_text())
        outlook = json.loads((scenario_dir / "drought_outlook.json").read_text())
        self.outlooks: dict[str, dict[str, Any]] = {o["system"]: o for o in outlook["systems"]}
        self.backtest = json.loads((scenario_dir / "backtest.json").read_text())
        sources = yaml.safe_load((scenario_dir / "config" / "data_sources.yaml").read_text())
        self.attribution: list[str] = sources["attribution"]
        systems = yaml.safe_load((scenario_dir / "config" / "systems.yaml").read_text())
        meta = json.loads((scenario_dir / "sample_data" / "reservoirs.json").read_text())
        self.system_names: dict[str, str] = {s["id"]: s["name"] for s in systems["systems"]}
        self.reservoir_names: dict[str, str] = {rid: m["name"] for rid, m in meta.items()}
        self.members: dict[str, list[str]] = {s["id"]: list(s["reservoirs"]) for s in systems["systems"]}
        self._aliases = {
            **{k.lower(): k for k in {**self.system_names, **self.reservoir_names}},
            **{v.lower(): k for k, v in {**self.system_names, **self.reservoir_names}.items()},
        }
        cache = cache_dir or Path(tempfile.gettempdir()) / "ai-circus-drought-watch"
        self.db = ReadOnlyDatabase(unpack(scenario_dir / "sample_data" / "water.sqlite.gz", cache), TABLES)
        self.as_of = date.fromisoformat(next(iter(self.outlooks.values()))["as_of"])
        self._forecast_by_series = {s["series"]: s for s in self.forecast["series"]}

    # ---- helpers -------------------------------------------------------------------------------------------------
    def resolve(self, name: str, *, systems_only: bool = False) -> str:
        """An id from an id or a display name (any case). Raises ToolError listing what exists."""
        key = self._aliases.get(name.strip().lower())
        valid = self.system_names if systems_only else {**self.system_names, **self.reservoir_names}
        if key is None or key not in valid:
            raise ToolError(
                f"Unknown {'system' if systems_only else 'system or reservoir'} {name!r}. Known: {sorted(valid)}."
            )
        return key

    def _forecast_of(self, series_id: str) -> dict[int, dict[str, float]]:
        entry = self._forecast_by_series[series_id]
        return {int(h): {k: v[k] for k in ("p10", "p50", "p90")} for h, v in entry["horizons"].items()}

    def _typical_error(self, series_id: str, horizon: int) -> float | None:
        group = "systems" if series_id in self.system_names else "reservoirs"
        row = next((r for r in self.backtest[group] if r["horizon"] == horizon), None)
        return None if row is None else row["mae_model"]

    # ---- tools ---------------------------------------------------------------------------------------------------
    def forecast_of(self, series: str, horizon: int | None = None) -> dict[str, Any]:
        """Storage forecast (p10/p50/p90) for a system or a reservoir, with what drives the median."""
        sid = self.resolve(series)
        entry = self._forecast_by_series[sid]
        horizons = entry["horizons"]
        if horizon is not None:
            if str(horizon) not in horizons:
                raise ToolError(f"Forecast horizons are {sorted(int(h) for h in horizons)} days.")
            horizons = {str(horizon): horizons[str(horizon)]}
        return {
            "series": sid,
            "name": {**self.system_names, **self.reservoir_names}[sid],
            "kind": "system" if sid in self.system_names else "reservoir",
            "origin": entry["origin"],
            "current_pct": entry["current_pct"],
            "horizons_days": horizons,
            "typical_error_pp": {h: self._typical_error(sid, int(h)) for h in horizons},
            "note": "p10-p90 is the central 80 % interval. typical_error_pp is the backtest mean absolute error of the "
            "median for that kind of series. Illustrative decision support.",
        }

    def what_if(
        self, system: str, scenario: Literal["dry", "normal", "wet"], savings_pct: float, horizon: int
    ) -> dict[str, Any]:
        """Storage under a rainfall scenario and demand savings: trajectory bands, state timeline and days to each
        threshold below the current state.
        """
        sid = self.resolve(system, systems_only=True)
        if not 1 <= horizon <= MAX_HORIZON:
            raise ToolError(f"horizon must be between 1 and {MAX_HORIZON} days.")
        if not 0 <= savings_pct <= 100:
            raise ToolError("savings_pct must be between 0 and 100.")
        outlook = self.outlooks[sid]
        current = outlook["current_pct"]
        forecast = self._forecast_of(sid)
        package = Package(id="custom", label=f"{savings_pct:g} % savings", savings=savings_pct / 100)
        model = self.book.state_model()
        thresholds = [
            c["threshold_pct"] for c in outlook["crossings"]
        ]  # the thresholds below the current state, best first
        rows = []
        for c in outlook["crossings"]:
            out = evaluate(
                current, forecast, package=package, scenario=scenario, horizon=horizon,
                threshold=float(c["threshold_pct"]), ceiling=FULL,
            )  # fmt: skip
            rows.append({
                "state_below": c["state_below"],
                "threshold_pct": c["threshold_pct"],
                "days_pessimistic_p10": out.crossing.pessimistic,
                "days_median_p50": out.crossing.median,
                "days_optimistic_p90": out.crossing.optimistic,
                "probability_below_within_horizon": out.probability_below,
            })
        last = evaluate(
            current, forecast, package=package, scenario=scenario, horizon=horizon,
            threshold=float(thresholds[-1]) if thresholds else 0.0, ceiling=FULL,
        )  # fmt: skip
        return {
            "system": sid,
            "as_of": outlook["as_of"],
            "current_pct": current,
            "current_state": outlook["state"],
            "scenario": scenario,
            "savings_pct": savings_pct,
            "horizon_days": horizon,
            "end_of_horizon_pct": {k: round(v, 1) for k, v in last.final.items()},
            "end_of_horizon_state": {k: model.classify(v) for k, v in last.final.items()},
            "thresholds": rows,
            "method": "Rainfall scenario moves the forecast distribution by the p10 or p90 distance from the median "
            "(a conservative stress); savings reduce only the net drawdown. Illustrative decision support.",
        }

    def optimise_measures(
        self, system: str, target_probability_pct: float, horizon: int, scenario: Literal["dry", "normal", "wet"]
    ) -> dict[str, Any]:
        """The least restrictive rulebook package keeping P(storage < emergency threshold within horizon) at or below
        the target, with every package's probability.
        """
        sid = self.resolve(system, systems_only=True)
        if not 1 <= horizon <= MAX_HORIZON:
            raise ToolError(f"horizon must be between 1 and {MAX_HORIZON} days.")
        if not 0 < target_probability_pct < 100:
            raise ToolError("target_probability_pct must be between 0 and 100.")
        outlook = self.outlooks[sid]
        threshold = float(self.book.state_model().bands[-2].min_value)  # type: ignore[arg-type]
        rec = optimise(
            outlook["current_pct"], self._forecast_of(sid), self.book.packages(), scenario=scenario, horizon=horizon,
            threshold=threshold, target=target_probability_pct / 100, ceiling=FULL,
        )  # fmt: skip
        labels = {s.id: s.label["en"] for s in self.book.states}
        return {
            "system": sid,
            "as_of": outlook["as_of"],
            "current_pct": outlook["current_pct"],
            "scenario": scenario,
            "horizon_days": horizon,
            "emergency_threshold_pct": threshold,
            "target_probability_pct": target_probability_pct,
            "meets_target": rec.meets_target,
            "least_restrictive_package": rec.chosen,
            "packages": [
                {
                    "package": o.package,
                    "label": labels[o.package],
                    "savings_pct": round(o.savings * 100, 1),
                    "probability_below_pct": round(o.probability_below * 100, 1),
                }
                for o in rec.outcomes
            ],
            "note": rec.note + " Packages are the rulebook's states; savings are illustrative. A person decides.",
        }

    def drought_state(self, system: str, on: date | None = None, horizon: int | None = None) -> dict[str, Any]:
        """The state now, on a past date (the rulebook's state machine over the published readings), or forecast at a
        horizon (p10/p50/p90 classified, with no hysteresis).
        """
        sid = self.resolve(system, systems_only=True)
        o = self.outlooks[sid]
        if on is not None and horizon is not None:
            raise ToolError("Give a date or a horizon, not both.")
        if horizon is not None:
            states = o["forecast_states"].get(str(horizon))
            if states is None:
                raise ToolError(f"Forecast horizons are {sorted(int(h) for h in o['forecast_states'])} days.")
            return {
                "system": sid,
                "horizon_days": horizon,
                "forecast_state_by_quantile": states,
                "current_state": o["state"],
                "note": "Forecast states classify each quantile without hysteresis.",
            }
        if on is None:
            return {
                key: o[key]
                for key in (
                    "system",
                    "as_of",
                    "current_pct",
                    "state",
                    "raw_state",
                    "days_in_state",
                    "hysteresis_days",
                    "change_7d_pp",
                    "change_30d_pp",
                    "recent_transitions",
                )
            }
        if on > self.as_of:
            raise ToolError(f"The latest published reading is {self.as_of.isoformat()}.")
        rows = self._history(sid, on)
        days = [d for d, _ in rows]
        if not days:
            raise ToolError(f"No reading for {sid} on or before {on.isoformat()}.")
        states = run_states([v for _, v in rows], self.book.state_model(), self.book.hysteresis_days)
        return {
            "system": sid, "date": days[-1], "storage_pct": rows[-1][1], "state": states[-1],
            "raw_state": self.book.state_model().classify(rows[-1][1]),
            "note": "State from the rulebook's state machine over the published readings up to that date.",
        }  # fmt: skip

    def _history(self, sid: str, until: date) -> list[tuple[str, float]]:
        # The state machine needs the whole history up to the date; a direct read keeps it exact and bounded
        # (about 9,800 rows for one series), so it does not go through the row-capped SQL tool.
        import sqlite3

        con = sqlite3.connect(f"file:{self.db.path.as_posix()}?mode=ro", uri=True)
        try:
            return [
                (d, float(p))
                for d, p in con.execute(
                    "SELECT day, pct FROM storage_daily WHERE series = ? AND day <= ? ORDER BY day",
                    (sid, until.isoformat()),
                )
            ]
        finally:
            con.close()

    def search_rulebook(self, query: str, lang: str = "en", top_k: int = RULEBOOK_TOP_K) -> dict[str, Any]:
        """The rulebook's paragraphs that best match a question, each with its article citation."""
        if lang not in self.book.languages:
            raise ToolError(f"The rulebook is available in {self.book.languages}.")
        words = [w.lower() for w in _WORD.findall(query) if w.lower() not in _STOP]
        paras = paragraphs(self.book, lang)
        df = Counter(w for p in paras for w in set(_tokens(f"{p.title} {p.text}")))
        scored: list[tuple[float, Paragraph]] = []
        for p in paras:
            tokens = Counter(_tokens(f"{p.title} {p.text}"))
            score = sum((1 + tokens[w]) ** 0.5 * (1 + 1 / (1 + df[w])) for w in words if w in tokens)
            if score:
                scored.append((score, p))
        scored.sort(key=lambda sp: (-sp[0], sp[1].article, sp[1].number))
        return {
            "query": query,
            "language": lang,
            "results": [
                {"citation": p.citation, "article_title": p.title, "text": p.text, "state": p.state}
                for _, p in scored[:top_k]
            ],
            "note": "Fictional plan inspired by typical drought plans. Quote the citation with the text.",
        }

    def dq_report(self, reservoir: str, start: date | None = None, end: date | None = None) -> dict[str, Any]:
        """Readings that need review for a reservoir over a period (default: the last 30 days), and its quality
        score. A flag means "needs review", not "wrong".
        """
        rid = self.resolve(reservoir)
        if rid in self.system_names:
            raise ToolError("Give a reservoir; for a system, ask for each of its reservoirs: " + str(self.members[rid]))
        end = end or self.as_of
        start = start or end - timedelta(days=29)
        if start > end:
            raise ToolError("start must not be after end.")
        flags = self.db.query(
            "SELECT day, code, detail FROM dq_flags WHERE reservoir = ? AND day BETWEEN ? AND ? ORDER BY day, code",
            (rid, start.isoformat(), end.isoformat()),
        )
        present = self.db.query(
            "SELECT count(DISTINCT day) FROM readings WHERE reservoir = ? AND day BETWEEN ? AND ?",
            (rid, start.isoformat(), end.isoformat()),
        )["rows"][0][0]
        period_days = (end - start).days + 1
        flagged_days = self.db.query(
            "SELECT count(DISTINCT day) FROM dq_flags WHERE reservoir = ? AND day BETWEEN ? AND ?",
            (rid, start.isoformat(), end.isoformat()),
        )["rows"][0][0]
        missing = period_days - present
        counts = Counter(r[1] for r in flags["rows"])
        return {
            "reservoir": rid,
            "name": self.reservoir_names[rid],
            "period": [start.isoformat(), end.isoformat()],
            "days_in_period": period_days,
            "days_with_a_reading": present,
            "missing_days": missing,
            "days_flagged": flagged_days,
            "score_pct": round(100 * (1 - min(period_days, flagged_days + missing) / period_days), 1),
            "flags_by_code": dict(counts),
            "flags": [{"day": r[0], "code": r[1], "detail": r[2]} for r in flags["rows"]],
            "flags_truncated": flags["truncated"],
            # every code the quality check can raise, so a flag never arrives without its explanation
            "meaning": {code: text.removeprefix("needs review: ") for code, text in DESCRIPTIONS.items()},
            "note": "Each flag needs review by a person; none says the reading is wrong.",
        }

    def run_sql(self, sql: str) -> dict[str, Any]:
        """One read-only SELECT over the governed tables."""
        try:
            result = self.db.query(sql)
        except SqlRefusedError as exc:
            raise ToolError(str(exc)) from exc
        return {**result, "tables": self.db.tables()}

    def bulletin(self, week_of: date | None = None, lang: str = "en") -> dict[str, Any]:
        """The weekly bulletin for the week of the latest readings, as markdown."""
        monday = self.as_of - timedelta(days=self.as_of.weekday())
        if week_of is not None and week_of - timedelta(days=week_of.weekday()) != monday:
            raise ToolError(
                f"Only the current week ({monday.isoformat()}) can be produced: "
                f"the forecast is from {self.as_of.isoformat()}."
            )
        if lang not in self.book.languages:
            raise ToolError(f"The bulletin is available in {self.book.languages}.")
        order = list(self.system_names)
        notes = self._dq_notes(self.as_of - timedelta(days=6), self.as_of)
        text = build_bulletin(
            self.book,
            [self.outlooks[s] for s in order],
            {s: self._forecast_of(s) for s in order},
            lang=lang,
            week_of=monday,
            system_names=self.system_names,
            dq_notes=notes,
            attribution=self.attribution,
        )
        return {"week_of": monday.isoformat(), "language": lang, "markdown": text}

    def _dq_notes(self, start: date, end: date) -> dict[str, list[str]]:
        notes: dict[str, list[str]] = {}
        for system, members in self.members.items():
            for rid in members:
                report = self.dq_report(rid, start, end)
                parts = [f"{n} {code}" for code, n in report["flags_by_code"].items()]
                if report["missing_days"]:
                    parts.append(f"{report['missing_days']} missing")
                if parts:
                    notes.setdefault(system, []).append(
                        f"{self.reservoir_names[rid]}: " + ", ".join(parts) + " (needs review)"
                    )
        return notes


def _tokens(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text) if w.lower() not in _STOP]


@lru_cache(maxsize=4)
def get_toolkit(scenario_dir: str) -> DroughtToolkit:
    """One toolkit per scenario directory and process (the files are committed and do not change at run time)."""
    return DroughtToolkit(Path(scenario_dir))


# ---- LangChain tools ---------------------------------------------------------------------------------------------


class _ForecastArgs(BaseModel):
    series: str = Field(max_length=80, description="A supply system or a reservoir, by id or name.")
    horizon: int | None = Field(None, ge=1, le=MAX_HORIZON, description="Days ahead: 7, 30, 90 or 180. Omit for all.")


class _WhatIfArgs(BaseModel):
    system: str = Field(max_length=80, description="A supply system, by id or name.")
    scenario: Literal["dry", "normal", "wet"] = "normal"
    savings_pct: float = Field(0, ge=0, le=100, description="Demand-side savings as a % of the net drawdown.")
    horizon: int = Field(90, ge=1, le=MAX_HORIZON, description="Days ahead.")


class _OptimiseArgs(BaseModel):
    system: str = Field(max_length=80)
    target_probability_pct: float = Field(10, gt=0, lt=100, description="Largest acceptable probability of emergency.")
    horizon: int = Field(180, ge=1, le=MAX_HORIZON)
    scenario: Literal["dry", "normal", "wet"] = "dry"


class _StateArgs(BaseModel):
    system: str = Field(max_length=80)
    on: date | None = Field(None, description="A past date (YYYY-MM-DD). Omit for now.")
    horizon: int | None = Field(None, ge=1, le=MAX_HORIZON, description="Forecast state at this many days ahead.")


class _RulebookArgs(BaseModel):
    query: str = Field(min_length=2, max_length=300, description="What to look up in the drought plan.")
    lang: str = Field("en", max_length=5, description="Plan language: en or es.")


class _DqArgs(BaseModel):
    reservoir: str = Field(max_length=80)
    start: date | None = Field(None, description="First day (YYYY-MM-DD); default 29 days before end.")
    end: date | None = Field(None, description="Last day; default the latest published reading.")


class _SqlArgs(BaseModel):
    sql: str = Field(min_length=6, max_length=4000, description="One read-only SELECT over the governed tables.")


class _BulletinArgs(BaseModel):
    week: date | None = Field(None, description="Any date in the week; only the current week is available.")
    lang: str = Field("en", max_length=5, description="en or es.")


def _json(fn: Any) -> Any:
    def run(**kwargs: Any) -> str:
        try:
            return json.dumps(fn(**kwargs), default=str)
        except ToolError as exc:
            return json.dumps({"error": str(exc)})

    return run


def build_drought_tools(toolkit: DroughtToolkit, *, scenario_slug: str) -> list[BaseTool]:
    """The eight tools of the toolset. The scenario is closed over; the model cannot reach another one's files."""
    tables = ", ".join(f"{t}({', '.join(c)})" for t, c in toolkit.db.tables().items())

    def bulletin(week: date | None = None, lang: str = "en") -> dict[str, Any]:
        out = toolkit.bulletin(week, lang)
        out["pdf_path"] = f"/bulletin/{scenario_slug}?lang={lang}"
        return out

    specs: list[tuple[str, Any, type[BaseModel], str]] = [
        (
            "run_sql",
            lambda sql: toolkit.run_sql(sql),
            _SqlArgs,
            "Run ONE read-only SELECT over the governed data to answer any ad-hoc question and to feed render_chart / "
            f"render_table. Tables: {tables}. Dates are 'YYYY-MM-DD' text; volumes in hm3; percentages of capacity. "
            "readings are as published (with entries that need review); storage_daily is the cleaned series per "
            "reservoir and per system (series, kind, day, pct). Results are capped at 200 rows.",
        ),
        (
            "forecast",
            lambda series, horizon=None: toolkit.forecast_of(series, horizon),
            _ForecastArgs,
            "Storage forecast for a supply system or a reservoir: p10/p50/p90 at 7, 30, 90 and 180 days, what drives "
            "the median, and the typical backtest error.",
        ),
        (
            "what_if",
            lambda system, scenario="normal", savings_pct=0, horizon=90: toolkit.what_if(
                system, scenario, savings_pct, horizon
            ),
            _WhatIfArgs,
            "What if the coming days are dry/normal/wet and demand is cut by a percentage? Returns the end-of-horizon "
            "storage bands and state, and the days to each threshold below the current state.",
        ),
        (
            "optimise_measures",
            lambda system, target_probability_pct=10, horizon=180, scenario="dry": toolkit.optimise_measures(
                system, target_probability_pct, horizon, scenario
            ),
            _OptimiseArgs,
            "Find the least restrictive package of the plan's measures that keeps the probability of reaching the "
            "emergency threshold within the horizon at or below a target. Illustrative decision support.",
        ),
        (
            "drought_state",
            lambda system, on=None, horizon=None: toolkit.drought_state(system, on, horizon),
            _StateArgs,
            "The drought state of a supply system: now, on a past date, or forecast at a horizon.",
        ),
        (
            "search_rulebook",
            lambda query, lang="en": toolkit.search_rulebook(query, lang),
            _RulebookArgs,
            "Search the fictional drought plan. Returns paragraphs with article citations (Art. n.m); always quote "
            "the citation with the rule.",
        ),
        (
            "dq_report",
            lambda reservoir, start=None, end=None: toolkit.dq_report(reservoir, start, end),
            _DqArgs,
            "Data-quality report of a reservoir over a period: readings that need review (never 'wrong'), missing "
            "days and a score.",
        ),
        (
            "generate_bulletin",
            bulletin,
            _BulletinArgs,
            "Generate this week's drought bulletin as markdown (and its PDF path). Present it as returned.",
        ),
    ]
    return [
        StructuredTool.from_function(func=_json(fn), name=name, description=description, args_schema=schema)
        for name, fn, schema, description in specs
    ]


def drought_system_prompt(toolkit: DroughtToolkit) -> str:
    """The rules the assistant follows in this scenario, appended to the scenario's own prompt."""
    states = ", ".join(
        f"{s.id} (from {s.min_pct:g} %)" if s.min_pct is not None else f"{s.id} (below)" for s in toolkit.book.states
    )
    return (
        "\n\nYou are the drought-watch assistant of the fictional Riverbend Water Authority, a reference "
        "implementation on synthetic reservoir data from a simulated, unvalidated feed. You support planning and "
        "communication; you decide nothing and declare no drought state.\n"
        "RULES\n"
        "1. Never state a number you did not get from a tool in this conversation. If you need a figure, call the "
        "tool; if no tool gives it, say so.\n"
        "2. For any question about the plan's rules, thresholds or measures call search_rulebook and quote the "
        "article citation (for example 'Art. 8.1') next to the rule.\n"
        "3. For an ad-hoc table or chart, call run_sql first, then render_chart or render_table with exactly the "
        "returned values.\n"
        "4. For 'when' or 'what if' questions use drought_state, forecast, what_if; for 'which measures keep us out "
        "of emergency' use optimise_measures. Always give the uncertainty (p10-p90) and say it is illustrative.\n"
        "5. Data quality: say a reading 'needs review'; never call it wrong or an error. Use dq_report.\n"
        "6. The weekly bulletin comes from generate_bulletin; present it as returned and mention its PDF path.\n"
        "7. Answer in the user's language. The plan exists in English and Spanish: quote it from the tool, and "
        "translate yourself if the user writes another language, keeping the citation.\n"
        f"States, best to worst: {states}. Storage % is the capacity-weighted share of a supply system.\n"
    )
