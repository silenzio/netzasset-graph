"""Transform: OSM-Rohdaten bereinigen, normalisieren und zu einem Graphen verknüpfen.

Ergebnis:
  * Substation  – Umspannwerke/Schaltanlagen (Knoten)
  * Line        – Leitungsabschnitte, wie sie in OSM erfasst sind (Knoten)
  * Operator    – normalisierte Betreibernamen (Knoten)
  * ENDS_AT     – Leitungsende liegt in/an einem Umspannwerk
  * JOINS       – zwei Leitungsabschnitte gleicher Spannung teilen einen Endpunkt
  * CONNECTS    – abgeleitete Verbindung Umspannwerk <-> Umspannwerk über eine
                  zusammenhängende Leitungskette
"""
from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from itertools import combinations

from .geo import Point, centroid, haversine_m, point_in_polygon, polyline_length_m

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Datenklassen
# --------------------------------------------------------------------------- #
@dataclass
class Substation:
    osm_id: str
    name: str | None
    operator: str | None
    voltages_kv: list[int]
    lat: float
    lon: float
    polygons: list[list[Point]] = field(default_factory=list, repr=False)


@dataclass
class Line:
    osm_id: str
    name: str | None
    ref: str | None
    operator: str | None
    voltages_kv: list[int]
    circuits: int | None
    length_km: float
    start_node: int
    end_node: int
    start: Point = field(repr=False)
    end: Point = field(repr=False)
    unmatched_ends: int = 0


@dataclass
class Graph:
    substations: list[Substation]
    lines: list[Line]
    operators: list[str]
    ends_at: list[dict]
    joins: list[dict]
    connects: list[dict]
    stats: dict


