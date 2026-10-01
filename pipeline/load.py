"""Load: Graph idempotent nach Neo4j schreiben (MERGE, in Batches)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from neo4j import GraphDatabase

log = logging.getLogger(__name__)

BATCH = 1000

CONSTRAINTS = [
    "CREATE CONSTRAINT substation_id IF NOT EXISTS FOR (s:Substation) REQUIRE s.osm_id IS UNIQUE",
    "CREATE CONSTRAINT line_id IF NOT EXISTS FOR (l:Line) REQUIRE l.osm_id IS UNIQUE",
    "CREATE CONSTRAINT operator_name IF NOT EXISTS FOR (o:Operator) REQUIRE o.name IS UNIQUE",
    "CREATE POINT INDEX substation_location IF NOT EXISTS FOR (s:Substation) ON (s.location)",
]

Q_SUBSTATIONS = """
UNWIND $rows AS r
MERGE (s:Substation {osm_id: r.osm_id})
SET s.name = r.name,
    s.voltages_kv = r.voltages_kv,
    s.max_voltage_kv = CASE WHEN size(r.voltages_kv) > 0 THEN r.voltages_kv[-1] END,
    s.location = point({latitude: r.lat, longitude: r.lon}),
    s.source = 'OpenStreetMap',
    s.loaded_at = $run_ts
WITH s, r
WHERE r.operator IS NOT NULL
MERGE (o:Operator {name: r.operator})
MERGE (s)-[:OPERATED_BY]->(o)
"""

Q_LINES = """
UNWIND $rows AS r
MERGE (l:Line {osm_id: r.osm_id})
SET l.name = r.name,
    l.ref = r.ref,
    l.voltages_kv = r.voltages_kv,
    l.circuits = r.circuits,
    l.length_km = r.length_km,
    l.start_location = point({latitude: r.start_lat, longitude: r.start_lon}),
    l.end_location = point({latitude: r.end_lat, longitude: r.end_lon}),
    l.unmatched_ends = r.unmatched_ends,
    l.source = 'OpenStreetMap',
    l.loaded_at = $run_ts
WITH l, r
WHERE r.operator IS NOT NULL
MERGE (o:Operator {name: r.operator})
MERGE (l)-[:OPERATED_BY]->(o)
"""

Q_ENDS_AT = """
UNWIND $rows AS r
MATCH (l:Line {osm_id: r.line_id}), (s:Substation {osm_id: r.substation_id})
MERGE (l)-[e:ENDS_AT {end: r.end}]->(s)
SET e.distance_m = r.distance_m
"""

Q_JOINS = """
UNWIND $rows AS r
MATCH (a:Line {osm_id: r.a}), (b:Line {osm_id: r.b})
MERGE (a)-[j:JOINS {osm_node: r.osm_node}]-(b)
"""

Q_CONNECTS = """
UNWIND $rows AS r
MATCH (a:Substation {osm_id: r.a}), (b:Substation {osm_id: r.b})
MERGE (a)-[c:CONNECTS {chain_id: r.chain_id}]-(b)
SET c.line_ids = r.line_ids,
    c.voltages_kv = r.voltages_kv,
    c.chain_length_km = r.chain_length_km,
    c.branching = r.branching,
    c.derived = true
"""

# Vor einem Neuaufbau abgeleitete Kanten entfernen, damit keine Altlasten bleiben
Q_RESET_DERIVED = "MATCH ()-[r:ENDS_AT|JOINS|CONNECTS|OPERATED_BY]-() DELETE r"

# Knoten, die im aktuellen Lauf nicht mehr geliefert wurden (in OSM gelöscht)
Q_PRUNE = """
MATCH (n) WHERE (n:Substation OR n:Line) AND n.loaded_at <> $run_ts
DETACH DELETE n
"""
Q_PRUNE_OPERATORS = "MATCH (o:Operator) WHERE NOT (o)<-[:OPERATED_BY]-() DELETE o"


def _batched(rows: list[dict]):
    for i in range(0, len(rows), BATCH):
        yield rows[i : i + BATCH]


def load(records: dict[str, list[dict]], uri: str, user: str, password: str) -> dict:
    run_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    counts = {}
    with GraphDatabase.driver(uri, auth=(user, password)) as driver:
        driver.verify_connectivity()
        with driver.session() as session:
            for stmt in CONSTRAINTS:
                session.run(stmt).consume()
            session.run(Q_RESET_DERIVED).consume()

            for key, query in (
                ("substations", Q_SUBSTATIONS),
                ("lines", Q_LINES),
                ("ends_at", Q_ENDS_AT),
                ("joins", Q_JOINS),
                ("connects", Q_CONNECTS),
            ):
                rows = records[key]
                for batch in _batched(rows):
                    session.execute_write(lambda tx, b=batch, q=query: tx.run(q, rows=b, run_ts=run_ts).consume())
                counts[key] = len(rows)
                log.info("Geladen: %-12s %6d", key, len(rows))

            pruned = session.run(Q_PRUNE, run_ts=run_ts).consume().counters.nodes_deleted
            session.run(Q_PRUNE_OPERATORS).consume()
            counts["pruned"] = pruned
    return counts
