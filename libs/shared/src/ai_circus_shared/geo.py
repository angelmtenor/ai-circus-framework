"""Small geographic helpers: UTM -> latitude/longitude and great-circle distance. Pure Python.

Open-data portals often publish station positions as UTM metres (ETRS89 / WGS84, which differ by less than
a metre here), and a scenario then wants "stations within X km". Nothing here needs a projection library.
"""

from __future__ import annotations

import math

_A = 6378137.0  # GRS80 / WGS84 semi-major axis, metres
_F = 1 / 298.257222101
_K0 = 0.9996
_FALSE_EASTING = 500_000.0
EARTH_RADIUS_KM = 6371.0088


def utm_to_latlon(easting: float, northing: float, zone: int, *, north: bool = True) -> tuple[float, float]:
    """(latitude, longitude) in degrees of a UTM position (Krueger series, sub-metre accuracy)."""
    if not 1 <= zone <= 60:
        raise ValueError(f"UTM zone {zone} is not between 1 and 60")
    n = _F / (2 - _F)
    a_hat = _A / (1 + n) * (1 + n**2 / 4 + n**4 / 64)
    beta = (
        n / 2 - 2 * n**2 / 3 + 37 * n**3 / 96 - n**4 / 360,
        n**2 / 48 + n**3 / 15 - 437 * n**4 / 1440,
        17 * n**3 / 480 - 37 * n**4 / 840,
        4397 * n**4 / 161280,
    )
    delta = (
        2 * n - 2 * n**2 / 3 - 2 * n**3,
        7 * n**2 / 3 - 8 * n**3 / 5,
        56 * n**3 / 15,
        4279 * n**4 / 630,
    )
    xi = (northing if north else northing - 10_000_000.0) / (_K0 * a_hat)
    eta = (easting - _FALSE_EASTING) / (_K0 * a_hat)
    xi_p, eta_p = xi, eta
    for j, b in enumerate(beta, start=1):
        xi_p -= b * math.sin(2 * j * xi) * math.cosh(2 * j * eta)
        eta_p -= b * math.cos(2 * j * xi) * math.sinh(2 * j * eta)
    chi = math.asin(math.sin(xi_p) / math.cosh(eta_p))
    lat = chi + sum(d * math.sin(2 * j * chi) for j, d in enumerate(delta, start=1))
    lon0 = math.radians((zone - 1) * 6 - 180 + 3)
    lon = lon0 + math.atan2(math.sinh(eta_p), math.cos(xi_p))
    return math.degrees(lat), math.degrees(lon)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))
