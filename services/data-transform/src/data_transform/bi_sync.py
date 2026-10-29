"""
- Title:    BI as code — sync every data product's Superset dashboards through the REST API
- Author:   Angel Martinez-Tenor

`python -m data_transform.bi_sync` (the bi tier's Job, `make k3s-data-bi-up`) reads each data
product's `bi_dashboards` file and creates or updates, idempotently by name:

1. the Superset **databases** — the warehouse (Postgres) and, when the analytics tier runs, the
   marts on ClickHouse;
2. one **dataset** per table a chart reads, with the data product's governed metrics as its
   saved metrics (same SQL as data-api's, money converted from cents to euros) — charts carry
   no SQL of their own;
3. the **charts** and **dashboards** (layout, auto-refresh);
4. **row-level security**: a base filter keeping every chart on the shared demo tenant
   (`DATA_FALLBACK_ORG_ID`), and the file's `row_security` roles (e.g. a store manager who only
   sees their store) with a demo user each.

Nothing here knows the domain: dashboards, metrics and roles all come from the scenario.
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any

import httpx
import yaml
from ai_circus_shared.data_product import DataProductConfig, MetricSpec, layer_schema

from data_transform.core.logger import configure_logger, get_logger
from data_transform.core.products import DataProduct, load_products

logger = get_logger(__name__)

WAREHOUSE_DB = "Data platform · warehouse (Postgres)"
MARTS_DB = "Data platform · marts (ClickHouse)"
VIZ = {
    "big_number": "big_number_total",
    "line": "echarts_timeseries_line",
    "bar": "echarts_timeseries_bar",
    "pie": "pie",
    "table": "table",
}
CHART_HEIGHT = {"big_number": 30, "line": 60, "bar": 60, "pie": 60, "table": 60}


_RISON_ID = __import__("re").compile(r"^[A-Za-z_./~][-A-Za-z0-9_./~]*$")


def rison(value: Any) -> str:
    """Encode a value as rison — the query-string JSON dialect Superset's `q` parameter takes
    (JSON itself only parses while every string is a bare identifier).
    """
    if isinstance(value, dict):
        return "(" + ",".join(f"{rison(k)}:{rison(v)}" for k, v in value.items()) + ")"
    if isinstance(value, list):
        return "!(" + ",".join(rison(v) for v in value) + ")"
    if value is True:
        return "!t"
    if value is False:
        return "!f"
    if value is None:
        return "!n"
    if isinstance(value, int | float):
        return str(value)
    text = str(value)
    if _RISON_ID.match(text) and not text[0].isdigit() and text[0] != "-":
        return text
    return "'" + text.replace("!", "!!").replace("'", "!'") + "'"


class Superset:
    """A small authenticated client for Superset's REST API (JWT + CSRF session)."""

    def __init__(self, base_url: str, username: str, password: str) -> None:
        """Log in and fetch a CSRF token for the session."""
        self.http = httpx.Client(base_url=base_url.rstrip("/"), timeout=60)
        token = self.http.post(
            "/api/v1/security/login",
            json={"username": username, "password": password, "provider": "db", "refresh": False},
        ).json()["access_token"]
        self.http.headers["Authorization"] = f"Bearer {token}"
        csrf = self.http.get("/api/v1/security/csrf_token/").json()["result"]
        self.http.headers.update({"X-CSRFToken": csrf, "Referer": base_url})

    def call(self, method: str, path: str, body: Any = None, **params: Any) -> Any:
        """One API call; raises with Superset's message on an error status."""
        response = self.http.request(method, path, json=body, params=params or None)
        if response.status_code >= 400:
            raise RuntimeError(f"{method} {path}: {response.status_code} {response.text[:400]}")
        return response.json() if response.content else {}

    def find(self, resource: str, column: str, value: Any) -> dict[str, Any] | None:
        """The first `resource` whose `column` equals `value`, or None."""
        query = rison({"filters": [{"col": column, "opr": "eq", "value": value}], "page_size": 100})
        result = self.call("GET", f"/api/v1/{resource}/", q=query).get("result", [])
        return result[0] if result else None

    def upsert(self, resource: str, column: str, value: Any, body: dict[str, Any]) -> int:
        """Create the resource, or update the existing one with the same `column` value; its id."""
        found = self.find(resource, column, value)
        if found is None:
            return int(self.call("POST", f"/api/v1/{resource}/", body)["id"])
        self.call("PUT", f"/api/v1/{resource}/{found['id']}", body)
        return int(found["id"])


