"""
- Title:    Records, datasets, hot tiles and freshness — what data-api reads besides metrics
- Author:   Angel Martinez-Tenor

- **Records** come straight from bronze (as received), with their *processing trail*: when it
  was produced, landed, archived to the lake, and whether the last successful transform's
  watermark has passed it (then it is in gold).
- **Datasets** are the curated tables a role may browse, with freshness against their SLO.
- **Hot** tiles read the per-minute aggregates data-ingest keeps (approximate, seconds).

Every query starts with `org_id = <caller's data org>` and the role's row filter.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from ai_circus_shared.contracts import get_one
from ai_circus_shared.data_policy import AccessDeniedError, DataAccess
from ai_circus_shared.data_product import RecordSpec, bronze_table, hot_table

from data_api.core.products import DataProduct
from data_api.core.warehouse import Query, Warehouse

MAX_LIST = 100


class NotFoundError(LookupError):
    """An unknown entity, dataset or record (404)."""

    pass


def record_spec(product: DataProduct, entity: str) -> RecordSpec:
    """The declared record entity, or NotFoundError."""
    spec = next((r for r in product.config.records if r.entity == entity), None)
    if spec is None:
        raise NotFoundError(f"Unknown entity {entity!r}.")
    return spec


def _row_filter(q: Query, access: DataAccess, column: str = "filter_value") -> str:
    if access.row_filter_values is None:
        return ""
    return " AND " + q.in_list(column, access.row_filter_values)


def last_transform(warehouse: Warehouse, product: DataProduct) -> dict[str, Any] | None:
    """The latest transform run (any outcome) and the latest successful one's watermark."""
    return warehouse.last_transform(product)


def _summary(spec: RecordSpec, payload: dict[str, Any]) -> dict[str, Any]:
    return {label: get_one(payload, path) for label, path in spec.summary.items()}


def get_record(warehouse: Warehouse, product: DataProduct, access: DataAccess, entity: str, key: str) -> dict[str, Any]:
    """One record from bronze with every version, its trail and any quarantine."""
    access.require_layer("bronze")
    spec = record_spec(product, entity)
    q = Query("postgres")
    table = q.ident(product.slug, bronze_table(spec.source))
    rows = warehouse.rows(
        q,
        f"""SELECT version, payload, payload_hash, event_ts, received_at, source_kind, delivery,
                                         contract_version, corrections, archived_at
                                  FROM {table} WHERE org_id = {q.param(access.org_id)}
                                  AND business_key = {q.param(key)}{_row_filter(q, access)} ORDER BY version""",
    )
    q2 = Query("postgres")
    quarantined = []
    if "quarantine" in access.access.layers or "ops" in access.access.layers:
        quarantined = warehouse.rows(
            q2,
            f"""SELECT quarantine_id, violations, received_at, status, source_kind
                                             FROM {q2.ident(product.slug, "ops_quarantine")}
                                             WHERE org_id = {q2.param(access.org_id)} AND business_key = {q2.param(key)}
                                             {_row_filter(q2, access)} ORDER BY received_at""",
        )
    if not rows and not quarantined:
        raise NotFoundError(f"No {entity} {key!r}.")
    transform = last_transform(warehouse, product)
    curated_through = transform.get("curated_through") if transform else None
    versions = []
    for row in rows:
        payload = row["payload"]
        stamp = payload.get("produced_at") if isinstance(payload, dict) else None
        trail = {
            "produced": stamp or row["event_ts"],
            "event_log": row["received_at"] if row["source_kind"] == "stream" else None,
            "validated": row["received_at"],
            "bronze": row["received_at"],
            "lake": row["archived_at"],
            "gold": curated_through if curated_through and curated_through >= row["received_at"] else None,
        }
        versions.append({
            **{k: v for k, v in row.items() if k != "payload"},
            "summary": _summary(spec, payload),
            "payload": access.mask(payload),
            "trail": trail,
        })
    current = versions[-1] if versions else None
    return {
        "entity": entity,
        "key": key,
        "label": spec.label,
        "current": current,
        "versions": versions,
        "quarantined": quarantined,
        "curated_through": curated_through,
    }


