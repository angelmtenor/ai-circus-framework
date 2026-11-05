"""GET /recommend/{slug} and /items (docs/adr/0018)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from ai_circus_shared.auth import Identity
from fastapi import FastAPI
from fastapi.testclient import TestClient

from prediction.api import _model_cache, _scenario_definition, router
from prediction.core.identity import resolve_identity

DOCUMENT = {
    "selected": "association_rules",
    "top_k": 2,
    "items": {"FIN": {"label": "Fin", "baskets": 9}, "BRD": {"label": "Board", "baskets": 4}},
    "candidates": {
        "association_rules": {"BRD": [{"item": "FIN", "score": 2.5}], "FIN": []},
        "item_factors": {"BRD": [{"item": "FIN", "score": 0.9}], "FIN": [{"item": "BRD", "score": 0.9}]},
    },
}


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[resolve_identity] = lambda: Identity(subject="u", org_id="org", roles=frozenset())
    app.dependency_overrides[_scenario_definition] = lambda: SimpleNamespace(slug="surf_ml~recommender")
    artifacts = SimpleNamespace(recommendations=DOCUMENT)
    app.dependency_overrides[_model_cache] = lambda: SimpleNamespace(get=lambda _org, _slug: artifacts)
    return TestClient(app)


def test_recommend_serves_every_candidate_side_by_side(client: TestClient) -> None:
    body = client.get("/recommend/surf_ml~recommender", params={"item": "BRD"}).json()
    assert body["label"] == "Board" and body["selected"] == "association_rules"
    assert body["candidates"]["association_rules"] == [{"item": "FIN", "label": "Fin", "score": 2.5}]
    assert body["candidates"]["item_factors"][0]["score"] == pytest.approx(0.9)


def test_recommend_404s_an_unknown_item_and_lists_the_known_ones(client: TestClient) -> None:
    assert client.get("/recommend/surf_ml~recommender", params={"item": "NOPE"}).status_code == 404
    items = client.get("/recommend/surf_ml~recommender/items").json()
    assert [i["item"] for i in items] == ["FIN", "BRD"] and items[0]["baskets"] == 9


def test_recommend_is_a_conflict_for_a_model_that_is_not_a_recommender(client: TestClient) -> None:
    client.app.dependency_overrides[_model_cache] = lambda: SimpleNamespace(  # type: ignore[attr-defined]
        get=lambda _org, _slug: SimpleNamespace(recommendations=None)
    )
    assert client.get("/recommend/churn", params={"item": "X"}).status_code == 409
