"""The committed files of scenarios/water_reservoirs_drought that other code reads: they must stay valid."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from ai_circus_shared.governance import GovernanceConfig
from ai_circus_shared.rulebook import load

SCENARIO = Path(__file__).resolve().parents[3] / "scenarios" / "water_reservoirs_drought"
pytestmark = pytest.mark.skipif(not SCENARIO.exists(), reason="scenario not present")


def test_the_governance_block_is_valid_and_declares_what_the_spec_says() -> None:
    block = GovernanceConfig.model_validate(yaml.safe_load((SCENARIO / "scenario.yaml").read_text())["governance"])
    assert block.ai_act.risk_category == "limited" and block.ai_act.annex_iii_area is None
    assert block.human_oversight.mode == "approve" and block.data.personal_data is False
    assert "not a safety component" in (block.intended_purpose or "")


def test_snapshot_manifest_matches_the_files_and_says_the_data_is_synthetic() -> None:
    import hashlib

    manifest = json.loads((SCENARIO / "sample_data" / "SNAPSHOT.json").read_text())
    for name, digest in manifest["sha256"].items():
        assert hashlib.sha256((SCENARIO / "sample_data" / name).read_bytes()).hexdigest() == digest, name
    assert manifest["data_provenance"] == "synthetic"
    assert any("generate_water_data.py" in line for line in manifest["attribution"])


def test_the_scenario_names_no_real_place_or_source() -> None:
    """The basin is fictional: its text files must not drift back to a real region, river or data portal."""
    real = ("catal", "llobregat", "tarragona", "meteo", "generalitat", "transparencia", "socrata", "gn9e")
    texts = [p for p in SCENARIO.rglob("*") if p.suffix in {".yaml", ".md", ".json"}]
    found = {str(p.relative_to(SCENARIO)): w for p in texts for w in real if w in p.read_text().lower()}
    assert found == {}


def test_systems_cover_every_reservoir_exactly_once_and_ids_do_not_clash() -> None:
    config = yaml.safe_load((SCENARIO / "config" / "systems.yaml").read_text())
    members = [r for s in config["systems"] for r in s["reservoirs"]]
    assert sorted(members) == sorted(config["reservoirs"]) and len(members) == len(set(members))
    assert not {s["id"] for s in config["systems"]} & set(config["reservoirs"])


def test_the_rulebook_loads_and_its_packages_cover_every_state() -> None:
    book = load(SCENARIO / "rulebook" / "rulebook.yaml")
    assert [p.id for p in book.packages()] == book.state_model().ids()


def test_the_committed_backtest_report_says_what_passed_and_what_did_not() -> None:
    report = json.loads((SCENARIO / "backtest.json").read_text())
    assert all(report["acceptance"].values())
    assert report["drought_weak_spots"], "the weak spots must stay on the record"
    assert "Reference implementation on synthetic data" in (SCENARIO / "backtest_report.md").read_text()
