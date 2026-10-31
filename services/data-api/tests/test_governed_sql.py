"""The SQL door must never see another tenant's rows, write, or escape the session."""

from __future__ import annotations

import pytest
from ai_circus_shared.data_policy import DataAccess
from ai_circus_shared.data_product import RoleAccess

from data_api.core.governed_sql import GovernedSqlError, rewrite, run
from tests.conftest_products import access_as, retail
from tests.fakes import FakeWarehouse


def test_every_dataset_reference_is_wrapped_in_a_tenant_filter() -> None:
    sql = rewrite(
        "SELECT store_id, sum(revenue_cents) FROM mart_sales_daily d JOIN mart_omnichannel o "
        "ON o.store_id = d.store_id GROUP BY 1",
        retail(),
        access_as("retail_analyst"),
        "postgres",
    )
    assert sql.count("org_id = 'acme'") == 2
    assert '"surf_retail_gold"."mart_sales_daily"' in sql
    assert '"surf_retail_gold"."mart_omnichannel"' in sql


def test_ctes_and_subqueries_are_rewritten_too() -> None:
    sql = rewrite(
        "WITH t AS (SELECT * FROM fct_sales_line) SELECT count(*) FROM t WHERE doc_id IN "
        "(SELECT doc_id FROM fct_sales_line)",
        retail(),
        access_as("retail_analyst"),
        "postgres",
    )
    assert sql.count("org_id = 'acme'") == 2


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("SELECT 1; SELECT 2", "Exactly one statement"),
        ("DELETE FROM mart_sales_daily", "Only SELECT"),
        ("SELECT * INTO x FROM mart_sales_daily", "read-only"),
        ("SELECT * FROM mart_sales_daily FOR UPDATE", "read-only"),
        ("SELECT set_config('x', 'y', true)", "not allowed"),
        ("SELECT pg_read_file('/etc/passwd')", "not allowed"),
        ("SELECT * FROM surf_retail.bronze_pos_receipts", "not allowed dataset"),
        ("SELECT * FROM surf_retail_gold.mart_sales_daily", "not allowed dataset"),
        ("SELECT * FROM pg_catalog.pg_tables", "not allowed dataset"),
        ("SELECT * FROM ops_quarantine", "not allowed dataset"),
    ],
)
def test_unsafe_queries_are_refused(sql: str, message: str) -> None:
    with pytest.raises(GovernedSqlError, match=message):
        rewrite(sql, retail(), access_as("retail_admin"), "postgres")


def test_hidden_columns_cannot_be_selected() -> None:
    with pytest.raises(GovernedSqlError, match="customer_key"):
        rewrite(
            "SELECT customer_key FROM fct_sales_line",
            retail(),
            access_as("retail_store_manager", values=("S012",)),
            "postgres",
        )


def test_clickhouse_dialect_rewrite() -> None:
    sql = rewrite("SELECT count() FROM mart_sales_daily", retail(), access_as("retail_analyst"), "clickhouse")
    assert "`surf_retail_gold`.`mart_sales_daily`" in sql or '"surf_retail_gold"' in sql
    assert "org_id = 'acme'" in sql


def test_run_requires_sql_role_and_caps_rows() -> None:
    from ai_circus_shared.data_policy import AccessDeniedError

    warehouse = FakeWarehouse(lambda _sql, _p: [{"n": i, "customer_key": "x"} for i in range(600)])
    with pytest.raises(AccessDeniedError):
        run(warehouse, retail(), access_as("retail_store_manager", values=("S012",)), "SELECT 1")  # type: ignore[arg-type]
    result = run(warehouse, retail(), access_as("retail_analyst"), "SELECT 1 AS n")  # type: ignore[arg-type]
    assert result["truncated"] is True and len(result["rows"]) == 500


def _analyst_hiding(*hidden: str) -> DataAccess:
    """A role that may run SQL over every row but has `hidden` columns masked."""
    from dataclasses import replace

    base = access_as("retail_analyst")
    return replace(base, access=RoleAccess(label="masked analyst", sql=True, hidden=list(hidden)))


_SALES_COLUMNS = ["org_id", "doc_id", "store_id", "customer_key", "loyalty_id", "revenue_cents"]


def test_hidden_column_check_is_case_insensitive() -> None:
    # Postgres folds an unquoted identifier to lower case, so `CUSTOMER_KEY` reads customer_key.
    with pytest.raises(GovernedSqlError, match="CUSTOMER_KEY"):
        rewrite(
            "SELECT CUSTOMER_KEY AS c FROM fct_sales_line",
            retail(),
            _analyst_hiding("customer_key"),
            "postgres",
        )


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM fct_sales_line",
        "SELECT to_jsonb(t) AS x FROM fct_sales_line t",
        "SELECT row_to_json(t) AS x FROM fct_sales_line t",
        "SELECT t::text AS x FROM fct_sales_line t",
    ],
)
def test_hidden_columns_are_absent_from_the_wrapped_subquery(sql: str) -> None:
    """Whole-row spellings never name a hidden column, so the subquery must not carry it."""
    rewritten = rewrite(
        sql, retail(), _analyst_hiding("customer_key", "loyalty_id"), "postgres", columns_of=lambda _ds: _SALES_COLUMNS
    )
    assert "customer_key" not in rewritten and "loyalty_id" not in rewritten
    assert '"revenue_cents"' in rewritten and "org_id = 'acme'" in rewritten


def test_run_projects_only_visible_columns() -> None:
    seen: list[str] = []

    def script(sql: str, _params: dict) -> list[dict]:  # type: ignore[type-arg]
        seen.append(sql)
        return [{"n": 1}]

    warehouse = FakeWarehouse(script)
    warehouse._columns = _SALES_COLUMNS
    run(warehouse, retail(), _analyst_hiding("customer_key"), "SELECT * FROM fct_sales_line")  # type: ignore[arg-type]
    assert "customer_key" not in seen[-1] and '"doc_id"' in seen[-1]
