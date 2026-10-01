// Beispielabfragen für den Neo4j Browser (http://localhost:7474)
// Tipp: Abfragen einzeln markieren und ausführen.

// 1) Überblick: Wie viele Objekte welcher Art?
MATCH (n) RETURN labels(n)[0] AS typ, count(*) AS anzahl ORDER BY anzahl DESC;

// 2) Netzbild: Umspannwerke und ihre abgeleiteten Verbindungen
MATCH p = (:Substation)-[:CONNECTS]-(:Substation) RETURN p LIMIT 300;

// 3) Am stärksten vernetzte Umspannwerke (Knotengrad)
MATCH (s:Substation)-[c:CONNECTS]-(nachbar:Substation)
RETURN coalesce(s.name, s.osm_id) AS umspannwerk,
       s.voltages_kv            AS spannungen_kv,
       count(DISTINCT nachbar)  AS verbundene_umspannwerke
ORDER BY verbundene_umspannwerke DESC LIMIT 15;

// 4) Kürzester Weg (Anzahl Verbindungen) zwischen zwei Umspannwerken
//    Namen bei Bedarf anpassen – Abfrage 3 liefert passende Kandidaten.
MATCH (a:Substation), (b:Substation)
WHERE a.name CONTAINS 'Gersteinwerk' AND b.name CONTAINS 'Rommerskirchen'
MATCH p = shortestPath((a)-[:CONNECTS*..15]-(b))
RETURN p;

// 5) Einzelnes Umspannwerk mit allen angeschlossenen Leitungsabschnitten
MATCH (s:Substation) WHERE s.name CONTAINS 'Gersteinwerk'
MATCH (l:Line)-[:ENDS_AT]->(s)
OPTIONAL MATCH (l)-[:OPERATED_BY]->(o:Operator)
RETURN s, l, o;

// 6) Leitungskilometer je Betreiber und Spannungsebene
MATCH (l:Line)
OPTIONAL MATCH (l)-[:OPERATED_BY]->(o:Operator)
UNWIND l.voltages_kv AS kv
RETURN coalesce(o.name, '(ohne Betreiber)') AS betreiber, kv,
       round(sum(l.length_km), 1) AS leitungs_km, count(l) AS abschnitte
ORDER BY leitungs_km DESC;

// 7) Umspannwerke im Umkreis von 25 km um einen Punkt (hier: Dortmund-Mitte)
WITH point({latitude: 51.5136, longitude: 7.4653}) AS dortmund
MATCH (s:Substation)
WHERE point.distance(s.location, dortmund) < 25000
RETURN coalesce(s.name, s.osm_id) AS umspannwerk,
       round(point.distance(s.location, dortmund) / 1000, 1) AS entfernung_km
ORDER BY entfernung_km;

// 8) Integrationslücken: Leitungsabschnitte, deren Enden keinem Umspannwerk
//    und keinem anderen Abschnitt zugeordnet werden konnten
MATCH (l:Line) WHERE l.unmatched_ends > 0 AND NOT (l)-[:JOINS]-()
RETURN l.osm_id, l.name, l.voltages_kv, l.length_km, l.unmatched_ends
ORDER BY l.length_km DESC LIMIT 25;
