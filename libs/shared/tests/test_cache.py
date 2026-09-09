"""Tests for ai_circus_shared.cache's TenantCache — key-prefixing is pure (no I/O)
and tested the same way ai_circus_shared.storage's ObjectStore._key is; get/set/
incr behavior is exercised against fakeredis, a real RESP-protocol server that
just happens to run in-process, rather than a mock of the redis-py client.
"""

from __future__ import annotations

from collections.abc import Iterator

import fakeredis
import pytest

from ai_circus_shared.cache import TenantCache


def _cache(client: object = None) -> TenantCache:
    return TenantCache(_client=client)


def test_key_is_tenant_prefixed() -> None:
    assert _cache()._key("org-1", "session:abc") == "tenant-org-1:session:abc"


@pytest.mark.parametrize("org_id", ["../org", "org/1", "org id", "org;drop", ""])
def test_key_rejects_invalid_tenant_org_id(org_id: str) -> None:
    with pytest.raises(ValueError, match="tenant_org_id"):
        _cache()._key(org_id, "k")


@pytest.mark.parametrize("org_id", ["admin", "engineering-demo", "org_1", "ORG-123"])
def test_key_accepts_expected_org_id_shapes(org_id: str) -> None:
    assert _cache()._key(org_id, "k") == f"tenant-{org_id}:k"


@pytest.fixture
def cache() -> Iterator[TenantCache]:
    yield TenantCache(_client=fakeredis.FakeStrictRedis(decode_responses=True))


def test_set_then_get_round_trips_value(cache: TenantCache) -> None:
    cache.set("org-1", "greeting", "hello")

    assert cache.get("org-1", "greeting") == "hello"


def test_get_returns_none_for_unknown_key(cache: TenantCache) -> None:
    assert cache.get("org-1", "does-not-exist") is None


def test_get_is_scoped_to_org(cache: TenantCache) -> None:
    cache.set("org-1", "greeting", "hello")

    assert cache.get("org-2", "greeting") is None


def test_delete_removes_the_key(cache: TenantCache) -> None:
    cache.set("org-1", "greeting", "hello")

    deleted = cache.delete("org-1", "greeting")

    assert deleted is True
    assert cache.get("org-1", "greeting") is None


def test_delete_unknown_key_returns_false(cache: TenantCache) -> None:
    assert cache.delete("org-1", "does-not-exist") is False


def test_incr_starts_at_one_and_increments(cache: TenantCache) -> None:
    assert cache.incr("org-1", "requests") == 1
    assert cache.incr("org-1", "requests") == 2


def test_incr_is_scoped_to_org(cache: TenantCache) -> None:
    cache.incr("org-1", "requests")

    assert cache.incr("org-2", "requests") == 1


def test_incr_applies_ttl_only_on_first_call(cache: TenantCache) -> None:
    cache.incr("org-1", "requests", ttl_seconds=60)
    ttl_after_first = cache._client.ttl(cache._key("org-1", "requests"))

    cache.incr("org-1", "requests", ttl_seconds=60)
    ttl_after_second = cache._client.ttl(cache._key("org-1", "requests"))

    assert ttl_after_first > 0
    assert ttl_after_second > 0


def test_incr_by_float_starts_at_amount_and_accumulates(cache: TenantCache) -> None:
    assert cache.incr_by_float("org-1", "spend", 0.15) == pytest.approx(0.15)
    assert cache.incr_by_float("org-1", "spend", 0.10) == pytest.approx(0.25)


def test_incr_by_float_is_scoped_to_org(cache: TenantCache) -> None:
    cache.incr_by_float("org-1", "spend", 1.0)

    assert cache.incr_by_float("org-2", "spend", 0.5) == pytest.approx(0.5)


def test_incr_by_float_applies_ttl_only_on_first_call(cache: TenantCache) -> None:
    cache.incr_by_float("org-1", "spend", 1.0, ttl_seconds=60)
    ttl_after_first = cache._client.ttl(cache._key("org-1", "spend"))

    cache.incr_by_float("org-1", "spend", 1.0, ttl_seconds=60)
    ttl_after_second = cache._client.ttl(cache._key("org-1", "spend"))

    assert ttl_after_first > 0
    assert ttl_after_second > 0