def _saved_metric(metric: MetricSpec) -> dict[str, Any]:
    if metric.format == "currency_cents":
        return {
            "metric_name": metric.name,
            "verbose_name": f"{metric.label} (€)",
            "expression": f"({metric.expression}) / 100.0",
            "d3format": ",.2f",
            "description": metric.description,
        }
    return {
        "metric_name": metric.name,
        "verbose_name": metric.label,
        "expression": metric.expression,
        "d3format": ",.1f" if metric.format == "percent" else ",d",
        "description": metric.description,
    }


def _time_filter(column: str, comparator: str) -> dict[str, Any]:
    return {
        "clause": "WHERE",
        "subject": column,
        "operator": "TEMPORAL_RANGE",
        "comparator": comparator,
        "expressionType": "SIMPLE",
    }


def chart_params(chart: dict[str, Any], time_column: str | None) -> dict[str, Any]:
    """Superset `params` for one declared chart."""
    viz, metric = chart["viz"], chart["metric"]
    window = (
        "Today : Tomorrow"
        if chart.get("today")
        else (
            f"Last {chart['last_minutes']} minutes : now"
            if chart.get("last_minutes")
            else chart.get("time_range", "Last month")
        )
    )
    column = chart.get("time") or time_column
    filters = [_time_filter(column, window)] if column else []
    params: dict[str, Any] = {"viz_type": VIZ[viz], "adhoc_filters": filters, "row_limit": chart.get("limit", 1000)}
    if viz == "big_number":
        params.update(metric=metric, header_font_size=0.4, subheader_font_size=0.15)
    elif viz == "line":
        params.update(
            x_axis=chart["time"],
            time_grain_sqla="PT1M" if chart.get("last_minutes") else "P1D",
            metrics=[metric],
            groupby=chart.get("groupby", []),
            show_legend=True,
            rich_tooltip=True,
        )
    elif viz == "bar":
        params.update(
            x_axis=chart["groupby"][0],
            metrics=[metric],
            groupby=chart["groupby"][1:],
            xAxisForceCategorical=True,
            orientation="vertical",
            x_axis_sort_asc=bool(chart.get("sort_by_dimension")),
            x_axis_sort=chart["groupby"][0] if chart.get("sort_by_dimension") else metric,
            show_value=False,
        )
    elif viz == "pie":
        params.update(metric=metric, groupby=chart["groupby"], show_labels=True, donut=True, show_legend=True)
    else:
        params.update(
            query_mode="aggregate",
            groupby=chart["groupby"],
            metrics=[metric],
            order_desc=True,
            timeseries_limit_metric=metric,
        )
    return params


def layout(title: str, charts: list[tuple[int, str, int, int]]) -> dict[str, Any]:
    """Superset's position_json: rows of up to 12 columns, charts in declaration order."""
    position: dict[str, Any] = {
        "DASHBOARD_VERSION_KEY": "v2",
        "ROOT_ID": {"type": "ROOT", "id": "ROOT_ID", "children": ["GRID_ID"]},
        "GRID_ID": {"type": "GRID", "id": "GRID_ID", "children": [], "parents": ["ROOT_ID"]},
        "HEADER_ID": {"id": "HEADER_ID", "type": "HEADER", "meta": {"text": title}},
    }
    row: list[str] = []
    used = 0
    rows: list[list[str]] = []
    for chart_id, name, width, height in charts:
        if used + width > 12 and row:
            rows.append(row)
            row, used = [], 0
        key = f"CHART-{chart_id}"
        position[key] = {
            "type": "CHART",
            "id": key,
            "children": [],
            "meta": {"chartId": chart_id, "width": width, "height": height, "sliceName": name},
        }
        row.append(key)
        used += width
    if row:
        rows.append(row)
    for i, children in enumerate(rows, start=1):
        row_key = f"ROW-{i}"
        position[row_key] = {
            "type": "ROW",
            "id": row_key,
            "children": children,
            "parents": ["ROOT_ID", "GRID_ID"],
            "meta": {"background": "BACKGROUND_TRANSPARENT"},
        }
        position["GRID_ID"]["children"].append(row_key)
        for child in children:
            position[child]["parents"] = ["ROOT_ID", "GRID_ID", row_key]
    return position


