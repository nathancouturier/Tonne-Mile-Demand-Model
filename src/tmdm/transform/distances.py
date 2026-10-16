"""Sea distances between ports computed on a maritime routing network.

Distances are computed with the ``searoute`` package, which finds the shortest
path on a global maritime network graph (derived from the Eurostat/MARNET
network). The package documents that routes are realistic but not intended for
navigation; for a tonne-mile index the relevant property is that relative
distances between trades are right, which the unit tests check against
published reference distances.

For each port pair the module stores the unrestricted shortest route and,
where relevant, alternatives that avoid the Suez Canal, the Panama Canal or
both, so that chokepoint disruptions can be mapped to a change in sailing
distance.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import pandas as pd

log = logging.getLogger(__name__)

NM_PER_KM = 1 / 1.852

# searoute restriction keywords understood by the routing network.
RESTRICTION_SETS: dict[str, list[str]] = {
    "shortest": ["northwest"],
    "avoid_suez": ["northwest", "suez"],
    "avoid_panama": ["northwest", "panama"],
    "avoid_suez_panama": ["northwest", "suez", "panama"],
}


@dataclass(frozen=True)
class Port:
    """A port with WGS84 coordinates (decimal degrees)."""

    port_id: str
    name: str
    country: str
    lat: float
    lon: float


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles (used as a lower bound check)."""
    r_nm = 3440.065
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r_nm * math.asin(math.sqrt(a))


def sea_distance_nm(origin: Port, destination: Port, variant: str = "shortest") -> tuple[float, list[list[float]]]:
    """Return the sea distance in nautical miles and the route geometry.

    Args:
        origin: Loading port.
        destination: Discharge port.
        variant: One of the keys of :data:`RESTRICTION_SETS`.

    Returns:
        A tuple ``(distance_nm, coordinates)`` where ``coordinates`` is a list of
        ``[lon, lat]`` pairs describing the route.
    """
    import searoute as sr

    restrictions = RESTRICTION_SETS[variant]
    route = sr.searoute(
        [origin.lon, origin.lat],
        [destination.lon, destination.lat],
        units="naut",
        restrictions=restrictions,
        append_orig_dest=True,
    )
    dist = float(route.properties["length"])
    coords = [[float(x), float(y)] for x, y in route.geometry["coordinates"]]
    gc = haversine_nm(origin.lat, origin.lon, destination.lat, destination.lon)
    if dist < gc * 0.98:
        pair = f"{origin.port_id}-{destination.port_id}"
        raise ValueError(f"sea distance {dist:.0f} nm below great circle {gc:.0f} nm for {pair}")
    return dist, coords


def build_distance_matrix(
    ports: dict[str, Port], pairs: list[tuple[str, str]]
) -> tuple[pd.DataFrame, dict[str, list[list[float]]]]:
    """Compute every routing variant for a list of (origin, destination) port pairs.

    Returns a tidy data frame with one row per pair and variant, and a mapping of
    ``"origin|destination|variant"`` to route geometry for the map.
    """
    rows = []
    geoms: dict[str, list[list[float]]] = {}
    for o_id, d_id in pairs:
        o, d = ports[o_id], ports[d_id]
        for variant in RESTRICTION_SETS:
            try:
                dist, coords = sea_distance_nm(o, d, variant)
            except Exception as exc:  # noqa: BLE001 - a routing variant may be infeasible
                log.warning("no %s route for %s-%s: %s", variant, o_id, d_id, exc)
                continue
            rows.append(
                {
                    "origin_port": o_id,
                    "destination_port": d_id,
                    "variant": variant,
                    "distance_nm": round(dist, 1),
                    "great_circle_nm": round(haversine_nm(o.lat, o.lon, d.lat, d.lon), 1),
                }
            )
            geoms[f"{o_id}|{d_id}|{variant}"] = coords
    df = pd.DataFrame(rows)
    return df, geoms
