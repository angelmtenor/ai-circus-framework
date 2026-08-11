"""Tests for load_normalized's per-tenant-with-fallback dataset loading."""

from __future__ import annotations

import pandas as pd
import pytest
from ai_circus_shared.tabular_ml import NORMALIZED_DATASET_KEY

from prediction.core.dataset import load_normalized


class FakeObjectStore:
    """In-memory stand-in for ai_circus_shared.storage.ObjectStore."""

    def __init__(self) -> None:
        """Start with an empty object map."""
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_dataframe(self, org_id: str, df: pd.DataFrame) -> None:
        """Store a DataFrame as parquet bytes under a tenant-scoped key."""
        self.objects[org_id, NORMALIZED_DATASET_KEY] = df.to_parquet()

    def get(self, org_id: str, path: str) -> bytes:
        """Retrieve previously stored bytes."""
        return self.objects[org_id, path]

    def exists(self, org_id: str, path: str) -> bool:
        """Mirror ObjectStore.exists() against the in-memory object map."""
        return (org_id, path) in self.objects


def test_load_normalized_loads_the_tenants_own_dataset() -> None:
    """A tenant with its own normalized dataset in MinIO gets exactly that."""
    store = FakeObjectStore()
    store.put_dataframe("org-1", pd.DataFrame({"a": [1, 2]}))
    store.put_dataframe("fallback-org", pd.DataFrame({"a": [99]}))

    df = load_normalized(store, "org-1", fallback_org_id="fallback-org")

    assert df["a"].tolist() == [1, 2]


def test_load_normalized_falls_back_to_shared_baseline_org_when_tenant_has_no_dataset() -> None:
    """A tenant with no dataset of its own (e.g. the admin/engineering-demo bypass
    orgs) gets the fallback org's dataset instead of a KeyError — this is what makes
    the Data tab actually work for any tenant besides the one training/etl ran for.
    """
    store = FakeObjectStore()
    store.put_dataframe("fallback-org", pd.DataFrame({"a": [99]}))

    df = load_normalized(store, "new-tenant-with-no-dataset", fallback_org_id="fallback-org")

    assert df["a"].tolist() == [99]