def list_records(
    warehouse: Warehouse, product: DataProduct, access: DataAccess, entity: str, search: str | None, limit: int
) -> list[dict[str, Any]]:
    """Latest records or a search, as summaries."""
    access.require_layer("bronze")
    spec = record_spec(product, entity)
    q = Query("postgres")
    table = q.ident(product.slug, bronze_table(spec.source))
    where = f"org_id = {q.param(access.org_id)}{_row_filter(q, access)}"
    if search:
        term = search.strip()[:100]
        where += (
            f" AND (search_text @@ plainto_tsquery('simple', {q.param(term)})"
            f" OR business_key ILIKE {q.param(term + '%')})"
        )
    rows = warehouse.rows(
        q,
        f"""SELECT DISTINCT ON (received_at, business_key) business_key, version, payload,
                                        event_ts, received_at, source_kind
                                 FROM {table} WHERE {where}
                                 ORDER BY received_at DESC, business_key, version DESC
                                 LIMIT {max(1, min(limit, MAX_LIST))}""",
    )
    return [
        {
            "key": r["business_key"],
            "version": r["version"],
            "event_ts": r["event_ts"],
            "received_at": r["received_at"],
            "source_kind": r["source_kind"],
            "summary": access.mask(_summary(spec, r["payload"])),
        }
        for r in rows
    ]


def datasets(warehouse: Warehouse, product: DataProduct, access: DataAccess) -> list[dict[str, Any]]:
    """The role's datasets with built state, row counts and freshness."""
    out = []
    now = datetime.now(UTC)
    for dataset in product.config.datasets:
        if dataset.layer not in access.access.layers:
            continue
        columns = warehouse.columns(product, dataset.layer, dataset.name)
        info: dict[str, Any] = {
            "name": dataset.name,
            "layer": dataset.layer,
            "label": dataset.label,
            "description": dataset.description,
            "grain": dataset.grain,
            "time_column": dataset.time_column,
            "freshness_minutes": dataset.freshness_minutes,
            "built": bool(columns),
            "columns": [c for c in columns if c not in access.hidden],
            "engine": warehouse.dialect_for(dataset.layer),
        }
        if columns and (access.row_filter_values is None or access.row_filter_column in columns):
            q = warehouse.query(dataset.layer)
            rel = q.ident(*warehouse.relation(product, dataset.layer, dataset.name))
            filt = _row_filter(q, access, q.ident(access.row_filter_column)) if access.row_filter_column else ""
            latest = f", max({q.ident(dataset.time_column)}) AS latest" if dataset.time_column else ""
            row = warehouse.rows(
                q, f"SELECT count(*) AS n{latest} FROM {rel} WHERE org_id = {q.param(access.org_id)}{filt}"
            )[0]
            info["rows"] = row["n"]
            info["latest"] = row.get("latest")
        elif columns:
            info["restricted"] = True
        out.append(info)
    transform = last_transform(warehouse, product)
    for info in out:
        info["curated_through"] = transform.get("curated_through") if transform and info["layer"] != "ops" else None
        slo = info["freshness_minutes"]
        stamp = info.get("curated_through")
        if slo and stamp:
            age = (now - datetime.fromisoformat(stamp)).total_seconds() / 60
            info["freshness"] = {"age_minutes": round(age, 1), "ok": age <= slo}
    return out


