"""Kleine Geo-Hilfsfunktionen ohne externe Abhängigkeiten."""
from __future__ import annotations

import math

Point = tuple[float, float]  # (lat, lon)

EARTH_RADIUS_M = 6_371_000.0


def haversine_m(a: Point, b: Point) -> float:
    """Großkreisabstand zweier Punkte in Metern."""
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def polyline_length_m(points: list[Point]) -> float:
    return sum(haversine_m(p, q) for p, q in zip(points, points[1:]))


def centroid(points: list[Point]) -> Point:
    """Einfacher Mittelpunkt der Stützpunkte (für kleine Flächen ausreichend)."""
    if not points:
        raise ValueError("centroid() braucht mindestens einen Punkt")
    pts = points[:-1] if len(points) > 1 and points[0] == points[-1] else points
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))


def point_in_polygon(p: Point, polygon: list[Point]) -> bool:
    """Ray-Casting-Test; polygon als Liste von (lat, lon)."""
    lat, lon = p
    inside = False
    n = len(polygon)
    for i in range(n):
        lat_i, lon_i = polygon[i]
        lat_j, lon_j = polygon[i - 1]
        if (lat_i > lat) != (lat_j > lat):
            lon_cross = lon_i + (lat - lat_i) * (lon_j - lon_i) / (lat_j - lat_i)
            if lon < lon_cross:
                inside = not inside
    return inside
