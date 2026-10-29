"""
- Title:    Governed metrics — scenario-declared definitions, one query builder
- Author:   Angel Martinez-Tenor

A metric is declared once in the data product (`data_product.metrics`: dataset, aggregate
expression, format, allowed dimensions) and every consumer — the UI's tiles, the assistant's
tools, MCP clients — asks data-api for it by name. That is the semantic layer: the same
"revenue" everywhere, with the same tenant scope and row filter.

Only names from the declaration ever reach the SQL text (dimensions, filters, the dataset's
time column); periods and filter values are bound parameters.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

from ai_circus_shared.data_policy import DataAccess
from ai_circus_shared.data_product import DatasetSpec, MetricSpec

from data_api.core.products import DataProduct
from data_api.core.warehouse import Query, Warehouse

Period = Literal["today", "yesterday", "this_week", "last_week", "last_7_days", "last_30_days", "last_90_days", "all"]
Compare = Literal["none", "previous_period", "same_period_last_week"]
PERIODS: tuple[str, ...] = Period.__args__  # type: ignore[attr-defined]
MAX_ROWS = 2000


class MetricError(ValueError):
    """A request the metric's declaration does not allow (unknown dimension, bad period…)."""


def period_range(period: str, today: date, start: date | None = None, end: date | None = None) -> tuple[date, date]:
    """[start, end) for a named period, or an explicit range when `start`/`end` are given."""
    if start is not None or end is not None:
        if start is None or end is None or end < start:
            raise MetricError("A custom range needs from <= to.")
        return start, end + timedelta(days=1)
    monday = today - timedelta(days=today.weekday())
    ranges = {
        "today": (today, today + timedelta(days=1)),
        "yesterday": (today - timedelta(days=1), today),
        "this_week": (monday, today + timedelta(days=1)),
        "last_week": (monday - timedelta(days=7), monday),
        "last_7_days": (today - timedelta(days=6), today + timedelta(days=1)),
        "last_30_days": (today - timedelta(days=29), today + timedelta(days=1)),
        "last_90_days": (today - timedelta(days=89), today + timedelta(days=1)),
        "all": (date(2000, 1, 1), today + timedelta(days=1)),
    }
    if period not in ranges:
        raise MetricError(f"Unknown period {period!r}; one of {sorted(ranges)}.")
    return ranges[period]


def compare_range(compare: str, start: date, end: date) -> tuple[date, date] | None:
    """The comparison window for a period: none, the previous same-length span, or a week earlier."""
    if compare == "none":
        return None
    if compare == "same_period_last_week":
        return start - timedelta(days=7), end - timedelta(days=7)
    if compare == "previous_period":
        return start - (end - start), start
    raise MetricError(f"Unknown compare {compare!r}.")


def parse_filters(raw: str | None) -> dict[str, list[str]]:
    """`store_id:S012,S013;channel:store` -> {"store_id": ["S012", "S013"], "channel": ["store"]}."""
    filters: dict[str, list[str]] = {}
    for part in (raw or "").split(";"):
        if ":" not in part:
            continue
        name, values = part.split(":", 1)
        cleaned = [v.strip() for v in values.split(",") if v.strip()][:50]
        if name.strip() and cleaned:
            filters[name.strip()] = cleaned
    return filters


@dataclass(frozen=True)
class MetricRequest:
    """One metric query as the API received it."""

    metric: str
    dimensions: tuple[str, ...] = ()
    period: str = "last_7_days"
    start: date | None = None
    end: date | None = None
    compare: str = "none"
    filters: tuple[tuple[str, tuple[str, ...]], ...] = ()
    limit: int = 50
    order: Literal["value_desc", "value_asc", "dimension"] = "value_desc"


def _metric(product: DataProduct, name: str) -> tuple[MetricSpec, DatasetSpec]:
    metric = next((m for m in product.config.metrics if m.name == name), None)
    if metric is None:
        raise MetricError(f"Unknown metric {name!r}.")
    return metric, product.config.dataset(metric.dataset)


def _where(
    q: Query, dataset: DatasetSpec, access: DataAccess, start: date, end: date, filters: dict[str, list[str]]
) -> str:
    clauses = [f"org_id = {q.param(access.org_id)}"]
    if dataset.time_column:
        col = q.ident(dataset.time_column)
        if q.dialect == "clickhouse":
            col = f"toDate({col})"
        clauses += [f"{col} >= {q.param(start)}", f"{col} < {q.param(end)}"]
    if access.row_filter_values is not None and access.row_filter_column:
        clauses.append(q.in_list(q.ident(access.row_filter_column), access.row_filter_values))
    for name, values in filters.items():
        clauses.append(q.in_list(q.ident(name), values))
    return " AND ".join(clauses)


