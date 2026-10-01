"""Konfiguration aus Umgebungsvariablen."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _voltages() -> list[int]:
    raw = os.getenv("VOLTAGES_KV", "220,380")
    return sorted({int(v.strip()) for v in raw.split(",") if v.strip()})


@dataclass(frozen=True)
class Config:
    neo4j_uri: str = os.getenv("NEO4J_URI", "bolt://localhost:7687")
    neo4j_user: str = os.getenv("NEO4J_USER", "neo4j")
    neo4j_password: str = os.getenv("NEO4J_PASSWORD", "netzasset-demo")
    region: str = os.getenv("REGION", "DE-NW")
    voltages_kv: list[int] = field(default_factory=_voltages)
    match_tolerance_m: float = float(os.getenv("MATCH_TOLERANCE_M", "250"))
    overpass_url: str = os.getenv("OVERPASS_URL", "https://overpass-api.de/api/interpreter")
    data_dir: Path = Path(os.getenv("DATA_DIR", "data"))