# --------------------------------------------------------------------------- #
# Bereinigung einzelner Attribute
# --------------------------------------------------------------------------- #
def parse_voltages_kv(raw: str | None) -> list[int]:
    """'380000;220000' -> [220, 380]; unbrauchbare Werte werden verworfen."""
    if not raw:
        return []
    out = set()
    for part in re.split(r"[;,]", raw):
        part = part.strip()
        if part.isdigit() and int(part) >= 1000:
            out.add(int(part) // 1000)
    return sorted(out)


def parse_int(raw: str | None) -> int | None:
    """'2' -> 2, '2;1' -> 2 (Maximum), sonst None."""
    if not raw:
        return None
    nums = [int(p) for p in re.split(r"[;,]", raw) if p.strip().isdigit()]
    return max(nums) if nums else None


_LEGAL_FORMS = re.compile(
    r"\b(gmbh|ag|se|kg|co|tso|mbh|ug|plc|ltd)\b|&|\.|,", flags=re.IGNORECASE
)


def operator_key(name: str) -> str:
    """Vergleichsschlüssel: Kleinschreibung, ohne Rechtsform und Satzzeichen."""
    return re.sub(r"\s+", " ", _LEGAL_FORMS.sub(" ", name.lower())).strip()


def split_operators(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [p.strip() for p in raw.split(";") if p.strip()]


class OperatorNormalizer:
    """Fasst Schreibvarianten ('Amprion', 'Amprion GmbH', 'amprion gmbh') zusammen.

    Kanonischer Name ist die häufigste Schreibweise je Schlüssel.
    """

    def __init__(self, raw_values: list[str | None]):
        variants: dict[str, Counter] = defaultdict(Counter)
        for raw in raw_values:
            for name in split_operators(raw):
                variants[operator_key(name)][name] += 1
        self.canonical = {
            key: counter.most_common(1)[0][0] for key, counter in variants.items() if key
        }
        self.variant_count = sum(len(c) for c in variants.values())

    def normalize(self, raw: str | None) -> str | None:
        names = split_operators(raw)
        if not names:
            return None
        # Bei mehreren Betreibern zählt der erstgenannte als Hauptbetreiber.
        return self.canonical.get(operator_key(names[0]), names[0])


# --------------------------------------------------------------------------- #
# OSM-Elemente lesen
# --------------------------------------------------------------------------- #
def _geometry(points: list[dict]) -> list[Point]:
    return [(p["lat"], p["lon"]) for p in points if p and "lat" in p]


def _substation_geometry(el: dict) -> tuple[Point, list[list[Point]]]:
    if el["type"] == "node":
        return (el["lat"], el["lon"]), []
    if el["type"] == "way":
        ring = _geometry(el.get("geometry", []))
        return centroid(ring), [ring]
    # Relation (Multipolygon): äußere Ringe einsammeln
    rings = [
        _geometry(m.get("geometry", []))
        for m in el.get("members", [])
        if m.get("type") == "way" and m.get("role", "outer") in ("outer", "")
    ]
    rings = [r for r in rings if len(r) >= 3]
    if not rings:
        raise ValueError("Relation ohne Geometrie")
    return centroid([p for r in rings for p in r]), rings


def parse_elements(payload: dict, normalizer: OperatorNormalizer | None = None):
    elements = payload.get("elements", [])
    if normalizer is None:
        normalizer = OperatorNormalizer([e.get("tags", {}).get("operator") for e in elements])

    substations: dict[str, Substation] = {}
    lines: dict[str, Line] = {}
    skipped = Counter()

    for el in elements:
        tags = el.get("tags", {})
        osm_id = f"{el['type'][0]}{el['id']}"
        power = tags.get("power")

        if power == "substation":
            try:
                (lat, lon), polygons = _substation_geometry(el)
            except (ValueError, KeyError):
                skipped["substation_ohne_geometrie"] += 1
                continue
            substations[osm_id] = Substation(
                osm_id=osm_id,
                name=tags.get("name"),
                operator=normalizer.normalize(tags.get("operator")),
                voltages_kv=parse_voltages_kv(tags.get("voltage")),
                lat=lat,
                lon=lon,
                polygons=polygons,
            )

        elif power == "line" and el["type"] == "way":
            geom = _geometry(el.get("geometry", []))
            nodes = el.get("nodes", [])
            if len(geom) < 2 or len(nodes) < 2:
                skipped["leitung_ohne_geometrie"] += 1
                continue
            lines[osm_id] = Line(
                osm_id=osm_id,
                name=tags.get("name"),
                ref=tags.get("ref"),
                operator=normalizer.normalize(tags.get("operator")),
                voltages_kv=parse_voltages_kv(tags.get("voltage")),
                circuits=parse_int(tags.get("circuits")),
                length_km=round(polyline_length_m(geom) / 1000, 3),
                start_node=nodes[0],
                end_node=nodes[-1],
                start=geom[0],
                end=geom[-1],
            )

    return list(substations.values()), list(lines.values()), normalizer, skipped


# --------------------------------------------------------------------------- #
# Verknüpfung
# --------------------------------------------------------------------------- #
def match_endpoint(p: Point, substations: list[Substation], tolerance_m: float):
    """Nächstes Umspannwerk zu einem Leitungsende.

    Liegt der Punkt innerhalb einer Umspannwerksfläche, ist der Abstand 0.
    Sonst zählt der kleinere Abstand zu Mittelpunkt oder Umrisspunkten.
    """
    best, best_d = None, float("inf")
    for s in substations:
        # Grobfilter (~0,05° ≈ 3–5 km), spart Rechenzeit
        if abs(s.lat - p[0]) > 0.05 or abs(s.lon - p[1]) > 0.08:
            continue
        if any(point_in_polygon(p, poly) for poly in s.polygons):
            return s, 0.0
        d = haversine_m(p, (s.lat, s.lon))
        for poly in s.polygons:
            d = min(d, min(haversine_m(p, q) for q in poly))
        if d < best_d:
            best, best_d = s, d
    if best is not None and best_d <= tolerance_m:
        return best, best_d
    return None, None


class _UnionFind:
    def __init__(self, items):
        self.parent = {i: i for i in items}

    def find(self, x):
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def build_graph(payload: dict, tolerance_m: float = 250.0) -> Graph:
    substations, lines, normalizer, skipped = parse_elements(payload)
    log.info("Gelesen: %d Umspannwerke, %d Leitungsabschnitte", len(substations), len(lines))

    # 1) Leitungsenden mit Umspannwerken verknüpfen
    ends_at: list[dict] = []
    matched_ends: set[tuple[str, int]] = set()  # (line_id, osm_node)
    for line in lines:
        for end, point, node in (("start", line.start, line.start_node), ("end", line.end, line.end_node)):
            sub, dist = match_endpoint(point, substations, tolerance_m)
            if sub is None:
                line.unmatched_ends += 1
                continue
            matched_ends.add((line.osm_id, node))
            ends_at.append(
                {"line_id": line.osm_id, "substation_id": sub.osm_id, "end": end, "distance_m": round(dist, 1)}
            )

    # 2) Abschnitte gleicher Spannung, die sich einen freien Endpunkt teilen, verbinden
    at_node: dict[int, list[Line]] = defaultdict(list)
    for line in lines:
        for node in (line.start_node, line.end_node):
            if (line.osm_id, node) not in matched_ends:
                at_node[node].append(line)

    uf = _UnionFind([l.osm_id for l in lines])
    joins: list[dict] = []
    for node, group in at_node.items():
        for a, b in combinations(group, 2):
            if a.osm_id == b.osm_id:
                continue
            if set(a.voltages_kv) & set(b.voltages_kv):
                uf.union(a.osm_id, b.osm_id)
                joins.append({"a": a.osm_id, "b": b.osm_id, "osm_node": node})

    # 3) Leitungsketten -> Verbindungen zwischen Umspannwerken ableiten
    by_line = {l.osm_id: l for l in lines}
    chain_lines: dict[str, list[str]] = defaultdict(list)
    for l in lines:
        chain_lines[uf.find(l.osm_id)].append(l.osm_id)
    chain_subs: dict[str, set[str]] = defaultdict(set)
    for e in ends_at:
        chain_subs[uf.find(e["line_id"])].add(e["substation_id"])

    connects: list[dict] = []
    for root, subs in chain_subs.items():
        if len(subs) < 2:
            continue
        members = [by_line[i] for i in chain_lines[root]]
        branching = len(subs) > 2
        for a, b in combinations(sorted(subs), 2):
            connects.append(
                {
                    "a": a,
                    "b": b,
                    "chain_id": root,
                    "line_ids": sorted(m.osm_id for m in members),
                    "voltages_kv": sorted({v for m in members for v in m.voltages_kv}),
                    "chain_length_km": round(sum(m.length_km for m in members), 2),
                    "branching": branching,
                }
            )

    operators = sorted({x.operator for x in [*substations, *lines] if x.operator})
    stats = {
        "substations": len(substations),
        "lines": len(lines),
        "line_km_total": round(sum(l.length_km for l in lines), 1),
        "operators": len(operators),
        "operator_variants_merged": normalizer.variant_count - len(normalizer.canonical),
        "ends_matched": len(ends_at),
        "ends_unmatched": sum(l.unmatched_ends for l in lines),
        "joins": len(joins),
        "connects": len(connects),
        "skipped": dict(skipped),
    }
    return Graph(substations, lines, operators, ends_at, joins, connects, stats)


def to_records(graph: Graph) -> dict[str, list[dict]]:
    """Für den Loader: flache, Neo4j-taugliche Dictionaries (ohne Polygone)."""
    subs = []
    for s in graph.substations:
        d = asdict(s)
        d.pop("polygons")
        subs.append(d)
    lines = []
    for l in graph.lines:
        d = asdict(l)
        d.pop("start"), d.pop("end")
        d["start_lat"], d["start_lon"] = l.start
        d["end_lat"], d["end_lon"] = l.end
        lines.append(d)
    return {
        "substations": subs,
        "lines": lines,
        "operators": [{"name": n} for n in graph.operators],
        "ends_at": graph.ends_at,
        "joins": graph.joins,
        "connects": graph.connects,
    }