def _run(
    warehouse: Warehouse,
    product: DataProduct,
    metric: MetricSpec,
    dataset: DatasetSpec,
    access: DataAccess,
    dims: list[str],
    start: date,
    end: date,
    filters: dict[str, list[str]],
    limit: int,
    order: str,
) -> list[dict[str, Any]]:
    q = warehouse.query(dataset.layer)
    relation = q.ident(*warehouse.relation(product, dataset.layer, dataset.name))
    select_dims = [q.ident(d) for d in dims]
    columns = ", ".join([*select_dims, f"{metric.expression} AS value"])
    sql = f"SELECT {columns} FROM {relation} WHERE {_where(q, dataset, access, start, end, filters)}"
    if dims:
        sql += f" GROUP BY {', '.join(select_dims)}"
        sql += " ORDER BY " + (
            ", ".join(select_dims) if order == "dimension" else f"value {'ASC' if order == 'value_asc' else 'DESC'}"
        )
        sql += f" LIMIT {int(limit)}"
    return warehouse.rows(q, sql)


def query_metric(
    warehouse: Warehouse, product: DataProduct, access: DataAccess, request: MetricRequest, today: date
) -> dict[str, Any]:
    """Run a metric (and its comparison) for the caller's access; raises MetricError on a bad request."""
    metric, dataset = _metric(product, request.metric)
    access.require_layer(dataset.layer)
    allowed = set(metric.dimensions) | ({dataset.time_column} if dataset.time_column else set())
    dims = list(dict.fromkeys(request.dimensions))
    unknown = [d for d in dims if d not in allowed] + [f for f, _ in request.filters if f not in allowed]
    if unknown:
        raise MetricError(
            f"Metric {metric.name!r} cannot be split or filtered by {unknown}; allowed: {sorted(allowed)}."
        )
    columns = warehouse.columns(product, dataset.layer, dataset.name)
    if not columns:
        raise MetricError(f"Dataset {dataset.name!r} has not been built yet (the transform has not run).")
    access.require_columns(columns)
    filters = {name: list(values) for name, values in request.filters}
    start, end = period_range(request.period, today, request.start, request.end)
    limit = max(1, min(request.limit, MAX_ROWS))
    rows = _run(warehouse, product, metric, dataset, access, dims, start, end, filters, limit, request.order)
    total = _run(warehouse, product, metric, dataset, access, [], start, end, filters, 1, request.order)
    result: dict[str, Any] = {
        "metric": metric.name,
        "label": metric.label,
        "description": metric.description,
        "format": metric.format,
        "dataset": dataset.name,
        "dimensions": dims,
        "period": {"name": request.period, "from": start.isoformat(), "to": (end - timedelta(days=1)).isoformat()},
        "filters": filters,
        "total": total[0]["value"] if total else None,
        "rows": rows,
    }
    span = compare_range(request.compare, start, end)
    if span is not None:
        previous = _run(
            warehouse, product, metric, dataset, access, dims, span[0], span[1], filters, MAX_ROWS, "dimension"
        )
        previous_total = _run(
            warehouse, product, metric, dataset, access, [], span[0], span[1], filters, 1, "dimension"
        )
        by_key = {tuple(r[d] for d in dims): r["value"] for r in previous}
        for row in rows:
            row["compare_value"] = by_key.get(tuple(row[d] for d in dims))
            row["change_pct"] = _change(row["value"], row["compare_value"])
        compare_total = previous_total[0]["value"] if previous_total else None
        result["compare"] = {
            "name": request.compare,
            "from": span[0].isoformat(),
            "to": (span[1] - timedelta(days=1)).isoformat(),
            "total": compare_total,
            "change_pct": _change(result["total"], compare_total),
        }
    return result


def _change(value: Any, previous: Any) -> float | None:
    if value is None or previous in (None, 0):
        return None
    return round(100.0 * (float(value) - float(previous)) / abs(float(previous)), 1)


def catalogue(product: DataProduct, access: DataAccess) -> list[dict[str, Any]]:
    """The metrics whose dataset layer the role can read."""
    out = []
    for metric in product.config.metrics:
        dataset = product.config.dataset(metric.dataset)
        if dataset.layer not in access.access.layers:
            continue
        out.append({
            "name": metric.name,
            "label": metric.label,
            "description": metric.description,
            "format": metric.format,
            "dataset": dataset.name,
            "dimensions": metric.dimensions,
            "time_column": dataset.time_column,
            "expression": metric.expression,
        })
    return out
