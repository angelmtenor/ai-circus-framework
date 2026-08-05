"""Thin S3-compatible object storage client for MinIO.

Every dataset, trained model/explainer artifact, and uploaded document lives here,
namespaced per tenant — never on a service's local disk — so services stay stateless
and horizontally scalable, and the backend swaps 1:1 to real S3/GCS later.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import BinaryIO

import boto3
from botocore.client import Config as BotoConfig


@dataclass(frozen=True)
class ObjectStore:
    """A MinIO/S3 client bound to one bucket, with tenant-prefixed keys."""

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
        """Create a client and ensure the target bucket exists."""
        client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=BotoConfig(signature_version="s3v4"),
        )
        existing = {b["Name"] for b in client.list_buckets().get("Buckets", [])}
        if bucket not in existing:
            client.create_bucket(Bucket=bucket)
        return cls(bucket=bucket, _client=client)

    def _key(self, tenant_org_id: str, path: str) -> str:
        return f"tenant-{tenant_org_id}/{path.lstrip('/')}"

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
