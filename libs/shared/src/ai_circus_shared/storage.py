"""Thin S3-compatible object storage client for SeaweedFS.

Every dataset, trained model/explainer artifact, and uploaded document lives here,
namespaced per tenant — never on a service's local disk — so services stay stateless
and horizontally scalable, and the backend swaps 1:1 to real S3/GCS later.
"""

from __future__ import annotations

import io
import re
import threading
from dataclasses import dataclass
from functools import cache
from typing import Any, BinaryIO

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError, ConnectionClosedError, EndpointConnectionError, ReadTimeoutError

from ai_circus_shared.startup import wait_for

# Every call site in this codebase passes a validated `Identity.org_id` (never raw
# client input) and a fixed, config-derived `path` — but `_key()` has no guard of its
# own, so a future caller passing user-controlled input into either argument would
# have no defense-in-depth against building a key outside the intended tenant prefix.
_SAFE_ORG_ID = re.compile(r"^[A-Za-z0-9_-]+$")

# Buckets already confirmed to exist, per client — a service binding one ObjectStore per
# scenario (prediction: one per tabular_ml scenario) lists buckets once, not once each.
_known_buckets: dict[int, set[str]] = {}
_known_buckets_lock = threading.Lock()


@cache
def _s3_client(endpoint_url: str, access_key: str, secret_key: str, region: str) -> Any:
    """One boto3 client per endpoint/credential set, shared by every ObjectStore in the
    process. boto3 clients are thread-safe but slow (~100 ms) and heavy (several MB of
    loaded service model) to build — previously one per bound bucket.
    """
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        config=BotoConfig(signature_version="s3v4"),
    )


def _is_transient(exc: Exception) -> bool:
    """SeaweedFS still starting: refused/closed connection, or a 5xx from its S3 gateway."""
    if isinstance(exc, (EndpointConnectionError, ConnectionClosedError, ReadTimeoutError)):
        return True
    if isinstance(exc, ClientError):
        return int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)) >= 500
    return False


def _ensure_bucket(client: Any, bucket: str) -> None:
    with _known_buckets_lock:
        known = _known_buckets.get(id(client))
        if known is not None and bucket in known:
            return
    existing = wait_for(
        lambda: {b["Name"] for b in client.list_buckets().get("Buckets", [])},
        what="SeaweedFS (object store)",
        retryable=_is_transient,
    )
    if bucket not in existing:
        client.create_bucket(Bucket=bucket)
        existing.add(bucket)
    with _known_buckets_lock:
        _known_buckets.setdefault(id(client), set()).update(existing)


@dataclass(frozen=True)
class ObjectStore:
    """A SeaweedFS/S3 client bound to one bucket, with tenant-prefixed keys."""

    bucket: str
    _client: object

    @classmethod
    def connect(
        cls,
        *,
        bucket: str,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1",
    ) -> ObjectStore:
        """Bind the process-wide client for this endpoint to `bucket`, creating the bucket
        if needed — waiting for SeaweedFS first if it is still starting (see startup.py).
        """
        client = _s3_client(endpoint_url, access_key, secret_key, region)
        _ensure_bucket(client, bucket)
        return cls(bucket=bucket, _client=client)

    def _key(self, tenant_org_id: str, path: str) -> str:
        if not _SAFE_ORG_ID.match(tenant_org_id):
            raise ValueError(f"Invalid tenant_org_id {tenant_org_id!r}: must match {_SAFE_ORG_ID.pattern}.")
        normalized = path.lstrip("/")
        if any(segment == ".." for segment in normalized.split("/")):
            raise ValueError(f"Invalid path {path!r}: '..' path segments are not allowed.")
        return f"tenant-{tenant_org_id}/{normalized}"

    def put(self, tenant_org_id: str, path: str, data: bytes | BinaryIO) -> str:
        """Upload bytes/a file-like object under a tenant-scoped key; return the key."""
        key = self._key(tenant_org_id, path)
        body = io.BytesIO(data) if isinstance(data, bytes) else data
        self._client.upload_fileobj(body, self.bucket, key)
        return key

    def get(self, tenant_org_id: str, path: str) -> bytes:
        """Download an object's contents as bytes."""
        key = self._key(tenant_org_id, path)
        buffer = io.BytesIO()
        self._client.download_fileobj(self.bucket, key, buffer)
        return buffer.getvalue()

    def exists(self, tenant_org_id: str, path: str) -> bool:
        """Return whether an object exists at the given tenant-scoped path."""
        key = self._key(tenant_org_id, path)
        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
        except self._client.exceptions.ClientError:
            return False
        return True

    def list(self, tenant_org_id: str, prefix: str = "") -> list[str]:
        """List object keys (relative to the tenant prefix) under the given prefix."""
        full_prefix = self._key(tenant_org_id, prefix)
        paginator = self._client.get_paginator("list_objects_v2")
        tenant_root = f"tenant-{tenant_org_id}/"
        keys = []
        for page in paginator.paginate(Bucket=self.bucket, Prefix=full_prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"].removeprefix(tenant_root))
        return keys
