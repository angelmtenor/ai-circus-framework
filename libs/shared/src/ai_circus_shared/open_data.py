"""Daily observations in one canonical shape, whatever feed they come from.

A feed declares *how its columns map* onto the canonical fields (`FieldMap`) and `normalise` turns its raw rows into
`Observation`s; an adapter for a real source implements `ObservationSource`, and nothing downstream changes. Demos never
call a live API: a script writes a snapshot once and it is committed.

Pure Python, so `libs/shared` stays free of pandas/numpy.
"""

from __future__ import annotations

import math
from collections.abc import Iterator, Mapping
from datetime import date, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict


class FieldMap(BaseModel):
    """Which source column holds each canonical observation field. `series` is the entity (a reservoir)."""

    model_config = ConfigDict(extra="forbid")

    day: str
    series: str
    level: str | None = None
    pct: str | None = None
    volume: str | None = None


class Observation(BaseModel):
    """One reading of one series on one day. A missing number is None, never a made-up value."""

    model_config = ConfigDict(frozen=True)

    series: str
    day: date
    level: float | None = None
    pct: float | None = None
    volume: float | None = None


class ObservationSource(Protocol):
    """What a data adapter offers the rest of the scenario."""

    def fetch_observations(self, field_map: FieldMap, *, since: date | None = None) -> list[Observation]: ...


def _number(raw: object) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if math.isnan(value) or math.isinf(value) else value


def _day(raw: object) -> date | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw)).date()
    except ValueError:
        return None


def normalise(
    rows: Iterator[Mapping[str, object]] | list[Mapping[str, object]], field_map: FieldMap
) -> list[Observation]:
    """Canonical observations from raw rows. A row with no usable day or series is dropped (and counted by the
    caller through the length difference); numbers that do not parse become None, never zero. Duplicates are
    kept: finding them is the data-quality module's job, not the adapter's."""
    out: list[Observation] = []
    for row in rows:
        day, series = _day(row.get(field_map.day)), row.get(field_map.series)
        if day is None or not series:
            continue
        out.append(
            Observation(
                series=str(series),
                day=day,
                level=_number(row.get(field_map.level)) if field_map.level else None,
                pct=_number(row.get(field_map.pct)) if field_map.pct else None,
                volume=_number(row.get(field_map.volume)) if field_map.volume else None,
            )
        )
    return out
