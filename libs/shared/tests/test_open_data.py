"""ai_circus_shared.open_data and geo — the canonical observation shape, and the distances around it."""

from __future__ import annotations

from datetime import date

import pytest

from ai_circus_shared.geo import haversine_km, utm_to_latlon
from ai_circus_shared.open_data import FieldMap, normalise

FIELDS = FieldMap(day="date", series="station", level="level_m", pct="pct", volume="vol")


def test_normalise_never_turns_missing_into_zero_and_keeps_duplicates() -> None:
    rows = [
        {"date": "2024-03-01T00:00:00.000", "station": "A", "level_m": "", "pct": "n/a", "vol": "1.25"},
        {"date": "2024-03-01T00:00:00.000", "station": "A", "level_m": "3", "pct": "10", "vol": "1.3"},
        {"date": "garbage", "station": "A", "pct": "1"},
        {"date": "2024-03-02T00:00:00.000", "station": "", "pct": "1"},
    ]
    out = normalise(rows, FIELDS)
    assert len(out) == 2  # the two unusable rows are dropped, the duplicate is not
    assert out[0].level is None and out[0].pct is None and out[0].volume == 1.25
    assert out[1].day == date(2024, 3, 1)


def test_utm_matches_a_known_point() -> None:
    # On zone 33's central meridian (15 E), 5,000 km north of the equator is about 45.149 N.
    lat, lon = utm_to_latlon(500000, 5000000, 33)
    assert lat == pytest.approx(45.149, abs=0.01)
    assert lon == pytest.approx(15.0, abs=1e-6)
    # The zone's own central meridian at the equator.
    lat0, lon0 = utm_to_latlon(500000, 0, 31)
    assert (lat0, lon0) == (pytest.approx(0, abs=1e-9), pytest.approx(3, abs=1e-9))
    with pytest.raises(ValueError):
        utm_to_latlon(500000, 0, 99)


def test_haversine() -> None:
    assert haversine_km(10.0, 20.0, 10.0, 20.0) == 0
    assert haversine_km(0, 0, 0, 1) == pytest.approx(111.19, abs=0.1)
