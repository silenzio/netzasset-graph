"""Einstiegspunkt: python -m pipeline.run [--refresh] [--skip-load]"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from .config import Config
from .extract import extract
from .transform import build_graph, to_records


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OSM-Übertragungsnetz -> Neo4j")
    parser.add_argument("--refresh", action="store_true", help="Rohdaten neu von Overpass laden")
    parser.add_argument("--skip-load", action="store_true", help="nur Extract + Transform, nicht nach Neo4j laden")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    log = logging.getLogger("pipeline")
    cfg = Config()
    t0 = time.perf_counter()

    payload = extract(cfg, refresh=args.refresh)
    graph = build_graph(payload, tolerance_m=cfg.match_tolerance_m)
    records = to_records(graph)

    stats_path = cfg.data_dir / "run_stats.json"
    stats_path.parent.mkdir(parents=True, exist_ok=True)
    stats_path.write_text(json.dumps(graph.stats, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("Transform-Kennzahlen: %s", json.dumps(graph.stats, ensure_ascii=False))

    if not args.skip_load:
        from .load import load  # Import erst hier: --skip-load braucht keinen Neo4j-Treiber

        counts = load(records, cfg.neo4j_uri, cfg.neo4j_user, cfg.neo4j_password)
        log.info("Load abgeschlossen: %s", counts)

    log.info("Fertig in %.1f s", time.perf_counter() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