def row_filter_options(warehouse: Warehouse, product: DataProduct, access: DataAccess) -> list[dict[str, Any]]:
    """Readable names of the row-filter values (store_id S012 -> "Acme Málaga"), from the
    dataset `access.row_filter.labels` names: only the values the caller may see. While an
    admin is viewing as another role the list is the admin's own (view-as is admin-only),
    so the picker keeps offering every store.
    """
    row_filter = product.config.access.row_filter
    if row_filter is None or row_filter.labels is None:
        return []
    dataset = product.config.dataset(row_filter.labels.dataset)
    label = row_filter.labels.label
    as_caller = not access.viewing_as
    if as_caller:
        try:
            access.require_layer(dataset.layer)
        except AccessDeniedError:
            return []
        if label in access.hidden:
            return []
    columns = warehouse.columns(product, dataset.layer, dataset.name)
    if row_filter.column not in columns or label not in columns:
        return []
    q = warehouse.query(dataset.layer)
    rel = q.ident(*warehouse.relation(product, dataset.layer, dataset.name))
    filt = _row_filter(q, access, q.ident(row_filter.column)) if as_caller else ""
    value, name = q.ident(row_filter.column), q.ident(label)
    rows = warehouse.rows(
        q,
        f"SELECT DISTINCT {value} AS value, {name} AS label FROM {rel} "
        f"WHERE org_id = {q.param(access.org_id)}{filt} ORDER BY value LIMIT 500",
    )
    return [{"value": str(r["value"]), "label": r.get("label")} for r in rows if r.get("value") is not None]


def dataset_rows(
    warehouse: Warehouse,
    product: DataProduct,
    access: DataAccess,
    name: str,
    limit: int,
    equals: dict[str, str] | None = None,
) -> dict[str, Any]:
    """The latest rows of one dataset, filtered and masked; `equals` narrows it by visible columns."""
    try:
        dataset = product.config.dataset(name)
    except KeyError as exc:
        raise NotFoundError(f"Unknown dataset {name!r}.") from exc
    access.require_layer(dataset.layer)
    columns = warehouse.columns(product, dataset.layer, dataset.name)
    if not columns:
        raise NotFoundError(f"Dataset {name!r} has not been built yet.")
    access.require_columns(columns)
    visible = [c for c in columns if c not in access.hidden]
    q = warehouse.query(dataset.layer)
    rel = q.ident(*warehouse.relation(product, dataset.layer, dataset.name))
    filt = _row_filter(q, access, q.ident(access.row_filter_column)) if access.row_filter_column else ""
    for column, value in (equals or {}).items():
        if column not in visible:
            raise NotFoundError(f"Dataset {name!r} has no column {column!r} you may filter on.")
        cast = f"CAST({q.ident(column)} AS {'String' if q.dialect == 'clickhouse' else 'text'})"
        # A boolean reads 'true' in Postgres but 1 in a ClickHouse copy of it: accept both spellings.
        spellings = {"true": ("true", "1"), "false": ("false", "0")}.get(value.lower(), (value,))
        filt += f" AND {cast} IN ({', '.join(q.param(v) for v in spellings)})"
    order = f" ORDER BY {q.ident(dataset.time_column)} DESC" if dataset.time_column else ""
    rows = warehouse.rows(
        q,
        f"SELECT {', '.join(q.ident(c) for c in visible)} FROM {rel} "
        f"WHERE org_id = {q.param(access.org_id)}{filt}{order} LIMIT {max(1, min(limit, 500))}",
    )
    return {"dataset": name, "columns": visible, "rows": rows}


