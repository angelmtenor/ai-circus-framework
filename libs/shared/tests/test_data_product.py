"""The data_product scenario kind: schema validation, contracts and the access policy."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from ai_circus_shared.auth import Identity, _string_claims
from ai_circus_shared.contracts import Contract, get_one, get_path, parse_timestamp
from ai_circus_shared.data_policy import AccessDeniedError, resolve_access
from ai_circus_shared.data_product import ContractRule, MetricSpec, check_files, layer_schema
from ai_circus_shared.scenario_schema import ScenarioDefinition, load_all

SCENARIOS = Path(__file__).resolve().parents[3] / "scenarios"


def raw_retail() -> dict[str, Any]:
    return yaml.safe_load((SCENARIOS / "surf_retail" / "scenario.yaml").read_text())


def test_every_shipped_data_product_is_valid_and_ships_its_files() -> None:
    products = [d for d in load_all(SCENARIOS) if d.kind == "data_product"]
    assert products, "at least one data product ships"
    for definition in products:
        assert definition.data_product is not None
        assert check_files(definition.data_product, SCENARIOS / definition.slug) == []


def test_every_ml_export_trains_a_shipped_tabular_scenario() -> None:
    """ml_exports point at tabular_ml scenarios that ship their sample (so they also run without
    the data tier), and the feature mart is a gold dataset whose sample carries every column."""
    scenarios = {d.slug: d for d in load_all(SCENARIOS)}
    exports = [(d, e) for d in scenarios.values() if d.data_product for e in d.data_product.ml_exports]
    assert exports
    for product, export in exports:
        child = scenarios.get(export.scenario)
        assert child is not None and child.kind == "tabular_ml" and child.dataset is not None, export
        seed = SCENARIOS / child.slug / child.dataset.seed_file
        assert seed.exists(), f"{child.slug} ships no {child.dataset.seed_file}"
        header = seed.read_text().splitlines()[0].split(",")
        needed = {child.dataset.index_col, child.dataset.target, *child.dataset.feature_columns}
        assert needed <= set(header), f"{seed.name} lacks {sorted(needed - set(header))}"
        assert product.data_product.dataset(export.dataset).layer == "gold"  # type: ignore[union-attr]


def test_data_product_block_is_required_for_and_only_for_its_kind() -> None:
    raw = raw_retail()
    del raw["data_product"]
    with pytest.raises(ValidationError, match="requires a `data_product` block"):
        ScenarioDefinition.model_validate(raw)
    raw = raw_retail()
    raw["ui_extras"] = {"kind": "region_map", "levels": []}
    with pytest.raises(ValidationError):
        ScenarioDefinition.model_validate(raw)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda dp: dp["metrics"][0].update(dataset="nope"), "is not declared"),
        (lambda dp: dp["sources"][2].update(lands_in="nope"), "is not a kafka source"),
        (lambda dp: dp["sources"][0].update(topic="other.pos.receipts.v1"), "must start with the domain"),
        (lambda dp: dp["hot_path"][0].update(sources=["nope"]), "unknown sources"),
        (lambda dp: dp["access"].update(default_role="ghost"), "default_role"),
        (lambda dp: dp["stage_labels"].update(teleport="x"), "unknown stages"),
        (lambda dp: dp["sources"][0]["rules"][3].update(reference="nowhere.id"), "not a CDC table"),
        (lambda dp: dp["metrics"][0].update(expression="sum(x); drop table y"), "plain aggregate"),
        (lambda dp: dp["datasets"][0].update(name="Bad-Name"), "String should match pattern"),
        (lambda dp: dp["access"]["row_filter"]["labels"].update(dataset="nope"), "row_filter.labels"),
        (lambda dp: dp["dashboard"][0].update(dims=["region"]), "dims == \\[places.key\\]"),
        (lambda dp: dp["dashboard"][0].update(metrics=["nope"]), "unknown metric 'nope'"),
        (lambda dp: dp["dashboard"][0]["places"].update(dataset="nope"), "places dataset"),
        (lambda dp: dp["dashboard"][2].update(metrics=["units"]), "chart: map only"),
    ],
)
def test_references_are_checked(mutate: Any, message: str) -> None:
    raw = raw_retail()
    mutate(raw["data_product"])
    with pytest.raises(ValidationError, match=message):
        ScenarioDefinition.model_validate(raw)


def test_rule_arguments_must_match_its_type() -> None:
    with pytest.raises(ValidationError, match="needs"):
        ContractRule(name="r", type="sum_equals", description="d", items="a[*].b")
    with pytest.raises(ValidationError, match="plain aggregate"):
        MetricSpec(name="m", label="m", description="d", dataset="x", expression="(select 1 from t)")


def test_layer_schema() -> None:
    assert layer_schema("retail", "gold") == "retail_gold"
    assert layer_schema("retail", "ops") == "retail"


def test_paths() -> None:
    payload = {"lines": [{"qty": 1}, {"qty": 2}], "totals": {"t": 3}, "a": None}
    assert get_path(payload, "lines[*].qty") == [1, 2]
    assert get_one(payload, "totals.t") == 3
    assert get_path(payload, "missing.x") == []
    assert parse_timestamp("2026-10-28T10:00:00") == datetime(2026, 10, 28, 10, tzinfo=UTC)
    assert parse_timestamp("nope") is None


def test_contract_reports_every_violation() -> None:
    schema = {
        "type": "object",
        "required": ["id", "lines", "total", "ts"],
        "properties": {"id": {"type": "string"}, "ts": {"type": "string", "format": "date-time"}},
    }
    rules = [
        ContractRule(name="adds_up", type="sum_equals", description="d", items="lines[*].v", total="total"),
        ContractRule(name="no_zero", type="nonzero", description="d", field="lines[*].v"),
        ContractRule(name="known", type="known_value", description="d", field="id", reference="things.id"),
        ContractRule(name="past", type="not_future", description="d", field="ts"),
    ]
    contract = Contract(schema, rules)
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    bad = {"id": "x", "lines": [{"v": 0}, {"v": 2}], "total": 5, "ts": future}
    violations = {v.rule for v in contract.check(bad, lookup=lambda *_a: False)}
    assert violations == {"adds_up", "no_zero", "known", "past"}
    good = {"id": "x", "lines": [{"v": 1}, {"v": 2}], "total": 3, "ts": datetime.now(UTC).isoformat()}
    assert contract.check(good, lookup=lambda *_a: True) == []
    assert [v.rule for v in contract.check({"id": 1})] == ["schema"] * len(contract.check({"id": 1}))
    assert contract.check({**good, "ts": "not a date"})[0].rule == "schema"


def config() -> Any:
    return ScenarioDefinition.model_validate(raw_retail()).data_product.access  # type: ignore[union-attr]


def test_platform_identity_is_admin_and_may_view_as() -> None:
    admin = Identity(subject="admin", org_id="admin", roles=frozenset())
    access = resolve_access(admin, config(), data_org_id="demo", is_platform=True)
    assert access.role == "retail_admin" and access.can_simulate
    manager = resolve_access(
        admin,
        config(),
        data_org_id="demo",
        is_platform=True,
        view_as="retail_store_manager",
        view_as_values=("S012", " "),
    )
    assert manager.row_filter_values == ("S012",) and manager.viewing_as and not manager.can_simulate
    assert manager.mask({"a": 1, "loyalty_id": "x", "n": [{"customer_key": "k", "b": 2}]}) == {"a": 1, "n": [{"b": 2}]}
    with pytest.raises(AccessDeniedError):
        resolve_access(admin, config(), data_org_id="demo", is_platform=True, view_as="ghost")


def test_keycloak_user_role_and_claims() -> None:
    user = Identity(
        subject="u1",
        org_id="acme",
        roles=frozenset({"retail_store_manager"}),
        attributes=(("stores", ("S012", "S013")),),
    )
    access = resolve_access(user, config(), data_org_id="acme", is_platform=False)
    assert access.row_filter_values == ("S012", "S013")
    with pytest.raises(AccessDeniedError):
        access.require_sql()
    with pytest.raises(AccessDeniedError):
        access.require_columns(["org_id", "region"])
    access.require_columns(["org_id", "store_id"])
    nobody = resolve_access(
        Identity(subject="u2", org_id="acme", roles=frozenset()), config(), data_org_id="demo", is_platform=False
    )
    assert nobody.role == "retail_analyst" and not nobody.can_simulate  # reading the fallback org's data
    assert nobody.describe()["fallback_data"] is True


def test_filtered_role_without_claim_sees_nothing() -> None:
    user = Identity(subject="u1", org_id="acme", roles=frozenset({"retail_store_manager"}))
    assert resolve_access(user, config(), data_org_id="acme", is_platform=False).row_filter_values == ()


def test_string_claims_are_captured_and_bounded() -> None:
    claims = {"sub": "u", "stores": ["S012"], "realm_access": {"roles": []}, "email": "a@b", "n": 3, "big": ["x" * 500]}
    captured = dict(_string_claims(claims))
    assert captured["stores"] == ("S012",) and captured["email"] == ("a@b",)
    assert "sub" not in captured and "n" not in captured and len(captured["big"][0]) == 200
    identity = Identity(subject="u", org_id="o", roles=frozenset(), attributes=tuple(captured.items()))
    assert identity.attribute("stores") == ("S012",) and identity.attribute("nope") == ()
    hash(identity)  # still hashable


def test_raw_retail_is_not_mutated_by_fixtures() -> None:
    assert copy.deepcopy(raw_retail()) == raw_retail()
