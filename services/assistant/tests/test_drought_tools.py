"""
- Title:    Tests for the drought-watch toolset, its golden prompts and the bulletin PDF
- Author:   Angel Martinez-Tenor
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from itertools import pairwise
from pathlib import Path
from typing import Any

import pytest
import yaml
from ai_circus_shared.rulebook import paragraphs
from ai_circus_shared.traceability import untraceable

from assistant.core.bulletin_pdf import render
from assistant.core.drought_tools import DroughtToolkit, ToolError, build_drought_tools, drought_system_prompt

SCENARIO = Path(__file__).resolve().parents[3] / "scenarios" / "water_reservoirs_drought"
pytestmark = pytest.mark.skipif(not (SCENARIO / "forecast.json").exists(), reason="scenario artifacts not present")


@pytest.fixture(scope="module")
def toolkit(tmp_path_factory: pytest.TempPathFactory) -> DroughtToolkit:
    return DroughtToolkit(SCENARIO, cache_dir=tmp_path_factory.mktemp("cache"))


@pytest.fixture(scope="module")
def tools(toolkit: DroughtToolkit) -> dict[str, Any]:
    return {t.name: t for t in build_drought_tools(toolkit, scenario_slug="water_reservoirs_drought")}


def _call(tools: dict[str, Any], name: str, args: dict[str, Any]) -> dict[str, Any]:
    return json.loads(tools[name].invoke(args))


def test_the_eight_tools_exist_and_say_what_they_do(tools: dict[str, Any]) -> None:
    assert set(tools) == {
        "run_sql", "forecast", "what_if", "optimise_measures", "drought_state", "search_rulebook", "dq_report",
        "generate_bulletin",
    }  # fmt: skip
    assert "storage_daily" in tools["run_sql"].description and "200 rows" in tools["run_sql"].description


# ---- the golden prompts --------------------------------------------------------------------------------------------
GOLDEN = yaml.safe_load((SCENARIO / "golden_prompts.yaml").read_text()) if SCENARIO.exists() else []


@pytest.mark.parametrize("golden", GOLDEN, ids=[g["id"] for g in GOLDEN])
def test_golden_prompt_plan_runs_and_yields_what_an_answer_needs(golden: dict[str, Any], tools: dict[str, Any]) -> None:
    used = [step["tool"] for step in golden["plan"]]
    backend = [t for t in golden["tools"] if not t.startswith("render_")]
    assert set(backend) <= set(used), "the plan must use every backend tool the prompt expects"
    for step in golden["plan"]:
        result = _call(tools, step["tool"], step["args"])
        assert "error" not in result, f"{step['tool']} refused: {result.get('error')}"
        expect = step.get("expect", {})
        for key, value in expect.get("equals", {}).items():
            assert result[key] == value
        for key in expect.get("has_keys", []):
            assert key in result
        for text in expect.get("text_contains", []):
            assert text in json.dumps(result)


def test_an_answer_built_from_tool_output_has_traceable_figures_and_an_invented_one_does_not(
    tools: dict[str, Any],
) -> None:
    result = _call(tools, "drought_state", {"system": "central"})
    honest = f"Central holds {result['current_pct']} % and is in state {result['state']}."
    assert untraceable(honest, [result]) == []
    invented = honest + " It will reach 31.7 % by March."
    assert untraceable(invented, [result]) == ["31.7"]


# ---- individual tools ------------------------------------------------------------------------------------------------
def test_forecast_by_name_or_id_and_one_horizon(tools: dict[str, Any]) -> None:
    by_name = _call(tools, "forecast", {"series": "Central system", "horizon": 30})
    by_id = _call(tools, "forecast", {"series": "central", "horizon": 30})
    assert by_name == by_id and set(by_id["horizons_days"]) == {"30"}
    q = by_id["horizons_days"]["30"]
    assert q["p10"] <= q["p50"] <= q["p90"] and by_id["typical_error_pp"]["30"] > 0
    assert "error" in _call(tools, "forecast", {"series": "nowhere"})


def test_what_if_orders_scenarios_and_savings_never_hurt(tools: dict[str, Any]) -> None:
    def end(scenario: str, savings: float) -> float:
        out = _call(
            tools, "what_if", {"system": "westfield", "scenario": scenario, "savings_pct": savings, "horizon": 90}
        )
        return out["end_of_horizon_pct"]["p50"]

    assert end("dry", 0) <= end("normal", 0) <= end("wet", 0)
    assert end("dry", 0) <= end("dry", 30)


def test_optimise_measures_returns_every_package(tools: dict[str, Any]) -> None:
    out = _call(tools, "optimise_measures", {"system": "south_valley"})
    assert [p["package"] for p in out["packages"]] == ["normal", "pre_alert", "alert", "exceptional", "emergency"]
    probs = [p["probability_below_pct"] for p in out["packages"]]
    assert probs == sorted(probs, reverse=True)
    assert "A person decides" in out["note"]


def test_drought_state_now_on_a_date_and_ahead(toolkit: DroughtToolkit) -> None:
    now = toolkit.drought_state("westfield")
    assert now["state"] in {"normal", "pre_alert", "alert", "exceptional", "emergency"} and now["days_in_state"] >= 1
    past = toolkit.drought_state("central", on=date(2023, 6, 1))
    assert past["state"] in {"alert", "exceptional", "emergency", "pre_alert"}  # the 2023 drought
    ahead = toolkit.drought_state("westfield", horizon=90)
    assert set(ahead["forecast_state_by_quantile"]) == {"p10", "p50", "p90"}
    with pytest.raises(ToolError, match="not both"):
        toolkit.drought_state("westfield", on=date(2023, 1, 1), horizon=30)
    with pytest.raises(ToolError, match="latest published"):
        toolkit.drought_state("westfield", on=date(2099, 1, 1))


def test_search_rulebook_cites_articles_in_both_languages(toolkit: DroughtToolkit) -> None:
    en = toolkit.search_rulebook("industry reduction in emergency")
    assert any(r["citation"] == "Art. 9.2" for r in en["results"])
    es = toolkit.search_rulebook("agricultura excepcionalidad", lang="es")
    assert any(r["citation"] == "Art. 8.1" for r in es["results"])
    assert toolkit.search_rulebook("zzzz qqqq")["results"] == []
    with pytest.raises(ToolError):
        toolkit.search_rulebook("agriculture", lang="fr")


def test_dq_report_uses_neutral_language_and_accounts_for_every_day(toolkit: DroughtToolkit) -> None:
    out = toolkit.dq_report("Lantern Lake", date(2026, 10, 1), date(2026, 11, 20))
    assert out["days_in_period"] == 51 and out["days_with_a_reading"] + out["missing_days"] == 51
    assert out["flags_by_code"] and "needs review" in out["note"]
    assert set(out["flags_by_code"]) <= set(out["meaning"])  # every flag comes with its explanation
    text = json.dumps(out).lower()
    assert "wrong" not in text.replace("none says the reading is wrong", "") and "error" not in text
    with pytest.raises(ToolError, match="Give a reservoir"):
        toolkit.dq_report("westfield")


def test_sql_is_read_only_and_limited_to_the_governed_tables(tools: dict[str, Any]) -> None:
    assert _call(tools, "run_sql", {"sql": "SELECT count(*) AS n FROM reservoirs"})["rows"] == [[9]]
    for bad in (
        "DROP TABLE readings",
        "SELECT * FROM sqlite_master",
        "SELECT 1; SELECT 2",
        "PRAGMA table_info(readings)",
    ):
        assert "error" in _call(tools, "run_sql", {"sql": bad})


def test_the_bulletin_is_all_traceable_and_renders_to_pdf(toolkit: DroughtToolkit, tools: dict[str, Any]) -> None:
    out = toolkit.bulletin(None, "en")
    paras = paragraphs(toolkit.book, "en")
    sources = [
        toolkit.outlooks, toolkit.forecast, out["week_of"], toolkit.attribution,
        [p.text for p in paras], [p.citation for p in paras], toolkit.reservoir_names, toolkit.system_names,
        toolkit._dq_notes(date.fromisoformat(out["week_of"]), toolkit.as_of),
    ]  # fmt: skip
    assert untraceable(out["markdown"], sources) == []
    pdf = render(out["markdown"], title="Bulletin")
    assert pdf.startswith(b"%PDF") and len(pdf) > 2_000
    with pytest.raises(ToolError, match="current week"):
        toolkit.bulletin(date(2020, 1, 6), "en")
    assert "Boletín semanal" in toolkit.bulletin(None, "es")["markdown"]
    assert _call(tools, "generate_bulletin", {})["pdf_path"].startswith("/bulletin/water_reservoirs_drought")


def test_the_system_prompt_states_the_rules(toolkit: DroughtToolkit) -> None:
    prompt = drought_system_prompt(toolkit)
    for phrase in ("Never state a number", "search_rulebook", "needs review", "render_chart", "translate yourself"):
        assert phrase in prompt


# ---- the control-room tab's reads ----------------------------------------------------------------------------------


def test_the_control_room_holds_the_plan_every_system_and_every_reservoir(toolkit: DroughtToolkit) -> None:
    room = toolkit.control_room()
    assert [s["id"] for s in room["states"]] == ["normal", "pre_alert", "alert", "exceptional", "emergency"]
    assert room["states"][0]["savings_pct"] == 0 and room["states"][-1]["min_pct"] is None
    assert {s["id"] for s in room["systems"]} == set(toolkit.system_names)
    assert {r["id"] for r in room["reservoirs"]} == set(toolkit.reservoir_names)
    for reservoir in room["reservoirs"]:
        assert reservoir["system"] in toolkit.system_names and reservoir["flags_30d"] >= 0
    assert set(room["drivers"]["central"]) == {"7", "30", "90", "180"}


def test_the_history_is_weekly_ends_today_and_states_follow_the_plan(toolkit: DroughtToolkit) -> None:
    history = toolkit.history
    dates = history["dates"]
    assert dates[-1] == toolkit.as_of.isoformat()
    assert all((date.fromisoformat(b) - date.fromisoformat(a)).days == 7 for a, b in pairwise(dates))
    assert set(history["series"]) == set(toolkit.system_names) | set(toolkit.reservoir_names)
    assert set(history["states"]) == set(toolkit.system_names)
    for sid, states in history["states"].items():
        assert len(states) == len(dates) == len(history["series"][sid])
    # the synthetic basin's long drought reaches at least alert somewhere in 2022-2024
    in_drought = [
        s for i, d in enumerate(dates) if "2022" <= d < "2025" for s in (v[i] for v in history["states"].values())
    ]
    assert {"alert", "exceptional", "emergency"} & set(in_drought)


def test_the_quality_views_count_every_flag_and_stay_neutral(toolkit: DroughtToolkit) -> None:
    overview = toolkit.quality_overview()
    snapshot = json.loads((SCENARIO / "sample_data" / "SNAPSHOT.json").read_text())
    by_code = Counter()
    for reservoir in overview["reservoirs"]:
        by_code.update(reservoir["flags_by_code"])
        assert sum(reservoir["flags_by_code"].values()) == sum(reservoir["flags_by_year"].values())
    assert sum(by_code.values()) == snapshot["rows"]["dq_flags"]
    assert "needs review" in overview["note"]
    window = toolkit.quality_series("Lantern Lake")
    assert window["reservoir"] == "lantern_lake" and window["published"] and window["cleaned"]
    assert window["period"][1] == toolkit.as_of.isoformat()
    with pytest.raises(ToolError, match="at most 400 days"):
        toolkit.quality_series("lantern_lake", date(2020, 1, 1), date(2022, 1, 1))
    shifted = toolkit.quality_series("lantern_lake", code="level_volume")
    assert "level_volume" in shifted["flags_by_code"]
    with pytest.raises(ToolError, match="Unknown flag"):
        toolkit.quality_series("lantern_lake", code="nonsense")
    assert all(0 <= r["score_pct"] <= 100 for r in overview["reservoirs"] if r["score_pct"] is not None)


# ---- the forecast: clipped, and the caller's own trained model -------------------------------------------------------


def test_forecast_quantiles_stay_within_capacity(toolkit: DroughtToolkit) -> None:
    """A quantile model can step past full (the committed lantern_lake p90 at 180 days is 105.2 %): no view shows it."""
    raw = toolkit._forecast_by_series["lantern_lake"]["horizons"]["180"]
    assert raw["p90"] > 100 and raw["p50"] > 100
    q = toolkit.forecast_of("lantern_lake", 180)["horizons_days"]["180"]
    assert q["p50"] == pytest.approx(100.0) and q["p90"] == pytest.approx(100.0)
    assert q["p10"] == raw["p10"] and q["drivers_pp"] == raw["drivers_pp"]
    for series in toolkit._forecast_by_series:
        for v in toolkit.forecast_of(series)["horizons_days"].values():
            assert 0 <= v["p10"] <= v["p50"] <= v["p90"] <= 100


def _served(toolkit: DroughtToolkit, shift: float = 0.0) -> dict[str, Any]:
    """What `tenant_forecast` returns for a model trained on the snapshot: the committed horizons, moved by `shift`."""
    return {
        "model_name": "lightgbm_quantile",
        "series": {
            sid: {
                "origin": entry["origin"],
                "current_pct": entry["current_pct"],
                "horizons": {
                    h: {**v, **{q: v[q] + shift for q in ("p10", "p50", "p90")}} for h, v in entry["horizons"].items()
                },
            }
            for sid, entry in toolkit._forecast_by_series.items()
        },
    }


def test_the_outlook_recomputed_from_a_forecast_matches_the_committed_one(toolkit: DroughtToolkit) -> None:
    """The view recomputes each outlook from the readings and a forecast; fed the committed forecast, it gives back the
    committed outlook, so a tenant's forecast changes only what follows from the forecast.
    """
    view = toolkit.with_forecast(_served(toolkit))
    assert view is not toolkit and view.forecast_source["kind"] == "tenant"
    for sid, committed in toolkit.outlooks.items():
        mine = view.outlooks[sid]
        for key in ("as_of", "current_pct", "state", "days_in_state", "change_7d_pp", "change_30d_pp"):
            assert mine[key] == committed[key], (sid, key)
        assert mine["forecast_states"] == committed["forecast_states"]
        assert mine["crossings"] == committed["crossings"]
        assert mine["recent_transitions"] == committed["recent_transitions"]


def test_a_tenants_trained_forecast_reaches_every_tool_and_the_reference_stays(toolkit: DroughtToolkit) -> None:
    served = _served(toolkit, shift=-6.0)
    view = toolkit.with_forecast(served)
    assert toolkit.with_forecast(served) is view  # one view per trained forecast, not one per request
    base = toolkit.forecast_of("central", 90)["horizons_days"]["90"]["p50"]
    assert view.forecast_of("central", 90)["horizons_days"]["90"]["p50"] == pytest.approx(base - 6.0, abs=0.01)
    assert "trained model (lightgbm_quantile)" in view.forecast_of("central")["forecast_source"]
    assert view.control_room()["forecast_source"]["kind"] == "tenant"
    assert (
        view.what_if("central", "normal", 0, 90)["end_of_horizon_pct"]["p50"]
        < toolkit.what_if("central", "normal", 0, 90)["end_of_horizon_pct"]["p50"]
    )
    assert view.bulletin()["markdown"] != toolkit.bulletin()["markdown"]
    assert toolkit.forecast_source["kind"] == "reference"  # the shared toolkit itself is never changed


def test_a_forecast_from_other_readings_or_none_keeps_the_reference(toolkit: DroughtToolkit) -> None:
    """A model that started from another day or other values (another tenant's own data) does not belong with the
    snapshot's readings: the committed reference is used, as with no trained model at all.
    """
    assert toolkit.with_forecast(None) is toolkit
    assert toolkit.with_forecast({"model_name": "x", "series": {}}) is toolkit
    other_day = _served(toolkit)
    other_day["series"]["central"]["origin"] = "2026-01-01"
    assert toolkit.with_forecast(other_day) is toolkit
    other_values = _served(toolkit)
    other_values["series"]["highmoor"]["current_pct"] += 3
    assert toolkit.with_forecast(other_values) is toolkit
    no_quantiles = _served(toolkit)
    no_quantiles["series"]["westfield"] = None  # a model trained before the forecast carried its quantiles
    assert toolkit.with_forecast(no_quantiles) is toolkit


def test_tenant_forecast_is_cached_per_org_and_falls_back_when_prediction_has_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from assistant.core import drought_tools

    monkeypatch.setattr(drought_tools, "_tenant_forecasts", {})
    calls: list[str] = []

    def served() -> dict[str, Any]:
        calls.append("org-a")
        return {"model_name": "m", "series": [{"label": "central", "quantiles": {"origin": "x"}}, {"label": "y"}]}

    first = drought_tools.tenant_forecast("org-a", "water", served)
    assert first == {"model_name": "m", "series": {"central": {"origin": "x"}, "y": None}}
    assert drought_tools.tenant_forecast("org-a", "water", served) == first and calls == ["org-a"]

    def untrained() -> dict[str, Any]:
        raise httpx.HTTPStatusError("503", request=httpx.Request("GET", "http://p"), response=httpx.Response(503))

    assert drought_tools.tenant_forecast("org-b", "water", untrained) is None
    monkeypatch.setattr(drought_tools, "TENANT_FORECAST_CACHE_MAX", 2)
    drought_tools.tenant_forecast("org-c", "water", served)
    assert len(drought_tools._tenant_forecasts) == 2  # bounded: the oldest entry made room


def test_first_requests_at_once_share_one_toolkit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The control room asks for several views together: concurrent first calls must build one toolkit, not race."""
    from concurrent.futures import ThreadPoolExecutor

    from assistant.core import drought_tools

    drought_tools._toolkit.cache_clear()
    monkeypatch.setattr(tempfile_module(), "gettempdir", lambda: str(tmp_path))
    with ThreadPoolExecutor(max_workers=4) as pool:
        kits = list(pool.map(lambda _: drought_tools.get_toolkit(str(SCENARIO)), range(4)))
    assert all(kit is kits[0] for kit in kits)
    drought_tools.warm_toolkits(SCENARIO.parent, ["water_reservoirs_drought", "no_such_scenario"])
    drought_tools._toolkit.cache_clear()


def tempfile_module() -> Any:
    import tempfile

    return tempfile