def sync_product(api: Superset, product: DataProduct, databases: dict[str, int], fallback_org: str) -> list[str]:
    """Datasets, charts, dashboards and row security of one data product; the dashboard URLs."""
    config: DataProductConfig = product.config
    spec = yaml.safe_load((product.scenario_dir / str(config.bi_dashboards)).read_text())
    metrics = {m.name: m for m in config.metrics}
    datasets: dict[str, int] = {}
    urls = []

    def dataset_for(table: str, source: str, extra: dict[str, Any] | None = None) -> int:
        if table in datasets:
            return datasets[table]
        if extra is not None:
            schema, time_column = extra["schema"], extra.get("time_column")
            saved = [
                {"metric_name": m["name"], "verbose_name": m["label"], "expression": m["expression"]}
                for m in extra.get("metrics", [])
            ]
        else:
            declared = config.dataset(table)
            schema, time_column = layer_schema(product.slug, declared.layer), declared.time_column
            saved = [_saved_metric(m) for m in config.metrics if m.dataset == table]
        database = databases[MARTS_DB] if source == "marts" and MARTS_DB in databases else databases[WAREHOUSE_DB]
        found = api.find("dataset", "table_name", table)
        if found is not None and found.get("schema") == schema and found.get("database", {}).get("id") == database:
            dataset_id = int(found["id"])
        else:
            if found is not None:
                api.call("DELETE", f"/api/v1/dataset/{found['id']}")
            dataset_id = int(
                api.call("POST", "/api/v1/dataset/", {"database": database, "schema": schema, "table_name": table})[
                    "id"
                ]
            )
        api.call("PUT", f"/api/v1/dataset/{dataset_id}/refresh")
        current = api.call("GET", f"/api/v1/dataset/{dataset_id}")["result"]
        # Superset updates a metric by id: re-sending a governed metric that already exists
        # without its id is a duplicate. Metrics added by hand in Superset's UI are kept.
        existing = {m["metric_name"]: m for m in current.get("metrics", [])}
        saved = [
            {**metric, "id": existing[metric["metric_name"]]["id"]} if metric["metric_name"] in existing else metric
            for metric in saved
        ]
        keep = [
            {"id": m["id"], "metric_name": m["metric_name"], "expression": m["expression"]}
            for name, m in existing.items()
            if name not in {s["metric_name"] for s in saved} and name != "count"
        ]
        api.call(
            "PUT",
            f"/api/v1/dataset/{dataset_id}?override_columns=false",
            {
                "metrics": keep + saved,
                "main_dttm_col": time_column,
                "description": f"{product.slug} · {table} (governed metrics from scenario.yaml)",
            },
        )
        datasets[table] = dataset_id
        return dataset_id

    extras = {d["name"]: d for board in spec.get("dashboards", []) for d in board.get("datasets", [])}
    for board in spec.get("dashboards", []):
        source = board.get("source", "marts")
        placed = []
        for chart in board["charts"]:
            table = chart.get("dataset") or metrics[chart["metric"]].dataset
            dataset_id = dataset_for(table, source, extras.get(table))
            time_column = extras[table].get("time_column") if table in extras else config.dataset(table).time_column
            body = {
                "slice_name": chart["title"],
                "viz_type": VIZ[chart["viz"]],
                "datasource_id": dataset_id,
                "datasource_type": "table",
                "params": json.dumps(chart_params(chart, time_column)),
            }
            chart_id = api.upsert("chart", "slice_name", chart["title"], body)
            placed.append((chart_id, chart["title"], int(chart.get("width", 6)), CHART_HEIGHT[chart["viz"]]))
        metadata = {
            "refresh_frequency": int(board.get("refresh_seconds", 0)),
            "color_scheme": "supersetColors",
            "timed_refresh_immune_slices": [],
        }
        dashboard_id = api.upsert(
            "dashboard",
            "slug",
            board["slug"],
            {
                "dashboard_title": board["title"],
                "slug": board["slug"],
                "published": True,
                "position_json": json.dumps(layout(board["title"], placed)),
                "json_metadata": json.dumps(metadata),
            },
        )
        for chart_id, *_ in placed:
            api.call("PUT", f"/api/v1/chart/{chart_id}", {"dashboards": [dashboard_id]})
        urls.append(f"/superset/dashboard/{board['slug']}/")

    _row_security(api, product, spec, datasets, fallback_org)
    return urls


def _row_security(
    api: Superset, product: DataProduct, spec: dict[str, Any], datasets: dict[str, int], fallback_org: str
) -> None:
    tenant_rule = f"{product.slug}: demo tenant only"
    api.upsert(
        "rowlevelsecurity",
        "name",
        tenant_rule,
        {
            "name": tenant_rule,
            "filter_type": "Base",
            "tables": list(datasets.values()),
            "roles": [],
            "clause": f"org_id = '{fallback_org}'",
            "group_key": "tenant",
            "description": "Every chart reads the shared demo tenant (tenancy is the first predicate everywhere).",
        },
    )
    for rule in spec.get("row_security", []):
        role_id = _ensure_role(api, rule["role"])
        tables = [datasets[t] for t in rule["datasets"] if t in datasets]
        api.upsert(
            "rowlevelsecurity",
            "name",
            rule["role"],
            {
                "name": rule["role"],
                "filter_type": "Regular",
                "tables": tables,
                "roles": [role_id],
                "clause": rule["clause"],
                "group_key": "rows",
                "description": f"Row filter of {rule['role']}",
            },
        )
        user = rule.get("user")
        if user:
            _ensure_user(api, user, [role_id, _ensure_role(api, "Gamma")])


