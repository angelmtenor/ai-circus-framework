"""ai_circus_shared.readonly_sql — what a model may and may not do through a SQL tool."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ai_circus_shared.readonly_sql import ReadOnlyDatabase, SqlRefusedError


@pytest.fixture
def db(tmp_path: Path) -> ReadOnlyDatabase:
    path = tmp_path / "t.sqlite"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE readings (reservoir TEXT, day TEXT, pct REAL);
        CREATE TABLE secrets (token TEXT);
        INSERT INTO secrets VALUES ('do-not-read');
        """
    )
    con.executemany(
        "INSERT INTO readings VALUES (?, ?, ?)", [("a", f"2024-01-{d:02d}", 50.0 + d) for d in range(1, 21)]
    )
    con.commit()
    con.close()
    return ReadOnlyDatabase(path, {"readings"}, max_rows=5, timeout_s=0.3)


def test_a_select_returns_columns_rows_and_the_cap(db: ReadOnlyDatabase) -> None:
    out = db.query("SELECT day, pct FROM readings ORDER BY day")
    assert out["columns"] == ["day", "pct"] and out["row_count"] == 5 and out["truncated"] is True
    assert out["rows"][0] == ["2024-01-01", 51.0]
    small = db.query("SELECT count(*) AS n FROM readings WHERE pct > 64")
    assert small == {"columns": ["n"], "rows": [[6]], "row_count": 1, "truncated": False}


def test_aggregates_ctes_and_joins_work(db: ReadOnlyDatabase) -> None:
    sql = "WITH w AS (SELECT * FROM readings WHERE pct > 60) SELECT max(pct) FROM w"
    assert db.query(sql)["rows"] == [[70.0]]


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO readings VALUES ('x', 'y', 1)",
        "DELETE FROM readings",
        "DROP TABLE readings",
        "UPDATE readings SET pct = 0",
        "PRAGMA table_info(readings)",
        "ATTACH DATABASE ':memory:' AS m",
        "SELECT 1; SELECT 2",
        "SELECT * FROM readings; DROP TABLE readings",
        "",
        "   ;  ",
    ],
)
def test_anything_but_one_select_is_refused(db: ReadOnlyDatabase, sql: str) -> None:
    with pytest.raises(SqlRefusedError):
        db.query(sql)


def test_tables_outside_the_allow_list_are_unreadable_however_they_are_reached(db: ReadOnlyDatabase) -> None:
    for sql in (
        "SELECT * FROM secrets",
        "SELECT name FROM sqlite_master",
        "SELECT (SELECT token FROM secrets) FROM readings",
        "WITH s AS (SELECT token FROM secrets) SELECT * FROM s",
        "SELECT * FROM readings JOIN secrets",
        "WITH secrets AS (SELECT token FROM secrets) SELECT * FROM secrets",  # a CTE cannot borrow a hidden name
        "WITH RECURSIVE sqlite_master(x) AS (SELECT 1) SELECT * FROM sqlite_master",
    ):
        with pytest.raises(SqlRefusedError, match="not available"):
            db.query(sql)


def test_dangerous_functions_are_refused(db: ReadOnlyDatabase) -> None:
    with pytest.raises(SqlRefusedError):
        db.query("SELECT load_extension('x')")


def test_a_runaway_query_is_stopped(db: ReadOnlyDatabase) -> None:
    with pytest.raises(SqlRefusedError, match="took longer"):
        db.query("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT count(*) FROM c")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT randomblob(1000000000)",  # one allocation the time limit could not stop
        "SELECT zeroblob(500000000)",
        "SELECT replace(hex(zeroblob(40000)), '0', '0000000000')",
        "SELECT length(group_concat(hex(randomblob(50000)))) FROM readings",
    ],
)
def test_a_query_cannot_build_a_huge_value(db: ReadOnlyDatabase, sql: str) -> None:
    with pytest.raises(SqlRefusedError, match="too large"):
        db.query(sql)


def test_a_result_past_the_size_cap_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "w.sqlite"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (v TEXT)")
    con.executemany("INSERT INTO t VALUES (?)", [("x" * 90_000,) for _ in range(20)])
    con.commit()
    con.close()
    wide = ReadOnlyDatabase(path, {"t"}, max_rows=50)
    with pytest.raises(SqlRefusedError, match="too large"):
        wide.query("SELECT v FROM t")
    assert wide.query("SELECT length(v) FROM t")["row_count"] == 20


def test_a_semicolon_inside_a_string_is_fine(db: ReadOnlyDatabase) -> None:
    assert db.query("SELECT ';' AS s")["rows"] == [[";"]]


def test_the_file_itself_is_read_only(db: ReadOnlyDatabase) -> None:
    con = db._connect()
    with pytest.raises(sqlite3.OperationalError):
        con.execute("INSERT INTO readings VALUES ('x', 'y', 1)")
    con.close()


def test_a_gzipped_database_is_unpacked_once_and_reused(db: ReadOnlyDatabase, tmp_path: Path) -> None:
    import gzip
    import shutil

    from ai_circus_shared.readonly_sql import unpack

    gz = tmp_path / "pack" / "t.sqlite.gz"
    gz.parent.mkdir()
    with db.path.open("rb") as src, gzip.open(gz, "wb") as dst:
        shutil.copyfileobj(src, dst)
    first = unpack(gz, tmp_path / "cache")
    assert first.name == "t.sqlite" and ReadOnlyDatabase(first, {"readings"}).query("SELECT count(*) FROM readings")[
        "rows"
    ] == [[20]]
    stamp = first.stat().st_mtime_ns
    assert unpack(gz, tmp_path / "cache") == first and first.stat().st_mtime_ns == stamp  # reused, not rewritten


def test_tables_lists_only_the_allowed_ones_and_their_columns(db: ReadOnlyDatabase) -> None:
    assert db.tables() == {"readings": ["reservoir", "day", "pct"]}
    with pytest.raises(ValueError):
        ReadOnlyDatabase(db.path, set())
