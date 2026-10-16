"""Route-level tonne-mile construction and the volume/distance split.

Tonne-miles on a route are tonnes shipped multiplied by the sailing distance
in nautical miles. Summing over routes gives total tonne-mile demand for the
covered trades. Average haul is total tonne-miles divided by total tonnes, so
that by construction

    dlog(tonne-miles) = dlog(tonnes) + dlog(average haul).

The average haul effect is split further into a trade-mix effect (volume
shares shifting towards longer or shorter routes, at base-period distances)
and a rerouting effect (route distances changing, at end-period shares).
The two pieces sum exactly to the change in average haul.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

REQUIRED_FLOW_COLUMNS = {"month", "route_id", "tonnes"}


def aggregate_tonne_miles(flows: pd.DataFrame, distances: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compute route and total tonne-miles per month.

    Args:
        flows: Tidy frame with columns ``month`` (period or timestamp),
            ``route_id`` and ``tonnes`` (metric tonnes).
        distances: Frame with ``route_id`` and ``distance_nm``; optionally a
            ``month`` column for time-varying distances (rerouting). If a
            ``month`` column is present, every (month, route_id) in ``flows``
            must have a distance.

    Returns:
        ``(by_route, totals)``. ``by_route`` has one row per month and route
        with tonnes, distance, tonne_miles and the route share of total
        tonne-miles. ``totals`` has one row per month with tonnes,
        tonne_miles and avg_haul_nm.
    """
    missing = REQUIRED_FLOW_COLUMNS - set(flows.columns)
    if missing:
        raise ValueError(f"flows is missing columns {sorted(missing)}")
    if (flows["tonnes"] < 0).any():
        raise ValueError("negative tonnes in flows")
    if "month" in distances.columns:
        merged = flows.merge(distances[["month", "route_id", "distance_nm"]], on=["month", "route_id"], how="left")
    else:
        merged = flows.merge(distances[["route_id", "distance_nm"]], on="route_id", how="left")
    if merged["distance_nm"].isna().any():
        bad = merged.loc[merged["distance_nm"].isna(), "route_id"].unique().tolist()
        raise ValueError(f"no distance for routes {bad}")
    merged["tonne_miles"] = merged["tonnes"] * merged["distance_nm"]
    totals = (
        merged.groupby("month", as_index=False)
        .agg(tonnes=("tonnes", "sum"), tonne_miles=("tonne_miles", "sum"))
        .sort_values("month")
        .reset_index(drop=True)
    )
    totals["avg_haul_nm"] = totals["tonne_miles"] / totals["tonnes"].where(totals["tonnes"] > 0)
    merged = merged.merge(totals[["month", "tonne_miles"]].rename(columns={"tonne_miles": "_tot"}), on="month")
    merged["share"] = merged["tonne_miles"] / merged["_tot"].where(merged["_tot"] > 0)
    by_route = merged.drop(columns="_tot").sort_values(["month", "route_id"]).reset_index(drop=True)
    return by_route, totals


def haul_split(
    tonnes_0: pd.Series,
    tonnes_1: pd.Series,
    dist_0: pd.Series,
    dist_1: pd.Series,
) -> tuple[float, float, float]:
    """Split the log change in average haul into mix and rerouting effects.

    All series are indexed by route_id. Returns ``(dlog_haul, mix, rerouting)``
    where ``mix + rerouting == dlog_haul`` exactly.
    """
    idx = tonnes_0.index.union(tonnes_1.index)
    t0 = tonnes_0.reindex(idx).fillna(0.0)
    t1 = tonnes_1.reindex(idx).fillna(0.0)
    d0 = dist_0.reindex(idx)
    d1 = dist_1.reindex(idx)
    d0 = d0.fillna(d1)
    d1 = d1.fillna(d0)
    if d0.isna().any():
        raise ValueError("distance missing for a route in both periods")
    s0 = t0 / t0.sum()
    s1 = t1 / t1.sum()
    haul_0 = float((s0 * d0).sum())
    haul_mix = float((s1 * d0).sum())
    haul_1 = float((s1 * d1).sum())
    dlog = float(np.log(haul_1 / haul_0))
    mix = float(np.log(haul_mix / haul_0))
    reroute = float(np.log(haul_1 / haul_mix))
    return dlog, mix, reroute