def hot(warehouse: Warehouse, product: DataProduct, access: DataAccess, name: str, minutes: int) -> dict[str, Any]:
    """One hot aggregate: the per-minute series and today's totals by dimension."""
    agg = next((a for a in product.config.hot_path if a.name == name), None)
    if agg is None:
        raise NotFoundError(f"Unknown hot aggregate {name!r}.")
    access.require_columns(list(agg.dimensions))
    q = Query("postgres")
    table = q.ident(product.slug, hot_table(agg.name))
    filt = _row_filter(q, access, q.ident(access.row_filter_column)) if access.row_filter_column else ""
    measures = [m.name for m in agg.measures]
    sums = ", ".join(f"sum({q.ident(m)}) AS {q.ident(m)}" for m in measures)
    now = datetime.now(UTC)
    since = now - timedelta(minutes=max(5, min(minutes, 24 * 60)))
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    org = q.param(access.org_id)
    series = warehouse.rows(
        q,
        f"""SELECT minute, {sums} FROM {table} WHERE org_id = {org}
                                   AND minute >= {q.param(since)}{filt} GROUP BY minute ORDER BY minute""",
    )
    dims = list(agg.dimensions)
    by_dim = {}
    for dim in dims:
        by_dim[dim] = warehouse.rows(
            q,
            f"""SELECT {q.ident(dim)} AS key, {sums} FROM {table}
                                           WHERE org_id = {org} AND minute >= {q.param(day_start)}{filt}
                                           GROUP BY {q.ident(dim)} ORDER BY 2 DESC LIMIT 50""",
        )
    totals = warehouse.rows(
        q,
        f"""SELECT {sums}, max(updated_at) AS updated_at FROM {table}
                                   WHERE org_id = {org} AND minute >= {q.param(day_start)}{filt}""",
    )[0]
    return {
        "name": agg.name,
        "label": agg.label,
        "measures": measures,
        "series": series,
        "today": totals,
        "today_by": by_dim,
        "since": since.isoformat(),
        "approximate": True,
    }


def capture_stats(warehouse: Warehouse, product: DataProduct, access: DataAccess) -> dict[str, Any]:
    """Counts only (no data, any role): per kafka source, how many records bronze holds, how
    many corrections it absorbed, how many are archived in the lake, and the latest arrival —
    the guided tour's live proof of each capture stage. Scoped to the caller's data org and,
    for a filtered role, its rows.
    """
    out = {}
    for source in product.config.kafka_sources():
        q = Query("postgres")
        row = warehouse.rows(
            q,
            f"""SELECT count(*) AS records, coalesce(sum(corrections), 0) AS corrections,
                       count(*) FILTER (WHERE archived_at IS NOT NULL) AS in_lake, max(received_at) AS latest
                FROM {q.ident(product.slug, bronze_table(source.id))}
                WHERE org_id = {q.param(access.org_id)}{_row_filter(q, access)}""",
        )[0]
        out[source.id] = row
    return out


def ops_summary(warehouse: Warehouse, product: DataProduct, access: DataAccess) -> dict[str, Any]:
    """Quarantine and file-delivery health — only for roles with the ops or quarantine layer."""
    if not ({"ops", "quarantine"} & set(access.access.layers)):
        return {}
    q = Query("postgres")
    org = q.param(access.org_id)
    quarantine = warehouse.rows(
        q,
        f"""SELECT count(*) FILTER (WHERE status = 'open') AS open,
                                              count(*) FILTER (WHERE status = 'open' AND day = current_date) AS today
                                       FROM {q.ident(product.slug, "ops_quarantine")} WHERE org_id = {org}""",
    )[0]
    deliveries = warehouse.rows(
        q,
        f"""SELECT status, count(*) AS n FROM {q.ident(product.slug, "ops_file_deliveries")}
                                       WHERE org_id = {org} GROUP BY status""",
    )
    return {"quarantine": quarantine, "deliveries": {r["status"]: r["n"] for r in deliveries}}


def quarantine_list(
    warehouse: Warehouse, product: DataProduct, access: DataAccess, status: str, limit: int
) -> list[dict[str, Any]]:
    """Quarantined records of one status, masked."""
    access.require_layer("quarantine")
    q = Query("postgres")
    rows = warehouse.rows(
        q,
        f"""SELECT quarantine_id, source_id, business_key, payload, raw, violations, source_kind,
                                        delivery, received_at, status
                                 FROM {q.ident(product.slug, "ops_quarantine")}
                                 WHERE org_id = {q.param(access.org_id)} AND status = {q.param(status)}
                                 {_row_filter(q, access)} ORDER BY received_at DESC LIMIT {max(1, min(limit, 200))}""",
    )
    return [{**r, "payload": access.mask(r["payload"])} for r in rows]
