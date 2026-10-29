"""
- Title:    Governed SQL — one read-only SELECT, rewritten so it can only see the caller's rows
- Author:   Angel Martinez-Tenor

The "SQL door" of the data product (docs/adr/0007): analysts write ordinary SQL against the
dataset *names* the scenario declares (`SELECT store_id, sum(revenue_cents) FROM
mart_sales_daily GROUP BY 1`). Before it runs, sqlglot parses it and:

1. accepts exactly one SELECT (UNIONs and CTEs included) — no DML/DDL, no SELECT INTO, no
   row locks;
2. accepts only silver/gold datasets the role's layers allow (or the query's own CTEs), and
   no column the role may not see;
3. rejects session-escaping functions (`set_config`, `pg_*`, `dblink`, file access…);
4. replaces every dataset reference with `(SELECT * FROM <schema>.<table> WHERE org_id =
   '<org>') AS <alias>` — the tenant filter is part of the query tree, not a convention.

It then runs in a read-only transaction with a statement timeout and a row cap; hidden columns
are dropped from the result.
"""

from __future__ import annotations

import re
from typing import Any

import sqlglot
from ai_circus_shared.data_policy import DataAccess
from ai_circus_shared.data_product import layer_schema
from sqlglot import exp

from data_api.core.products import DataProduct
from data_api.core.warehouse import Warehouse

MAX_SQL_LENGTH = 5000
MAX_RESULT_ROWS = 500
_DENIED_FUNCTION = re.compile(
    r"^(set_config|current_setting|pg_.*|dblink.*|lo_.*|query_to_xml.*|table_to_xml.*|.*_file|copy|"
    r"url|s3|file|remote.*|cluster.*|mysql|postgresql|input|executable|jdbc|odbc|hdfs|azure.*|gcs|iceberg.*|"
    r"system\..*|sleep.*|currval|nextval|setval|txid_.*|.*advisory.*)$",
    re.IGNORECASE,
)


class GovernedSqlError(ValueError):
    """A query the SQL door refuses (with the reason shown to the user)."""

    pass


def _function_name(node: Any) -> str:  # an exp.Func (sqlglot's stubs don't relate it to Expression)
    if isinstance(node, exp.Anonymous):
        return str(node.this)
    return node.sql_name() if hasattr(node, "sql_name") else type(node).__name__


def rewrite(sql: str, product: DataProduct, access: DataAccess, dialect: str) -> str:
    """Validate one SELECT and wrap every dataset reference in its tenant filter (see module docstring)."""
    if len(sql) > MAX_SQL_LENGTH:
        raise GovernedSqlError(f"Query longer than {MAX_SQL_LENGTH} characters.")
    read = "postgres" if dialect == "postgres" else "clickhouse"
    try:
        statements = [s for s in sqlglot.parse(sql, read=read) if s is not None]
    except sqlglot.errors.ParseError as exc:
        raise GovernedSqlError(f"Could not parse the query: {str(exc).splitlines()[0]}") from exc
    if len(statements) != 1:
        raise GovernedSqlError("Exactly one statement is allowed.")
    tree = statements[0]
    if not isinstance(tree, exp.Query):
        raise GovernedSqlError("Only SELECT queries are allowed.")
    if tree.find(exp.Into, exp.Lock, exp.Insert, exp.Update, exp.Delete, exp.Command, exp.Create, exp.Drop):
        raise GovernedSqlError("Only plain read-only SELECT queries are allowed.")
    for func in tree.find_all(exp.Func):
        name = _function_name(func)
        if _DENIED_FUNCTION.match(name):
            raise GovernedSqlError(f"Function {name!r} is not allowed.")
    # Curated layers only: ops tables carry raw payloads (with fields a role may not see).
    allowed = {
        d.name: d for d in product.config.datasets if d.layer in access.access.layers and d.layer in ("silver", "gold")
    }
    for column in tree.find_all(exp.Column):
        if column.name in access.hidden:
            raise GovernedSqlError(f"Column {column.name!r} is not available to role {access.role!r}.")
    ctes = {cte.alias_or_name for cte in tree.find_all(exp.CTE)}
    for table in list(tree.find_all(exp.Table)):
        name = table.name
        if table.args.get("db") is None and name in ctes:
            continue
        dataset = allowed.get(name)
        if dataset is None or table.args.get("db") is not None or table.args.get("catalog") is not None:
            raise GovernedSqlError(
                f"Unknown or not allowed dataset {table.sql(dialect=read)!r}; available: {sorted(allowed)}."
            )
        inner = (
            exp
            .select("*")
            .from_(
                exp.Table(
                    this=exp.to_identifier(name, quoted=True),
                    db=exp.to_identifier(layer_schema(product.slug, dataset.layer), quoted=True),
                )
            )
            .where(exp.EQ(this=exp.column("org_id"), expression=exp.Literal.string(access.org_id)))
        )
        alias = table.alias or name
        table.replace(exp.Subquery(this=inner, alias=exp.TableAlias(this=exp.to_identifier(alias))))
    return tree.sql(dialect=read)


def run(warehouse: Warehouse, product: DataProduct, access: DataAccess, sql: str) -> dict[str, Any]:
    """Rewrite, run read-only with a timeout and row cap, and drop hidden columns from the result."""
    access.require_sql()
    dialect = warehouse.marts_engine
    rewritten = rewrite(sql, product, access, dialect)
    q = warehouse.query("gold")
    rows = warehouse.rows(q, f"SELECT * FROM ({rewritten}) AS governed LIMIT {MAX_RESULT_ROWS + 1}", timeout_ms=10_000)
    truncated = len(rows) > MAX_RESULT_ROWS
    rows = [{k: v for k, v in r.items() if k not in access.hidden} for r in rows[:MAX_RESULT_ROWS]]
    return {
        "columns": list(rows[0]) if rows else [],
        "rows": rows,
        "truncated": truncated,
        "engine": dialect,
        "executed_sql": rewritten,
    }
