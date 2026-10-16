"""Distances for every covered route, with blended ports and canal variants.

The distance of a route is the volume-weighted blend of the distances between
its loading ports and discharge ports (weights in :mod:`tmdm.reference`).
Four routing variants are computed: the shortest path, and paths avoiding the
Suez Canal, the Panama Canal, or both. Iron ore routes, and coal routes except
those from the United States, Colombia and Canada, are assumed to be carried by
Capesize tonnage that cannot use the Panama Canal, so their baseline distance
is the variant avoiding Panama. Coal from the Americas moves largely on
Panamax ships through the canal, so its baseline is the shortest path, as for
grains.

Minor bulks (an extension reported as a robustness variant) move in Handysize
and Supramax ships through many terminals. Their routes use the main dry bulk
ports of each country in the IMF PortWatch ports table, weighted by dry bulk
vessel calls, and the shortest path as baseline.
"""

from __future__ import annotations

import json
import logging
import unicodedata

import pandas as pd

from tmdm.config import PROCESSED_DIR
from tmdm.ingest import portwatch, wpi
from tmdm.reference import (
    DISCHARGE,
    IMPORTER_ORIGIN_LOAD,
    PACIFIC_DESTINATIONS,
    PANEL_COMMODITIES,
    PORTS,
    ROUTE_GROUPS,
    PortSpec,
    loading_blend,
)
from tmdm.transform.distances import RESTRICTION_SETS, Port, sea_distance_nm

log = logging.getLogger(__name__)

BASELINE_VARIANT = {"iron_ore": "avoid_panama", "coal": "avoid_panama", "grains": "shortest", "minor_bulks": "shortest"}
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
        if g.group_id.endswith("_mirror") or g.commodity not in PANEL_COMMODITIES:
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


RoutePorts = dict[str, tuple[str, str, tuple[tuple[str, float], ...], tuple[tuple[str, float], ...]]]


def build_route_distances(
    ports: dict[str, Port], pairs: RoutePorts | None = None
) -> tuple[pd.DataFrame, dict[str, list[list[float]]]]:
    """Compute blended distances per route and variant, and keep the geometry of the main leg."""
    if pairs is None:
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


# Minor bulks: main dry bulk ports per country from the PortWatch ports table.
MINOR_PORTS_PER_COUNTRY = 3
# North American ports west of this longitude serve Pacific trades.
WEST_COAST_LON = -100.0
TWO_COAST_COUNTRIES = frozenset({"USA", "CAN"})
# Origins whose cargo to North America lands on the west coast.
PACIFIC_ORIGINS = frozenset({"AUS", "IDN"})


def portwatch_blend(
    ports_db: pd.DataFrame, iso3: str, coast: str | None = None, k: int = MINOR_PORTS_PER_COUNTRY
) -> tuple[list[Port], tuple[tuple[str, float], ...]]:
    """The ``k`` ports of a country with the most dry bulk vessel calls, weighted by those calls.

    ``coast`` ("west" or "east") restricts North American countries to one seaboard.
    """
    d = ports_db[(ports_db["ISO3"] == iso3) & (ports_db["vessel_count_dry_bulk"] > 0)]
    if coast == "west":
        d = d[d["lon"] < WEST_COAST_LON]
    elif coast == "east":
        d = d[d["lon"] >= WEST_COAST_LON]
    d = d.nlargest(k, "vessel_count_dry_bulk")
    if d.empty:
        raise LookupError(f"no dry bulk port for {iso3} ({coast or 'any coast'}) in the PortWatch ports table")
    total = float(d["vessel_count_dry_bulk"].sum())
    ports = [
        Port(f"PW_{r['portid']}", str(r["portname"]), iso3, float(r["lat"]), float(r["lon"]))
        for r in d.to_dict(orient="records")
    ]
    weights = tuple((p.port_id, float(c) / total) for p, c in zip(ports, d["vessel_count_dry_bulk"], strict=True))
    return ports, weights


def minor_route_ports(routes: pd.DataFrame, ports_db: pd.DataFrame) -> tuple[dict[str, Port], RoutePorts]:
    """Loading and discharge blends for minor-bulk routes (columns route_id, origin, destination)."""
    ports: dict[str, Port] = {}
    pairs: RoutePorts = {}
    for r in routes[["route_id", "origin", "destination"]].drop_duplicates().to_dict(orient="records"):
        origin, dest = str(r["origin"]), str(r["destination"])
        o_coast = d_coast = None
        if origin in TWO_COAST_COUNTRIES:
            o_coast = "west" if dest in PACIFIC_DESTINATIONS else "east"
        if dest in TWO_COAST_COUNTRIES:
            d_coast = "west" if origin in PACIFIC_ORIGINS else "east"
        lp, load = portwatch_blend(ports_db, origin, o_coast)
        dp, disc = portwatch_blend(ports_db, dest, d_coast)
        ports.update({p.port_id: p for p in (*lp, *dp)})
        pairs[str(r["route_id"])] = (origin, dest, load, disc)
    return ports, pairs


def minor_bulk_distances(routes: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Distances per minor-bulk route and variant, and the PortWatch ports used (with weights)."""
    ports, pairs = minor_route_ports(routes, portwatch.load_ports())
    dist, _geoms = build_route_distances(ports, pairs)
    used = pd.DataFrame(
        [
            {"route_id": rid, "end": end, "port_id": pid, "port": ports[pid].name, "weight": round(w, 4)}
            for rid, (_o, _d, load, disc) in pairs.items()
            for end, blend in (("loading", load), ("discharge", disc))
            for pid, w in blend
        ]
    )
    return dist, used
