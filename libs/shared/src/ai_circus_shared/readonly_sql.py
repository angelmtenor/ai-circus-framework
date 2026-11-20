"""Read-only SQL over a small SQLite file, for an assistant that must answer ad-hoc questions without being able to
change, escape from or exhaust anything.

The guarantees are enforced by SQLite itself, not by parsing the text:

- the file is opened read-only (`mode=ro`) with `query_only` on;
- an *authorizer* allows only `SELECT` and reads of the allow-listed tables and views (columns included), plus plain
  SQL functions: no `ATTACH`, no `PRAGMA`, no extension loading, no writes, no other table, no temp objects;
- one statement per call, a row cap (`truncated` says when it bit) and a wall-clock limit;
- bounded memory: SQLite's own limits cap any one value (`randomblob(1e9)`, a `replace` that multiplies a string, a
  long `group_concat`) and the number of result columns, and the whole result is refused past a size cap, because
  the time limit cannot stop one large allocation;
- results are plain JSON-able values.

Stdlib only. A tool built on it never receives a tenant or a user from the model: the caller decides which file and
which tables a request may see.
"""

from __future__ import annotations

import gzip
import os
import re
import shutil
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

DEFAULT_MAX_ROWS = 200
DEFAULT_TIMEOUT_S = 3.0
MAX_SQL_CHARS = 4_000
MAX_VALUE_BYTES = 100_000  # SQLITE_LIMIT_LENGTH: the largest string, blob or row SQLite will build
MAX_COLUMNS = 100  # SQLITE_LIMIT_COLUMN
MAX_RESULT_CHARS = 1_000_000  # the whole result, as text: also what a model would have to read
_PROGRESS_EVERY = 10_000  # SQLite virtual-machine instructions between time checks
_SQLITE_RECURSIVE = 33  # the action code of a recursive CTE; not exposed as a constant by every Python build
_DENIED_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit"}
_SYSTEM_TABLES = {"sqlite_master", "sqlite_schema", "sqlite_temp_master", "sqlite_temp_schema", "sqlite_sequence"}
# `WITH [RECURSIVE] name [(cols)] AS (` and the `, name [(cols)] AS (` that follow it. Over-matching text inside a
# string literal only adds a name that is then checked against the real tables, so it can refuse, never allow.
_CTE = re.compile(
    r'(?is)(?:\bwith(?:\s+recursive)?|,)\s*"?(\w+)"?\s*(?:\([^)]*\))?\s*\bas\s*(?:not\s+materialized\s*|materialized\s*)?\('
)


def unpack(gz_path: Path, directory: Path) -> Path:
    """Unpack `x.sqlite.gz` into `directory` once and return the file; a later call reuses it while the archive is
    not newer. The write is atomic (a temp name renamed into place), so two workers starting together are safe."""
    target = directory / gz_path.name.removesuffix(".gz")
    if target.exists() and target.stat().st_mtime >= gz_path.stat().st_mtime:
        return target
    directory.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(f".{os.getpid()}.tmp")
    with gzip.open(gz_path, "rb") as src, tmp.open("wb") as dst:
        shutil.copyfileobj(src, dst)
    tmp.replace(target)
    return target


class SqlRefusedError(ValueError):
    """The statement is not allowed (or could not run); the message is safe to show to the model."""