def _ensure_role(api: Superset, name: str) -> int:
    roles = api.call("GET", "/api/v1/security/roles/", q=rison({"page_size": 100})).get("result", [])
    for role in roles:
        if role["name"] == name:
            return int(role["id"])
    return int(api.call("POST", "/api/v1/security/roles/", {"name": name})["id"])


def _ensure_user(api: Superset, user: dict[str, str], role_ids: list[int]) -> None:
    users = api.call("GET", "/api/v1/security/users/", q=rison({"page_size": 100})).get("result", [])
    if any(u["username"] == user["username"] for u in users):
        return
    # A demo login for showing the row filter; its password is random and never printed
    # (an admin resets it in Superset's UI to log in as this user).
    api.call(
        "POST",
        "/api/v1/security/users/",
        {
            "username": user["username"],
            "first_name": user["first_name"],
            "last_name": user["last_name"],
            "email": f"{user['username']}@example.org",
            "active": True,
            "roles": role_ids,
            "password": secrets.token_urlsafe(24),
        },
    )


def ensure_databases(
    api: Superset,
    *,
    pg_user: str,
    pg_password: str,
    pg_database: str,
    clickhouse_user: str | None,
    clickhouse_password: str | None,
) -> dict[str, int]:
    """The Superset database connections: always the warehouse, plus ClickHouse when configured."""
    wanted = {WAREHOUSE_DB: f"postgresql+psycopg2://{pg_user}:{pg_password}@postgres:5432/{pg_database}"}
    if clickhouse_user:
        wanted[MARTS_DB] = f"clickhousedb://{clickhouse_user}:{clickhouse_password or ''}@clickhouse:8123/default"
    ids = {}
    for name, uri in wanted.items():
        ids[name] = api.upsert(
            "database",
            "database_name",
            name,
            {
                "database_name": name,
                "sqlalchemy_uri": uri,
                "expose_in_sqllab": True,
                "allow_run_async": False,
                "allow_dml": False,
                "allow_file_upload": False,
            },
        )
    return ids


def _clickhouse_marts_built(config: Any, slugs: list[str]) -> bool:
    """Whether the analytics tier has built every data product's gold database on ClickHouse."""
    if not config.CLICKHOUSE_HOST or not slugs:
        return False
    from data_transform.core import clickhouse

    try:
        password = config.CLICKHOUSE_PASSWORD.get_secret_value() if config.CLICKHOUSE_PASSWORD else None
        client = clickhouse.connect(config.CLICKHOUSE_HOST, config.CLICKHOUSE_USER, password)
        return all(str(client.command(f"EXISTS DATABASE `{slug}_gold`")).strip() == "1" for slug in slugs)
    except Exception:
        return False


def main() -> None:
    """Sync every data product that declares `bi_dashboards`."""
    configure_logger()
    from data_transform import get_env_config

    config = get_env_config()
    if not config.SUPERSET_URL or config.SUPERSET_ADMIN_PASSWORD is None:
        raise SystemExit("SUPERSET_URL and SUPERSET_ADMIN_PASSWORD are required for bi-sync")
    api = Superset(config.SUPERSET_URL, "admin", config.SUPERSET_ADMIN_PASSWORD.get_secret_value())
    products = load_products(Path(config.SCENARIOS_DIR), config.SCENARIOS or "")
    use_clickhouse = _clickhouse_marts_built(config, list(products))
    logger.info("Charts read the marts from {}", "ClickHouse" if use_clickhouse else "Postgres")
    databases = ensure_databases(
        api,
        pg_user=config.POSTGRES_USER,
        pg_password=config.POSTGRES_PASSWORD.get_secret_value(),
        pg_database=config.POSTGRES_DB,
        clickhouse_user=config.CLICKHOUSE_USER if use_clickhouse else None,
        clickhouse_password=config.CLICKHOUSE_PASSWORD.get_secret_value() if config.CLICKHOUSE_PASSWORD else None,
    )
    for product in products.values():
        if product.config.bi_dashboards:
            urls = sync_product(api, product, databases, config.DATA_FALLBACK_ORG_ID)
            logger.info("{}: {} dashboard(s) synced: {}", product.slug, len(urls), ", ".join(urls))


if __name__ == "__main__":
    main()
