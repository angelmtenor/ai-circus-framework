"""
- Title:    Data tools — the assistant's governed door into a data product
- Author:   Angel Martinez-Tenor

For a `kind: data_product` scenario the chat answers business questions with numbers, and
those numbers only ever come from data-api (docs/adr/0007): the declared metrics (the same
definitions as the dashboards and BI), records as received, dataset health and — for roles
allowed to — governed SQL. Every call forwards the caller's own Authorization header, plus
the "view as" role the admin may be demonstrating, so the assistant sees exactly what the
user would: same tenant, same row filter, same masks. Nothing here is domain-specific: the
tools' descriptions are filled from the scenario's own metric and record catalogue.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
from ai_circus_shared.scenario_schema import ScenarioDefinition
from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

_TIMEOUT = 20.0
_MAX_ROWS = 60
_PERIODS = "today, yesterday, this_week, last_week, last_7_days, last_30_days, last_90_days, all"


class DataApiClient:
    """data-api for one data product, called with the user's own token (and the admin's
    optional "view as" role), so the assistant sees exactly what the user may see.
    """

    def __init__(self, base_url: str, slug: str, authorization: str | None, view_as: dict[str, Any] | None) -> None:
        """Bind the product's URL and the caller's headers."""
        self._base = f"{base_url.rstrip('/')}/v1/{slug}"
        self._headers = {"Authorization": authorization} if authorization else {}
        if view_as and isinstance(view_as.get("role"), str):
            self._headers["X-Data-View-As"] = view_as["role"][:64]
            values = view_as.get("values") or []
            if isinstance(values, list) and values:
                self._headers["X-Data-View-As-Values"] = ",".join(str(v) for v in values)[:400]

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET a data-api path; the JSON answer or an error dict the model can read."""
        response = httpx.get(f"{self._base}{path}", params=params, headers=self._headers, timeout=_TIMEOUT)
        return self._result(response)

    def post(self, path: str, body: dict[str, Any]) -> Any:
        """POST a JSON body to a data-api path; same result shape as `get`."""
        response = httpx.post(f"{self._base}{path}", json=body, headers=self._headers, timeout=_TIMEOUT)
        return self._result(response)

    @staticmethod
    def _result(response: httpx.Response) -> Any:
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail")
            except ValueError:
                detail = response.text[:300]
            # Returned to the model as data (not raised): "not allowed for your role" is an
            # answer the user should hear, not a crash.
            return {"error": detail, "status": response.status_code}
        return response.json()


def _dump(value: Any) -> str:
    return json.dumps(value, default=str)[:12_000]


class _MetricArgs(BaseModel):
    metric: str = Field(description="A metric name from list_metrics.")
    dims: list[str] = Field(default=[], max_length=3, description="Dimensions to split by (the metric's own).")
    period: str = Field("last_7_days", description=f"One of: {_PERIODS}.")
    compare: str = Field("none", description="none, previous_period or same_period_last_week.")
    filters: dict[str, list[str]] = Field(default={}, description="Dimension -> allowed values.")
    limit: int = Field(15, ge=1, le=_MAX_ROWS)


class _RecordArgs(BaseModel):
    entity: str = Field(description="A record entity from list_metrics' `records`.")
    key: str = Field(max_length=128, description="The record's business key.")


class _SearchArgs(BaseModel):
    entity: str = Field(description="A record entity from list_metrics' `records`.")
    query: str = Field("", max_length=100, description="Free text or a key prefix; empty = the latest records.")
    limit: int = Field(5, ge=1, le=20)


class _SqlArgs(BaseModel):
    sql: str = Field(max_length=4000, description="One read-only SELECT over the curated dataset names.")


class _NoArgs(BaseModel):
    pass


class _ProductArgs(BaseModel):
    query: str = Field(max_length=60, description="Words of a product name, category, brand or SKU.")
    limit: int = Field(8, ge=1, le=20)


