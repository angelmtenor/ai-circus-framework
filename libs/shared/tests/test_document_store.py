"""Tests for ai_circus_shared.document_store's DocumentStore — every method must
stay scoped by org_id, since this is what stands between one tenant's flexible-
schema records and another's.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from ai_circus_shared.document_store import Base, DocumentStore


@pytest.fixture
def store() -> Iterator[DocumentStore]:
    """A DocumentStore backed by a fresh in-memory SQLite database per test —
    exercises the same SQLAlchemy models/queries production uses against
    Postgres, without needing a real database for this shared library's own
    test suite.
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield DocumentStore(session)


def test_put_then_get_round_trips_content(store: DocumentStore) -> None:
    store.put("org-1", "sessions", "doc-1", {"turns": 3, "topic": "billing"})

    fetched = store.get("org-1", "sessions", "doc-1")

    assert fetched is not None
    assert fetched.content == {"turns": 3, "topic": "billing"}


def test_get_returns_none_for_unknown_doc_id(store: DocumentStore) -> None:
    assert store.get("org-1", "sessions", "does-not-exist") is None


def test_get_returns_none_for_another_orgs_document(store: DocumentStore) -> None:
    store.put("org-1", "sessions", "doc-1", {"a": 1})

    assert store.get("org-2", "sessions", "doc-1") is None


def test_get_returns_none_for_another_collection(store: DocumentStore) -> None:
    store.put("org-1", "sessions", "doc-1", {"a": 1})

    assert store.get("org-1", "other-collection", "doc-1") is None


def test_put_again_replaces_content_in_place(store: DocumentStore) -> None:
    store.put("org-1", "sessions", "doc-1", {"turns": 1})

    store.put("org-1", "sessions", "doc-1", {"turns": 2})

    assert store.get("org-1", "sessions", "doc-1").content == {"turns": 2}
    assert len(store.list("org-1", "sessions")) == 1


def test_list_is_scoped_to_org_and_collection(store: DocumentStore) -> None:
    store.put("org-1", "sessions", "mine", {"v": 1})
    store.put("org-2", "sessions", "other-org", {"v": 1})
    store.put("org-1", "other-collection", "other-collection-doc", {"v": 1})

    listed = store.list("org-1", "sessions")

    assert [d.doc_id for d in listed] == ["mine"]


def test_list_orders_most_recently_updated_first(store: DocumentStore) -> None:
    store.put("org-1", "sessions", "first", {"v": 1})
    store.put("org-1", "sessions", "second", {"v": 1})
    store.put("org-1", "sessions", "first", {"v": 2})  # bump first's updated_at

    listed = store.list("org-1", "sessions")

    assert [d.doc_id for d in listed] == ["first", "second"]


def test_delete_removes_the_document(store: DocumentStore) -> None:
    store.put("org-1", "sessions", "doc-1", {"a": 1})

    deleted = store.delete("org-1", "sessions", "doc-1")

    assert deleted is True
    assert store.get("org-1", "sessions", "doc-1") is None


def test_delete_owned_by_another_org_is_a_no_op(store: DocumentStore) -> None:
    store.put("org-1", "sessions", "doc-1", {"a": 1})

    deleted = store.delete("org-2", "sessions", "doc-1")

    assert deleted is False
    assert store.get("org-1", "sessions", "doc-1") is not None


def test_delete_unknown_doc_id_is_a_no_op(store: DocumentStore) -> None:
    assert store.delete("org-1", "sessions", "does-not-exist") is False


def test_content_can_be_a_nested_structure(store: DocumentStore) -> None:
    nested = {"messages": [{"role": "user", "text": "hi"}], "meta": {"tags": ["a", "b"]}}

    store.put("org-1", "sessions", "doc-1", nested)

    assert store.get("org-1", "sessions", "doc-1").content == nested
