"""Tests for ai_circus_shared.cdc.parse_change — using real test_decoding
output lines captured live against a running Postgres (INSERT/UPDATE/DELETE
against ai_circus_shared.document_store's own `documents` table), not
hand-guessed fixtures. ensure_slot/poll_changes/slot_status are thin SQL
wrappers exercised against a real Postgres in the live docker-compose
verification instead (no sqlite/fake substitute for logical replication —
it's a real server-side feature, not something an in-memory engine can fake).
"""

from __future__ import annotations

from ai_circus_shared.cdc import parse_change

_REAL_INSERT_LINE = (
    "table public.documents: INSERT: org_id[character varying]:'admin' "
    "collection[character varying]:'cdc-test' doc_id[character varying]:'doc-1' "
    'content[json]:\'{"a": 1, "note": "first"}\' '
    "created_at[timestamp with time zone]:'2026-09-08 19:58:15.862279+00' "
    "updated_at[timestamp with time zone]:'2026-09-08 19:58:15.862287+00'"
)

_REAL_UPDATE_LINE = (
    "table public.documents: UPDATE: org_id[character varying]:'admin' "
    "collection[character varying]:'cdc-test' doc_id[character varying]:'doc-1' "
    "content[json]:'{\"a\": 2}' "
    "created_at[timestamp with time zone]:'2026-09-08 19:58:15.862279+00' "
    "updated_at[timestamp with time zone]:'2026-09-08 19:58:28.801882+00'"
)

_REAL_DELETE_LINE = (
    "table public.documents: DELETE: org_id[character varying]:'admin' "
    "collection[character varying]:'cdc-test' doc_id[character varying]:'doc-1'"
)


def test_parse_change_returns_none_for_begin_line() -> None:
    assert parse_change("BEGIN 1071") is None


def test_parse_change_returns_none_for_commit_line() -> None:
    assert parse_change("COMMIT 1071") is None


def test_parse_change_returns_none_for_unrecognized_line() -> None:
    assert parse_change("something else entirely") is None


def test_parse_change_parses_a_real_insert() -> None:
    event = parse_change(_REAL_INSERT_LINE)

    assert event is not None
    assert event.schema == "public"
    assert event.table == "documents"
    assert event.operation == "INSERT"
    assert event.columns == {
        "org_id": "admin",
        "collection": "cdc-test",
        "doc_id": "doc-1",
        "content": '{"a": 1, "note": "first"}',
        "created_at": "2026-09-08 19:58:15.862279+00",
        "updated_at": "2026-09-08 19:58:15.862287+00",
    }


def test_parse_change_parses_a_real_update() -> None:
    event = parse_change(_REAL_UPDATE_LINE)

    assert event is not None
    assert event.operation == "UPDATE"
    assert event.columns["content"] == '{"a": 2}'


def test_parse_change_parses_a_real_delete_key_only_columns() -> None:
    """DELETE only carries the table's REPLICA IDENTITY (primary key) columns
    by Postgres's own default — not the full deleted row.
    """
    event = parse_change(_REAL_DELETE_LINE)

    assert event is not None
    assert event.operation == "DELETE"
    assert event.columns == {"org_id": "admin", "collection": "cdc-test", "doc_id": "doc-1"}


def test_parse_change_unescapes_doubled_single_quotes() -> None:
    line = "table public.documents: INSERT: doc_id[character varying]:'it''s here'"

    event = parse_change(line)

    assert event is not None
    assert event.columns["doc_id"] == "it's here"


def test_parse_change_handles_a_bare_unquoted_value() -> None:
    line = "table public.some_table: INSERT: count[integer]:42"

    event = parse_change(line)

    assert event is not None
    assert event.columns["count"] == "42"


def test_to_dict_is_json_serializable_shape() -> None:
    event = parse_change(_REAL_INSERT_LINE)
    assert event is not None

    assert event.to_dict() == {
        "schema": "public",
        "table": "documents",
        "operation": "INSERT",
        "columns": event.columns,
    }
