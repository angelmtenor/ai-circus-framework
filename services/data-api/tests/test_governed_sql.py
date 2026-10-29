"""The SQL door must never see another tenant's rows, write, or escape the session."""

from __future__ import annotations

import pytest

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
