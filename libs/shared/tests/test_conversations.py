"""Tests for ai_circus_shared.conversations' ConversationStore — every method must
stay scoped by org_id/user_id, since this is what stands between one tenant/user
and another's chat history.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from ai_circus_shared.conversations import DEFAULT_TITLE, Base, ConversationStore


@pytest.fixture
def store() -> Iterator[ConversationStore]:
    """A ConversationStore backed by a fresh in-memory SQLite database per test —
    exercises the same SQLAlchemy models/queries production uses against Postgres,
    without needing a real database for this shared library's own test suite.
    """
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield ConversationStore(session)


def test_create_conversation_is_returned_by_list(store: ConversationStore) -> None:
    created = store.create_conversation("org-1", "user-1", "docs_rag", "New conversation")

    listed = store.list_conversations("org-1", "user-1", "docs_rag")

    assert [c.id for c in listed] == [created.id]
    assert listed[0].title == "New conversation"


def test_list_conversations_is_scoped_to_org_and_user(store: ConversationStore) -> None:
    store.create_conversation("org-1", "user-1", "docs_rag", "mine")
    store.create_conversation("org-2", "user-1", "docs_rag", "other org")
    store.create_conversation("org-1", "user-2", "docs_rag", "other user, same org")
    store.create_conversation("org-1", "user-1", "other_scenario", "other scenario")

    listed = store.list_conversations("org-1", "user-1", "docs_rag")

    assert [c.title for c in listed] == ["mine"]


def test_list_conversations_orders_most_recently_updated_first(store: ConversationStore) -> None:
    first = store.create_conversation("org-1", "user-1", "docs_rag", "first")
    second = store.create_conversation("org-1", "user-1", "docs_rag", "second")
    store.append_messages(first.id, "org-1", "user-1", [("user", "hello again")])

    listed = store.list_conversations("org-1", "user-1", "docs_rag")

    assert [c.id for c in listed] == [first.id, second.id]


def test_get_conversation_returns_none_for_another_org(store: ConversationStore) -> None:
    created = store.create_conversation("org-1", "user-1", "docs_rag", "mine")

    assert store.get_conversation(created.id, "org-2", "user-1") is None
    assert store.get_conversation(created.id, "org-1", "user-2") is None
    assert store.get_conversation(created.id, "org-1", "user-1") is not None


def test_delete_conversation_removes_it_and_its_messages(store: ConversationStore) -> None:
    created = store.create_conversation("org-1", "user-1", "docs_rag", "mine")
    store.append_messages(created.id, "org-1", "user-1", [("user", "hi"), ("assistant", "hello")])

    deleted = store.delete_conversation(created.id, "org-1", "user-1")

    assert deleted is True
    assert store.get_conversation(created.id, "org-1", "user-1") is None
    assert store.list_messages(created.id, "org-1", "user-1") == []


def test_delete_conversation_owned_by_another_org_is_a_no_op(store: ConversationStore) -> None:
    created = store.create_conversation("org-1", "user-1", "docs_rag", "mine")

    deleted = store.delete_conversation(created.id, "org-2", "user-1")

    assert deleted is False
    assert store.get_conversation(created.id, "org-1", "user-1") is not None


def test_delete_conversation_unknown_id_is_a_no_op(store: ConversationStore) -> None:
    assert store.delete_conversation("does-not-exist", "org-1", "user-1") is False


def test_append_and_list_messages_round_trips_in_order(store: ConversationStore) -> None:
    created = store.create_conversation("org-1", "user-1", "docs_rag", "mine")

    store.append_messages(created.id, "org-1", "user-1", [("user", "hello"), ("assistant", "hi there")])

    messages = store.list_messages(created.id, "org-1", "user-1")
    assert [(m.role, m.content) for m in messages] == [("user", "hello"), ("assistant", "hi there")]


def test_append_messages_content_can_be_a_content_block_list(store: ConversationStore) -> None:
    created = store.create_conversation("org-1", "user-1", "docs_rag", "mine")
    blocks = [{"type": "text", "text": "hi"}, {"type": "image", "source": {"type": "data", "value": "abc"}}]

    store.append_messages(created.id, "org-1", "user-1", [("user", blocks)])

    messages = store.list_messages(created.id, "org-1", "user-1")
    assert messages[0].content == blocks


def test_append_messages_renames_a_default_titled_conversation_from_the_first_user_turn(
    store: ConversationStore,
) -> None:
    created = store.create_conversation("org-1", "user-1", "docs_rag", DEFAULT_TITLE)

    store.append_messages(created.id, "org-1", "user-1", [("user", "What is the overdraft fee?")])

    renamed = store.get_conversation(created.id, "org-1", "user-1")
    assert renamed is not None
    assert renamed.title == "What is the overdraft fee?"


def test_append_messages_truncates_a_long_first_message_into_the_title(store: ConversationStore) -> None:
    created = store.create_conversation("org-1", "user-1", "docs_rag", DEFAULT_TITLE)
    long_text = "Can you explain in great detail how the overdraft fee schedule works for premium accounts?"

    store.append_messages(created.id, "org-1", "user-1", [("user", long_text)])

    renamed = store.get_conversation(created.id, "org-1", "user-1")
    assert renamed is not None
    assert renamed.title.endswith("…")
    assert len(renamed.title) <= 49


def test_append_messages_does_not_rename_a_conversation_with_a_custom_title(store: ConversationStore) -> None:
    created = store.create_conversation("org-1", "user-1", "docs_rag", "My custom title")

    store.append_messages(created.id, "org-1", "user-1", [("user", "hello")])

    renamed = store.get_conversation(created.id, "org-1", "user-1")
    assert renamed is not None
    assert renamed.title == "My custom title"


def test_append_messages_does_not_rename_on_a_later_turn(store: ConversationStore) -> None:
    created = store.create_conversation("org-1", "user-1", "docs_rag", DEFAULT_TITLE)
    store.append_messages(created.id, "org-1", "user-1", [("user", "first message")])

    store.append_messages(created.id, "org-1", "user-1", [("user", "second message")])

    renamed = store.get_conversation(created.id, "org-1", "user-1")
    assert renamed is not None
    assert renamed.title == "first message"


def test_append_messages_leaves_the_default_title_when_the_first_turn_has_no_user_text(
    store: ConversationStore,
) -> None:
    created = store.create_conversation("org-1", "user-1", "docs_rag", DEFAULT_TITLE)
    blocks = [{"type": "text", "text": "hi"}]

    store.append_messages(created.id, "org-1", "user-1", [("user", blocks)])

    renamed = store.get_conversation(created.id, "org-1", "user-1")
    assert renamed is not None
    assert renamed.title == DEFAULT_TITLE


def test_list_messages_for_another_orgs_conversation_returns_empty(store: ConversationStore) -> None:
    created = store.create_conversation("org-1", "user-1", "docs_rag", "mine")
    store.append_messages(created.id, "org-1", "user-1", [("user", "hello")])

    assert store.list_messages(created.id, "org-2", "user-1") == []


def test_append_messages_to_unknown_conversation_is_a_no_op(store: ConversationStore) -> None:
    store.append_messages("does-not-exist", "org-1", "user-1", [("user", "hello")])
    # No exception, and nothing to list under any scope.
    assert store.list_messages("does-not-exist", "org-1", "user-1") == []


def test_append_messages_with_no_turns_is_a_no_op(store: ConversationStore) -> None:
    created = store.create_conversation("org-1", "user-1", "docs_rag", "mine")

    store.append_messages(created.id, "org-1", "user-1", [])

    assert store.list_messages(created.id, "org-1", "user-1") == []
