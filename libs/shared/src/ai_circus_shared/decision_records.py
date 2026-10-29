"""
- Title:    Decision records for non-LLM predictions (record-keeping applies to ML, not only LLMs)
- Author:   Angel Martinez-Tenor

Every scoring request served by `prediction` (tabular models) and `dl-inference` (deep learning)
leaves one content-free **decision record**: which AI system and tenant, which model and which
version of it, what came out (an outcome histogram and the score range), whether an explanation
was available, and a `trace_id` that ties it to the request. It carries no input features, no
free text and no identity — see `PredictionRecordData`.

Two destinations, both off by default and both best-effort (a failure is logged, never raised —
scoring must not depend on the evidence side-channel):

- the governance **event** `prediction.recorded` (`GOVERNANCE_EVENTS_ENABLED`; log or webhook sink);
- a durable per-tenant **record store** in the object store (`GOVERNANCE_RECORD_STORE=objectstore`),
  one small JSON object per request at `governance/decision-records/<YYYY-MM-DD>/<trace_id>-<n>.json`.
  Only this store has a retention the framework can enforce: `GOVERNANCE_RETENTION_DAYS` (default 183,
  about six months — the floor EU AI Act Art. 19 / 26(6) sets for logs a deployer or provider
  keeps) is applied by pruning day folders older than that, at most once a day per tenant per process.
  The audit log is never pruned (that would break its hash chain), and trace retention is
  Langfuse's to configure — see docs/governance/telemetry.md.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from ai_circus_shared.governance_events import (
    GovernanceEvents,
    GovernanceEventSettings,
    PredictionRecordData,
    env_flag,
)
from ai_circus_shared.tabular_ml import MODEL_CHECKSUMS_METADATA_FIELD

logger = logging.getLogger("ai_circus.governance.records")

RECORD_PREFIX = "governance/decision-records"
DEFAULT_RETENTION_DAYS = 183
_TRACEPARENT = re.compile(r"^[0-9a-f]{2}-([0-9a-f]{32})-[0-9a-f]{16}-[0-9a-f]{2}$")
_DAY_FOLDER = re.compile(rf"^{re.escape(RECORD_PREFIX)}/(\d{{4}}-\d{{2}}-\d{{2}})/")


class RecordStore(Protocol):
    """The slice of `ai_circus_shared.storage.ObjectStore` the recorder needs."""

    def put(self, tenant_org_id: str, path: str, data: bytes) -> str: ...

    def list(self, tenant_org_id: str, prefix: str = "") -> list[str]: ...

    def delete(self, tenant_org_id: str, path: str) -> None: ...


def model_version(metadata: Mapping[str, Any]) -> str:
    """A short, content-derived id of the stored model artifacts (12 hex chars).

    Taken from the training run's artifact checksums when present, so it changes exactly when the
    model does; otherwise from the identifying metadata, and `unversioned` if there is none.
    """
    checksums = metadata.get(MODEL_CHECKSUMS_METADATA_FIELD)
    if isinstance(checksums, Mapping) and checksums:
        material = "|".join(f"{key}={checksums[key]}" for key in sorted(checksums))
    else:
        parts = {key: metadata.get(key) for key in ("model_name", "test_score", "training_rows", "trained_at")}
        if not any(value is not None for value in parts.values()):
            return "unversioned"
        material = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(material.encode()).hexdigest()[:12]


def trace_id_from(headers: Mapping[str, str]) -> str:
    """The request's W3C trace id (from `traceparent`) when the caller sent one, else a fresh id."""
    match = _TRACEPARENT.match(headers.get("traceparent", "").strip().lower())
    return match.group(1) if match else uuid.uuid4().hex


def summarise(outcomes: Iterable[str], scores: Iterable[float]) -> tuple[dict[str, int], float | None, float | None]:
    """An outcome histogram and the (min, max) of the scores, rounded for the record."""
    histogram: dict[str, int] = {}
    for outcome in outcomes:
        histogram[outcome] = histogram.get(outcome, 0) + 1
    values = [float(score) for score in scores]
    if not values:
        return histogram, None, None
    return histogram, round(min(values), 6), round(max(values), 6)


