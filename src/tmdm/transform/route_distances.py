"""Distances for every covered route, with blended ports and canal variants.

The distance of a route is the volume-weighted blend of the distances between
its loading ports and discharge ports (weights in :mod:`tmdm.reference`).
Four routing variants are computed: the shortest path, and paths avoiding the
Suez Canal, the Panama Canal, or both. Iron ore and coal routes are assumed
to be carried by Capesize and large Panamax tonnage that does not use the
Panama Canal, so their baseline distance is the variant avoiding Panama.
"""

from __future__ import annotations

import json
import logging
import unicodedata

import pandas as pd

from tmdm.config import PROCESSED_DIR
from tmdm.ingest import wpi
from tmdm.reference import DISCHARGE, IMPORTER_ORIGIN_LOAD, PORTS, ROUTE_GROUPS, PortSpec, loading_blend
from tmdm.transform.distances import RESTRICTION_SETS, Port, sea_distance_nm

log = logging.getLogger(__name__)

BASELINE_VARIANT = {"iron_ore": "avoid_panama", "coal": "avoid_panama", "grains": "shortest"}
# Coal from the Americas to Pacific destinations moves largely in Panamax tonnage through the canal.
PANAMAX_COAL_ORIGINS = {"USA", "COL", "CAN"}


def baseline_variant(route_id: str) -> str:
    """Routing variant used as the baseline distance of a route."""
    origin, _dest, commodity = route_id.split("-", 2)
    if commodity == "coal" and origin in PANAMAX_COAL_ORIGINS:
        return "shortest"
    return BASELINE_VARIANT[commodity]


def _norm(s: str) -> str:
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower().strip()


def resolve_ports(wpi_df: pd.DataFrame) -> dict[str, Port]:
    """Match every PortSpec against the World Port Index by name and country."""
    wpi_df = wpi_df.copy()
    wpi_df["_name"] = wpi_df["port_name"].map(_norm)
    wpi_df["_alt"] = wpi_df["alt_name"].fillna("").map(_norm)
    wpi_df["_country"] = wpi_df["country"].map(_norm)
    ports: dict[str, Port] = {}
    for pid, spec in PORTS.items():
        match = _find(wpi_df, spec)
        if match is None:
            raise LookupError(f"port {pid} ({spec.names}, {spec.country}) not found in the World Port Index")
        ports[pid] = Port(pid, spec.label, spec.country, float(match["lat"]), float(match["lon"]))
    return ports


def _find(df: pd.DataFrame, spec: PortSpec) -> pd.Series | None:
    country = _norm(spec.country)
    cand = df[df["_country"] == country]
    if cand.empty:
        cand = df[df["_country"].str.contains(country.split()[0], regex=False)]
    for name in spec.names:
        n = _norm(name)
        hit = cand[(cand["_name"] == n) | (cand["_alt"] == n)]
        if not hit.empty:
            return hit.iloc[0]
    for name in spec.names:
        n = _norm(name)
        hit = cand[cand["_name"].str.startswith(n) | cand["_alt"].str.contains(n, regex=False)]
        if not hit.empty:
            return hit.iloc[0]
    return None


def route_port_pairs() -> dict[str, tuple[str, str, tuple[tuple[str, float], ...], tuple[tuple[str, float], ...]]]:
    """Map route_id to (origin, destination, loading blend, discharge blend)."""
    out = {}
    for g in ROUTE_GROUPS:
        if g.group_id.endswith("_mirror"):
            continue
        origin_iso = g.reporter.replace("_COMEX", "") if g.flow == "X" else None
        for p in g.partners:
            if g.flow == "X":
                origin, dest = origin_iso or "", p
                load = loading_blend(origin, g.load_key, dest)
            else:
                origin, dest = p, g.reporter
                load = loading_blend(origin, IMPORTER_ORIGIN_LOAD[(g.group_id, origin)], dest)
            disc = DISCHARGE.get((dest, g.commodity))
            if disc is None:
                raise KeyError(f"no discharge port for {dest} {g.commodity} (group {g.group_id})")
            out[f"{origin}-{dest}-{g.commodity}"] = (origin, dest, load, disc)
    return out


# Ports inside the Red Sea (north of Bab el-Mandeb): a route serving one cannot avoid the strait by
# sailing round the Cape, so no Red Sea diversion is applied to it.
RED_SEA_BOX = (12.5, 30.0, 32.0, 44.0)  # min lat, max lat, min lon, max lon


def in_red_sea(port: Port) -> bool:
    """True if the port lies inside the Red Sea box."""
    lat0, lat1, lon0, lon1 = RED_SEA_BOX
    return lat0 <= port.lat <= lat1 and lon0 <= port.lon <= lon1


def build_route_distances(ports: dict[str, Port]) -> tuple[pd.DataFrame, dict[str, list[list[float]]]]:
    """Compute blended distances per route and variant, and keep the geometry of the main leg."""
    pairs = route_port_pairs()
    cache: dict[tuple[str, str, str], tuple[float, list[list[float]]]] = {}
    rows = []
    geoms: dict[str, list[list[float]]] = {}
    for rid, (origin, dest, load, disc) in pairs.items():
        commodity = rid.split("-")[-1]
        for variant in RESTRICTION_SETS:
            total, weight = 0.0, 0.0
            feasible = True
            main_leg: tuple[float, str, str] | None = None
            for lp, lw in load:
                for dp, dw in disc:
                    key = (lp, dp, variant)
                    if key not in cache:
                        try:
                            cache[key] = sea_distance_nm(ports[lp], ports[dp], variant)
                        except Exception as exc:  # noqa: BLE001 - infeasible variant for this pair
                            log.warning("no %s route %s-%s: %s", variant, lp, dp, exc)
                            cache[key] = (float("nan"), [])
                    dist, _ = cache[key]
                    if dist != dist:
                        feasible = False
                        break
                    total += lw * dw * dist
                    weight += lw * dw
                    if main_leg is None or lw * dw > main_leg[0]:
                        main_leg = (lw * dw, lp, dp)
                if not feasible:
                    break
            if not feasible or weight == 0:
                continue
            dist_nm = total / weight
            rows.append(
                {
                    "route_id": rid,
                    "origin": origin,
                    "destination": dest,
                    "commodity": commodity,
                    "variant": variant,
                    "distance_nm": round(dist_nm, 1),
                    "is_baseline": variant == baseline_variant(rid),
                    "red_sea_port": any(in_red_sea(ports[pid]) for pid, _w in (*load, *disc)),
                }
            )
            if main_leg is not None:
                geoms[f"{rid}|{variant}"] = cache[(main_leg[1], main_leg[2], variant)][1]
    df = pd.DataFrame(rows)
    return df, geoms


def load_ports_table(ports: dict[str, Port]) -> pd.DataFrame:
    """Tidy table of resolved ports."""
    return pd.DataFrame(
        [
            {"port_id": p.port_id, "name": p.name, "country": p.country, "lat": p.lat, "lon": p.lon}
            for p in ports.values()
        ]
    )


def run_distances() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Resolve ports, compute distances, write processed files and return (distances, ports)."""
    ports = resolve_ports(wpi.load())
    dist, geoms = build_route_distances(ports)
    dist.to_csv(PROCESSED_DIR / "route_distances.csv", index=False)
    (PROCESSED_DIR / "route_geometries.json").write_text(json.dumps(geoms))
    pt = load_ports_table(ports)
    pt.to_csv(PROCESSED_DIR / "ports.csv", index=False)
    log.info("distances: %d route-variants for %d routes", len(dist), dist["route_id"].nunique())
    return dist, pt