class _TogetherArgs(BaseModel):
    product_id: str = Field(max_length=32, description="The product's id (SKU) — find it with find_products.")


class _MlArgs(BaseModel):
    model: str = Field(description="A model slug from ml_models.")
    where: dict[str, str] = Field(
        default={},
        max_length=4,
        description="Column=value filters on the model's feature dataset (e.g. store_id, rider_group, customer_key).",
    )
    limit: int = Field(4, ge=1, le=10)


def _top_contributions(prediction: dict[str, Any], n: int = 6) -> dict[str, Any]:
    """A prediction without its long arrays: the n largest feature contributions only."""
    out = {}
    for key, value in prediction.items():
        if isinstance(value, dict) and value and all(isinstance(v, int | float) for v in value.values()):
            top = sorted(value.items(), key=lambda kv: -abs(kv[1]))[:n]
            out[key] = {k: round(float(v), 4) for k, v in top}
        elif not isinstance(value, list | dict) or key in ("decision", "gate"):
            out[key] = value
    return out


def build_data_tools(
    client: DataApiClient,
    definition: ScenarioDefinition,
    *,
    definitions: dict[str, ScenarioDefinition] | None = None,
    prediction: Any = None,
    authorization: str | None = None,
) -> list[BaseTool]:
    """The governed tools of a data product: metrics, records, SQL, datasets and pipeline health
    — plus, when it declares them, its catalogue and recommendations (apps.storefront) and the
    models trained on its feature marts (ml_exports, served by the prediction service).
    """
    config = definition.data_product
    assert config is not None
    shop = config.apps.storefront
    models = {
        e.scenario: (e, definitions[e.scenario])
        for e in config.ml_exports
        if definitions and e.scenario in definitions and definitions[e.scenario].dataset is not None
    }
    metric_names = ", ".join(m.name for m in config.metrics)
    records = config.records  # bound here: the closures below don't keep the `assert` narrowing
    entities = ", ".join(r.entity for r in records)

    def list_metrics() -> str:
        return _dump({
            "metrics": client.get("/metrics"),
            "records": [{"entity": r.entity, "label": r.label, "summary": list(r.summary)} for r in records],
        })

    def query_metric(
        metric: str,
        dims: list[str] | None = None,
        period: str = "last_7_days",
        compare: str = "none",
        filters: dict[str, list[str]] | None = None,
        limit: int = 15,
    ) -> str:
        params = {
            "dims": ",".join(dims or []),
            "period": period,
            "compare": compare,
            "limit": limit,
            "filters": ";".join(f"{k}:{','.join(v)}" for k, v in (filters or {}).items()),
        }
        return _dump(client.get(f"/metrics/{metric}", params))

    def get_record(entity: str, key: str) -> str:
        result = client.get(f"/records/{entity}/{key}")
        if isinstance(result, dict) and "versions" in result:
            result["versions"] = result["versions"][-3:]  # the latest versions are what matters
        return _dump(result)

    def search_records(entity: str, query: str = "", limit: int = 5) -> str:
        return _dump(client.get(f"/records/{entity}", {"q": query or None, "limit": limit}))

    def data_health() -> str:
        overview = client.get("/overview")
        datasets = client.get("/datasets")
        if isinstance(overview, dict) and "error" not in overview:
            overview = {k: overview.get(k) for k in ("access", "transform", "ops", "tiers", "marts_engine", "now")}
        if isinstance(datasets, list):
            datasets = [
                {
                    k: d.get(k)
                    for k in (
                        "name",
                        "layer",
                        "rows",
                        "latest",
                        "freshness",
                        "freshness_minutes",
                        "restricted",
                        "built",
                    )
                }
                for d in datasets
            ]
        return _dump({"overview": overview, "datasets": datasets})

    def run_sql(sql: str) -> str:
        result = client.post("/query", {"sql": sql})
        if isinstance(result, dict) and isinstance(result.get("rows"), list):
            result["rows"] = result["rows"][:_MAX_ROWS]
        return _dump(result)

    def find_products(query: str, limit: int = 8) -> str:
        assert shop is not None
        result = client.get(f"/datasets/{shop.catalogue}/rows", {"limit": 500})
        if not isinstance(result, dict) or "rows" not in result:
            return _dump(result)
        words = query.lower().split()
        f = shop.fields
        hits = [
            r
            for r in result["rows"]
            if all(
                w in " ".join(str(r.get(c, "")) for c in (f.id, f.name, f.group, f.brand or "")).lower() for w in words
            )
        ]
        keep = [c for c in (f.id, f.name, f.group, f.brand, f.subtitle, f.price, f.rank, f.available) if c]
        return _dump({"products": [{c: r.get(c) for c in keep} for r in hits[:limit]], "matches": len(hits)})

    def bought_together(product_id: str) -> str:
        assert shop is not None and shop.recommendations is not None
        return _dump(
            client.get(
                f"/datasets/{shop.recommendations}/rows", {"limit": 8, "where": f"{shop.fields.id}={product_id}"}
            )
        )

    def ml_models() -> str:
        return _dump([
            {
                "model": slug,
                "title": child.title,
                "task": child.model.task_type if child.model else None,
                "predicts": child.model.target_label if child.model else child.dataset.target,  # type: ignore[union-attr]
                "meaning": child.model.target_description if child.model else None,
                "features": child.dataset.feature_columns,  # type: ignore[union-attr]
                "row_key": child.dataset.index_col,  # type: ignore[union-attr]
                "feature_dataset": export.dataset,
            }
            for slug, (export, child) in models.items()
        ])

    def ml_predict(model: str, where: dict[str, str] | None = None, limit: int = 4) -> str:
        if model not in models:
            return _dump({"error": f"Unknown model {model!r}; known: {sorted(models)}."})
        export, child = models[model]
        ds = child.dataset
        assert ds is not None
        rows = client.get(
            f"/datasets/{export.dataset}/rows",
            {"limit": 200, "where": [f"{k}={v}" for k, v in (where or {}).items()]},
        )
        if not isinstance(rows, dict) or "rows" not in rows:
            return _dump(rows)
        # Rows whose target is still unknown are "now" (e.g. next week's forecast); otherwise the latest.
        candidates = sorted(rows["rows"], key=lambda r: r.get(ds.target) is not None)[:limit]
        if not candidates:
            return _dump({"error": "No feature rows match those filters.", "where": where})
        records = [{c: r.get(c) for c in ds.feature_columns} for r in candidates]
        try:
            scored = prediction.predict(scenario_slug=model, authorization=authorization, records=records)
        except httpx.HTTPStatusError as exc:
            return _dump({"error": f"prediction service: {exc.response.status_code} {exc.response.text[:200]}"})
        predictions = scored.get("predictions", []) if isinstance(scored, dict) else []
        return _dump({
            "model": model,
            "predicts": child.model.target_label if child.model else ds.target,
            "results": [
                {
                    "row": r.get(ds.index_col),
                    **{c: r.get(c) for c in ds.display_columns},
                    "actual": r.get(ds.target),
                    "features": {c: r.get(c) for c in ds.feature_columns},
                    "prediction": _top_contributions(p) if isinstance(p, dict) else p,
                }
                for r, p in zip(candidates, predictions, strict=False)
            ],
        })

    extra: list[BaseTool] = []
    if shop is not None:
        extra.append(
            StructuredTool.from_function(
                func=find_products,
                name="find_products",
                args_schema=_ProductArgs,
                description="Search the catalogue (name, category, brand, SKU): ids, prices, sizes, best-seller rank.",
            )
        )
        if shop.recommendations is not None:
            extra.append(
                StructuredTool.from_function(
                    func=bought_together,
                    name="bought_together",
                    args_schema=_TogetherArgs,
                    description="What customers buy together with a product — association rules over the last year of "
                    "baskets (together = baskets with both, confidence, lift). The web shop's recommender.",
                )
            )
    if models and prediction is not None:
        extra += [
            StructuredTool.from_function(
                func=ml_models,
                name="ml_models",
                args_schema=_NoArgs,
                description="The machine-learning models trained on this data product's feature marts: what each "
                "predicts, its features and which columns to filter its rows by.",
            ),
            StructuredTool.from_function(
                func=ml_predict,
                name="ml_predict",
                args_schema=_MlArgs,
                description="Score rows of a model's feature dataset (filtered by `where`) with the trained model: "
                "the prediction plus its largest feature contributions (SHAP). Rows without a known target come "
                "first — e.g. next week's demand forecast for a shop and rider group.",
            ),
        ]

    return [
        *extra,
        StructuredTool.from_function(
            func=list_metrics,
            name="list_metrics",
            args_schema=_NoArgs,
            description="The governed metrics (name, definition, format, allowed dimensions) and record entities of "
            "this data product. Call it first when unsure which metric answers a question.",
        ),
        StructuredTool.from_function(
            func=query_metric,
            name="query_metric",
            args_schema=_MetricArgs,
            description=f"Compute a governed metric ({metric_names}) for a period, optionally split by its "
            "dimensions, filtered, and compared with an earlier period (change_pct). Money with format "
            "currency_cents is in cents: divide by 100 for euros. Prefer this over SQL.",
        ),
        StructuredTool.from_function(
            func=get_record,
            name="get_record",
            args_schema=_RecordArgs,
            description=f"One record ({entities}) by its key, as received, with its processing trail "
            "(produced → event log → bronze → lake → gold) and any quarantine.",
        ),
        StructuredTool.from_function(
            func=search_records,
            name="search_records",
            args_schema=_SearchArgs,
            description=f"Search records ({entities}) by text or key prefix, or list the latest ones.",
        ),
        StructuredTool.from_function(
            func=data_health,
            name="data_health",
            args_schema=_NoArgs,
            description="Freshness of every dataset vs its SLO, the last transform run (tests), quarantine and file "
            "reconciliation, and the caller's role. Use for 'is the data fresh/complete' questions.",
        ),
        StructuredTool.from_function(
            func=run_sql,
            name="run_sql",
            args_schema=_SqlArgs,
            description="Governed read-only SQL over the curated dataset names, for questions no metric answers. "
            "Only some roles may use it; the answer says so otherwise.",
        ),
    ]


