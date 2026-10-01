"""Extract: Übertragungsnetz-Objekte aus OpenStreetMap über die Overpass API laden.

Rohdaten werden unverändert unter data/raw/ abgelegt. Existiert für Region und
Spannungsebenen bereits eine Datei, wird sie wiederverwendet (schont die
öffentliche Overpass-Instanz; mit --refresh erzwingt man einen neuen Abruf).
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import requests

from .config import Config

log = logging.getLogger(__name__)

USER_AGENT = "netzasset-graph/0.1 (Portfolio-Projekt; Datenquelle OpenStreetMap)"


def build_query(region: str, voltages_kv: list[int], timeout_s: int = 300) -> str:
    volts = "|".join(str(kv * 1000) for kv in voltages_kv)
    voltage_re = f"(^|;)({volts})(;|$)"
    return f"""
[out:json][timeout:{timeout_s}];
area["ISO3166-2"="{region}"]["boundary"="administrative"]->.region;
(
  nwr["power"="substation"]["voltage"~"{voltage_re}"](area.region);
  way["power"="line"]["voltage"~"{voltage_re}"](area.region);
);
out body geom;
""".strip()


def raw_path(cfg: Config) -> Path:
    volts = "-".join(str(v) for v in cfg.voltages_kv)
    return cfg.data_dir / "raw" / f"osm_{cfg.region}_{volts}kv.json"


def extract(cfg: Config, refresh: bool = False, retries: int = 3) -> dict:
    path = raw_path(cfg)
    if path.exists() and not refresh:
        log.info("Verwende vorhandene Rohdaten: %s", path)
        return json.loads(path.read_text(encoding="utf-8"))

    query = build_query(cfg.region, cfg.voltages_kv)
    for attempt in range(1, retries + 1):
        log.info("Overpass-Abfrage für %s (%s kV), Versuch %d", cfg.region, cfg.voltages_kv, attempt)
        try:
            resp = requests.post(
                cfg.overpass_url,
                data={"data": query},
                headers={"User-Agent": USER_AGENT},
                timeout=360,
            )
            if resp.status_code in (429, 504):
                raise requests.HTTPError(f"Overpass ausgelastet ({resp.status_code})")
            resp.raise_for_status()
            payload = resp.json()
            break
        except (requests.RequestException, ValueError) as exc:
            if attempt == retries:
                raise RuntimeError(f"Overpass-Abruf fehlgeschlagen: {exc}") from exc
            wait = 30 * attempt
            log.warning("%s, neuer Versuch in %d s", exc, wait)
            time.sleep(wait)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    log.info("%d OSM-Elemente gespeichert: %s", len(payload.get("elements", [])), path)
    return payload