@dataclass
class PredictionRecorder:
    """Emits the `prediction.recorded` event and/or stores the record, with retention. Never raises."""

    events: GovernanceEvents
    # scenario slug -> the object-store bucket that scenario's tenant data lives in (empty = no store)
    stores: Mapping[str, RecordStore] = field(default_factory=dict)
    retention_days: int = DEFAULT_RETENTION_DAYS
    _pruned: dict[tuple[str, str, date], bool] = field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        """Whether a record goes anywhere at all."""
        return self.events.enabled or bool(self.stores)

    def record(self, data: PredictionRecordData, *, now: datetime | None = None) -> None:
        """Send one record to every configured destination."""
        if not self.enabled:
            return
        self.events.emit(
            "prediction.recorded",
            data,
            tenant_id=data.org_id,
            ai_system_id=data.ai_system_id,
            subject=data.scenario_slug,
        )
        store = self.stores.get(data.scenario_slug)
        if store is None:
            return
        moment = now or datetime.now(UTC)
        try:
            path = f"{RECORD_PREFIX}/{moment:%Y-%m-%d}/{data.trace_id}-{uuid.uuid4().hex[:8]}.json"
            payload = {
                "recorded_at": moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")
            } | data.model_dump(mode="json", exclude_none=True)
            store.put(data.org_id, path, json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
            self.prune(data.org_id, data.scenario_slug, today=moment.date())
        except Exception:  # an evidence side-channel must never fail the prediction it records
            logger.exception("decision record not stored for scenario=%s", data.scenario_slug)

    def prune(self, org_id: str, scenario_slug: str, *, today: date | None = None) -> int:
        """Delete this tenant's day folders older than the retention window in one scenario's
        bucket; at most once a day per tenant and scenario per process. Returns how many
        records were deleted.
        """
        day = today or datetime.now(UTC).date()
        store = self.stores.get(scenario_slug)
        if store is None or (org_id, scenario_slug, day) in self._pruned:
            return 0
        self._pruned[(org_id, scenario_slug, day)] = True
        if len(self._pruned) > 10_000:
            self._pruned.clear()
        cutoff = day - timedelta(days=self.retention_days)
        deleted = 0
        for key in store.list(org_id, RECORD_PREFIX + "/"):
            match = _DAY_FOLDER.match(key)
            if match and date.fromisoformat(match.group(1)) < cutoff:
                store.delete(org_id, key)
                deleted += 1
        if deleted:
            logger.info("pruned %d decision records older than %s for org=%s", deleted, cutoff, org_id)
        return deleted


def build_recorder(
    config: object, *, source: str, stores: Mapping[str, RecordStore] | None = None
) -> PredictionRecorder:
    """The recorder a service holds, from its EnvConfig (everything off unless switched on).

    `stores` (one per scenario) are used only when `GOVERNANCE_RECORD_STORE=objectstore`; retention is
    `GOVERNANCE_RETENTION_DAYS` (default 183), and a value below 1 falls back to the default.
    """
    events = GovernanceEvents(GovernanceEventSettings.from_config(config, source=source))
    wants_store = str(getattr(config, "GOVERNANCE_RECORD_STORE", None) or "").strip().lower() == "objectstore"
    raw = getattr(config, "GOVERNANCE_RETENTION_DAYS", None)
    try:
        days = int(raw) if raw not in (None, "") else DEFAULT_RETENTION_DAYS
    except (TypeError, ValueError):
        days = DEFAULT_RETENTION_DAYS
    return PredictionRecorder(
        events=events,
        stores=dict(stores) if (wants_store and stores) else {},
        retention_days=days if days >= 1 else DEFAULT_RETENTION_DAYS,
    )


__all__ = [
    "DEFAULT_RETENTION_DAYS",
    "RECORD_PREFIX",
    "PredictionRecorder",
    "RecordStore",
    "build_recorder",
    "env_flag",
    "model_version",
    "summarise",
    "trace_id_from",
]