def data_system_prompt(definition: ScenarioDefinition) -> str:
    """System prompt of a data product's assistant: its metrics, datasets and the rules for using them."""
    config = definition.data_product
    assert config is not None
    metrics = "\n".join(
        f"- {m.name} ({m.format}): {m.description}; by {', '.join(m.dimensions) or 'time only'}" for m in config.metrics
    )
    return (
        f"{definition.chat.context.strip()}\n\n"
        "You answer from a governed data product through tools — never from memory. Each number you give must come "
        "from a tool result in this conversation; say which metric and period it is, and how fresh the data is "
        "(data_health or the result's period). Results are already restricted to what the user may see: if a tool "
        "says a dataset or operation isn't available for the role, tell the user so plainly. Money in "
        "currency_cents is in cents — convert to euros. When a chart or table helps, call render_chart / "
        "render_table with values copied exactly from a tool result.\n\n"
        f"Governed metrics:\n{metrics}\n"
        + (
            "\nThe catalogue and the web shop's recommender are tools too: find_products for products and their ids, "
            "bought_together for what customers buy with a product (cite together/confidence/lift as evidence).\n"
            if config.apps.storefront
            else ""
        )
        + (
            "\nMachine-learning models are trained on this data product's feature marts (ml_models lists them): "
            "use ml_predict for forecasts and propensities and explain them with the returned feature "
            "contributions. A prediction is an estimate — say so, and never present it as a measured number.\n"
            if config.ml_exports
            else ""
        )
    )
