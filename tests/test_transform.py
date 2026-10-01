"""Tests für Bereinigung und Verknüpfung – laufen ohne Netz und ohne Neo4j.

    python -m unittest discover -s tests -v
"""
import json
import unittest
from pathlib import Path

from pipeline.extract import build_query
from pipeline.geo import haversine_m, point_in_polygon
from pipeline.transform import (
    OperatorNormalizer,
    build_graph,
    operator_key,
    parse_int,
    parse_voltages_kv,
    to_records,
)

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "mini_grid.json").read_text(encoding="utf-8"))


class TestCleaning(unittest.TestCase):
    def test_voltages(self):
        self.assertEqual(parse_voltages_kv("380000;220000"), [220, 380])
        self.assertEqual(parse_voltages_kv("380000;abc;"), [380])
        self.assertEqual(parse_voltages_kv(None), [])

    def test_int(self):
        self.assertEqual(parse_int("1;2"), 2)
        self.assertIsNone(parse_int("viele"))

    def test_operator_key(self):
        self.assertEqual(operator_key("Amprion GmbH"), operator_key("amprion"))
        self.assertEqual(operator_key("TenneT TSO GmbH"), "tennet")

    def test_operator_normalizer(self):
        n = OperatorNormalizer(["Amprion GmbH", "Amprion GmbH", "amprion", "Westnetz GmbH;Amprion"])
        self.assertEqual(n.normalize("amprion"), "Amprion GmbH")
        self.assertEqual(n.normalize("Westnetz GmbH;Amprion"), "Westnetz GmbH")
        self.assertIsNone(n.normalize(""))


class TestGeo(unittest.TestCase):
    def test_haversine(self):
        # 0,01° Breite ≈ 1,11 km
        self.assertAlmostEqual(haversine_m((51.0, 7.0), (51.01, 7.0)), 1112, delta=5)

    def test_point_in_polygon(self):
        square = [(0, 0), (0, 1), (1, 1), (1, 0), (0, 0)]
        self.assertTrue(point_in_polygon((0.5, 0.5), square))
        self.assertFalse(point_in_polygon((1.5, 0.5), square))


class TestGraph(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.g = build_graph(FIXTURE, tolerance_m=250)

    def test_counts(self):
        s = self.g.stats
        self.assertEqual(s["substations"], 3)
        self.assertEqual(s["lines"], 4)  # Leitung ohne Geometrie wird verworfen
        self.assertEqual(s["skipped"], {"leitung_ohne_geometrie": 1})

    def test_operator_variants_merged(self):
        self.assertEqual(self.g.operators, ["Amprion GmbH"])

    def test_relation_substation_has_geometry(self):
        rel = next(s for s in self.g.substations if s.osm_id == "r3")
        self.assertAlmostEqual(rel.lat, 51.60, places=3)
        self.assertEqual(len(rel.polygons), 1)

    def test_ends_matched(self):
        pairs = {(e["line_id"], e["substation_id"]) for e in self.g.ends_at}
        self.assertIn(("w101", "w1"), pairs)  # innerhalb der Fläche
        self.assertIn(("w102", "n2"), pairs)  # ~55 m neben dem Punkt, innerhalb der Toleranz
        self.assertIn(("w103", "w1"), pairs)
        self.assertIn(("w103", "r3"), pairs)
        self.assertEqual(self.g.stats["ends_unmatched"], 4)

    def test_joins_only_same_voltage(self):
        self.assertEqual([(j["a"], j["b"]) for j in self.g.joins], [("w101", "w102")])

    def test_connects(self):
        edges = {(c["a"], c["b"]): c for c in self.g.connects}
        self.assertEqual(set(edges), {("n2", "w1"), ("r3", "w1")})
        ab = edges[("n2", "w1")]
        self.assertEqual(ab["line_ids"], ["w101", "w102"])
        self.assertEqual(ab["voltages_kv"], [380])
        self.assertFalse(ab["branching"])

    def test_tolerance_respected(self):
        g = build_graph(FIXTURE, tolerance_m=10)
        self.assertNotIn(("w102", "n2"), {(e["line_id"], e["substation_id"]) for e in g.ends_at})

    def test_records_are_flat(self):
        rec = to_records(self.g)
        self.assertNotIn("polygons", rec["substations"][0])
        self.assertIn("start_lat", rec["lines"][0])
        json.dumps(rec)  # muss serialisierbar sein


class TestQuery(unittest.TestCase):
    def test_query(self):
        q = build_query("DE-NW", [220, 380])
        self.assertIn('"ISO3166-2"="DE-NW"', q)
        self.assertIn("220000|380000", q)
        self.assertIn("out body geom", q)


if __name__ == "__main__":
    unittest.main()