class ReadOnlyDatabase:
    """A read-only, allow-listed view of one SQLite file."""

    def __init__(
        self,
        path: Path,
        allowed_tables: set[str],
        *,
        max_rows: int = DEFAULT_MAX_ROWS,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        if not allowed_tables:
            raise ValueError("allowed_tables must not be empty")
        self.path = path
        self.allowed = {t.lower() for t in allowed_tables}
        self.max_rows = max_rows
        self.timeout_s = timeout_s

    def tables(self) -> dict[str, list[str]]:
        """The allowed tables and their columns, for a prompt or a tool description."""
        with closing(self._connect()) as con:
            out: dict[str, list[str]] = {}
            for name in sorted(self.allowed):
                rows = con.execute(f'PRAGMA table_info("{name}")').fetchall()  # trusted: our own allow-list
                if rows:
                    out[name] = [r[1] for r in rows]
            return out

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True, check_same_thread=False)
        con.execute("PRAGMA query_only = ON")
        con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_VALUE_BYTES)
        con.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, MAX_COLUMNS)
        return con

    def _authorizer_for(self, ctes: set[str]):
        """An authorizer that also lets a statement read the names of its own CTEs (a recursive one reads itself)."""
        readable = self.allowed | ctes

        def authorize(action: int, arg1: str | None, arg2: str | None, db: str | None, source: str | None) -> int:
            if action == sqlite3.SQLITE_SELECT:
                return sqlite3.SQLITE_OK
            if action == sqlite3.SQLITE_READ:
                return sqlite3.SQLITE_OK if (arg1 or "").lower() in readable else sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_FUNCTION:
                return sqlite3.SQLITE_DENY if (arg2 or "").lower() in _DENIED_FUNCTIONS else sqlite3.SQLITE_OK
            if action == _SQLITE_RECURSIVE:
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY

        return authorize

    def _cte_names(self, text: str, con: sqlite3.Connection) -> set[str]:
        """The CTE names a statement defines. One that shares its name with a real table the caller may not read is
        refused: it could otherwise read that table through its own definition."""
        names = {m.lower() for m in _CTE.findall(text)}
        real = {r[0].lower() for r in con.execute("SELECT name FROM sqlite_master").fetchall()} | _SYSTEM_TABLES
        hidden = names & (real - self.allowed)
        if hidden:
            raise SqlRefusedError(f"That statement reads something that is not available ({sorted(hidden)[0]}).")
        return names

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any]:
        """Run one SELECT. Returns {"columns": [...], "rows": [[...]], "row_count": n, "truncated": bool}."""
        text = sql.strip().rstrip(";").strip()
        if not text or len(text) > MAX_SQL_CHARS:
            raise SqlRefusedError(f"Send one SELECT of at most {MAX_SQL_CHARS} characters.")
        if not re.match(r"(?is)^(select|with)\b", text) or not sqlite3.complete_statement(text + ";"):
            raise SqlRefusedError("Only a single SELECT statement is allowed.")
        if ";" in re.sub(r"'(?:[^']|'')*'", "", text):
            raise SqlRefusedError("Only one statement per call.")
        deadline = time.monotonic() + self.timeout_s
        con = self._connect()
        try:
            con.set_authorizer(self._authorizer_for(self._cte_names(text, con)))
            con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, _PROGRESS_EVERY)
            cursor = con.execute(text, params)
            columns = [d[0] for d in cursor.description or []]
            rows: list[tuple[Any, ...]] = []
            size = 0
            for row in cursor:
                size += sum(len(str(v)) for v in row)
                if size > MAX_RESULT_CHARS:
                    raise SqlRefusedError("The result is too large to return: select fewer or shorter columns.")
                rows.append(row)
                if len(rows) > self.max_rows:
                    break
        except sqlite3.DatabaseError as exc:
            message = str(exc)
            if "authoriz" in message or "prohibited" in message:
                raise SqlRefusedError(f"That statement reads something that is not available ({message}).") from exc
            if "interrupted" in message:
                raise SqlRefusedError(f"The query took longer than {self.timeout_s:g} s: narrow it.") from exc
            if "too big" in message or "too many columns" in message:
                raise SqlRefusedError("The query builds a value or a row that is too large: narrow it.") from exc
            raise SqlRefusedError(f"SQL error: {message}") from exc
        finally:
            con.close()
        truncated = len(rows) > self.max_rows
        rows = rows[: self.max_rows]
        return {"columns": columns, "rows": [list(r) for r in rows], "row_count": len(rows), "truncated": truncated}
