"""Tests for ObjectStore's tenant-scoped key building — the one place a future caller
passing user-controlled `tenant_org_id`/`path` input would need a defense-in-depth
guard against escaping the intended tenant prefix.
"""

from __future__ import annotations

import pytest
from botocore.exceptions import ClientError

from ai_circus_shared import storage
from ai_circus_shared.storage import ObjectStore


def _store() -> ObjectStore:
    # `_key()` is pure (no I/O), so a real boto3 client isn't needed to exercise it.
    return ObjectStore(bucket="test-bucket", _client=None)


def test_key_is_tenant_prefixed() -> None:
    assert _store()._key("org-1", "model.joblib") == "tenant-org-1/model.joblib"


def test_key_strips_a_leading_slash() -> None:
    assert _store()._key("org-1", "/model.joblib") == "tenant-org-1/model.joblib"


@pytest.mark.parametrize("path", ["../secret", "a/../../etc/passwd", "..", "a/..", "../"])
def test_key_rejects_parent_directory_segments(path: str) -> None:
    with pytest.raises(ValueError, match="path segments"):
        _store()._key("org-1", path)


@pytest.mark.parametrize("org_id", ["../org", "org/1", "org id", "org;drop", ""])
def test_key_rejects_invalid_tenant_org_id(org_id: str) -> None:
    with pytest.raises(ValueError, match="tenant_org_id"):
        _store()._key(org_id, "model.joblib")


@pytest.mark.parametrize("org_id", ["admin", "engineering-demo", "org_1", "ORG-123"])
def test_key_accepts_expected_org_id_shapes(org_id: str) -> None:
    assert _store()._key(org_id, "x") == f"tenant-{org_id}/x"


def test_connect_shares_one_client_and_lists_buckets_once(monkeypatch) -> None:
    """Binding N buckets on one endpoint builds one boto3 client; buckets already seen are
    never re-listed (only a not-yet-known bucket triggers a fresh listing)."""
    from ai_circus_shared import storage as storage_module

    created: list[object] = []
    listed: list[int] = []

    class _FakeClient:
        def list_buckets(self) -> dict[str, list[dict[str, str]]]:
            listed.append(1)
            return {"Buckets": [{"Name": "churn"}]}

        def create_bucket(self, Bucket: str) -> None:  # noqa: N803 - boto3's own kwarg name
            created.append(Bucket)

    fake = _FakeClient()
    storage_module._s3_client.cache_clear()
    storage_module._known_buckets.clear()
    monkeypatch.setattr(storage_module.boto3, "client", lambda *_a, **_kw: fake)
    kwargs = {"endpoint_url": "http://s3", "access_key": "a", "secret_key": "b"}
    stores = [storage_module.ObjectStore.connect(bucket=b, **kwargs) for b in ("churn", "mpm", "mpm")]
    assert all(store._client is fake for store in stores)
    assert listed == [1, 1]  # churn (known after the first list), mpm (missing -> created), mpm (cached)
    assert created == ["mpm"]
    storage_module._s3_client.cache_clear()
    storage_module._known_buckets.clear()


def test_connect_without_create_bucket_never_lists_or_creates(monkeypatch) -> None:
    """A read-only caller (the governance inventory) must not create buckets as a side effect."""
    from ai_circus_shared import storage as storage_module

    calls: list[str] = []

    class _FakeClient:
        def list_buckets(self) -> dict[str, list]:
            calls.append("list")
            return {"Buckets": []}

        def create_bucket(self, Bucket: str) -> None:  # noqa: N803 - boto3's own kwarg name
            calls.append(f"create:{Bucket}")

    storage_module._s3_client.cache_clear()
    storage_module._known_buckets.clear()
    monkeypatch.setattr(storage_module.boto3, "client", lambda *_a, **_kw: _FakeClient())
    store = storage_module.ObjectStore.connect(
        bucket="never-trained", endpoint_url="http://s3", access_key="a", secret_key="b", create_bucket=False
    )
    assert store.bucket == "never-trained" and calls == []
    storage_module._s3_client.cache_clear()
    storage_module._known_buckets.clear()


@pytest.mark.parametrize("org_id", ["org-1\n", "org-1\n\n"])
def test_key_rejects_a_trailing_newline(org_id: str) -> None:
    """`$` matches before a trailing newline; the guard must not."""
    with pytest.raises(ValueError, match="tenant_org_id"):
        _store()._key(org_id, "x")


class _FakeS3:
    """Just enough of a boto3 client for exists()/_ensure_bucket()."""

    class exceptions:  # noqa: N801 - mirrors boto3's client.exceptions namespace
        ClientError = ClientError

    def __init__(self, code: str | None = None, create_code: str | None = None) -> None:
        self.code, self.create_code = code, create_code

    def head_object(self, **_kw: object) -> None:
        if self.code:
            raise ClientError({"Error": {"Code": self.code}}, "HeadObject")

    def list_buckets(self) -> dict:  # type: ignore[type-arg]
        return {"Buckets": []}

    def create_bucket(self, **_kw: object) -> None:
        if self.create_code:
            raise ClientError({"Error": {"Code": self.create_code}}, "CreateBucket")


@pytest.mark.parametrize(
    ("code", "expected"), [(None, True), ("404", False), ("NoSuchKey", False), ("NotFound", False)]
)
def test_exists_distinguishes_present_from_absent(code: str | None, expected: bool) -> None:
    assert ObjectStore(bucket="b", _client=_FakeS3(code)).exists("org-1", "x") is expected


@pytest.mark.parametrize("code", ["403", "AccessDenied", "500", "ServiceUnavailable"])
def test_exists_does_not_hide_auth_or_server_errors(code: str) -> None:
    with pytest.raises(ClientError):
        ObjectStore(bucket="b", _client=_FakeS3(code)).exists("org-1", "x")


def test_losing_the_bucket_creation_race_is_not_an_error() -> None:
    storage._ensure_bucket(_FakeS3(create_code="BucketAlreadyOwnedByYou"), "race-bucket")
    with pytest.raises(ClientError):
        storage._ensure_bucket(_FakeS3(create_code="AccessDenied"), "denied-bucket")
