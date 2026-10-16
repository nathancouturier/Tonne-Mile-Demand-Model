"""Transform stage: build every processed series from the raw downloads.

Outputs (all under ``data/processed``):

* ``freight_monthly.csv``: monthly Baltic Dry Index.
* ``route_flows_monthly.csv``: tonnes per route and month.
* ``route_distances.csv``, ``route_geometries.json``, ``ports.csv``: distance matrix.
* ``route_distance_monthly.csv``: route distances after chokepoint diversion.
* ``tonne_miles_by_route.csv``, ``tonne_miles_total.csv``: tonne-mile aggregates.
* ``fleet_monthly.csv``, ``fleet_annual.csv``, ``port_time_semiannual.csv``, ``supply_factor_monthly.csv``.
* ``chokepoints_monthly.csv``, ``diversion_shares.csv``.
* ``portwatch_monthly_global.csv``, ``portwatch_weekly_global.csv``, ``portwatch_monthly_country.csv``.
* ``panel_monthly.csv``: the balanced monthly panel used by the model.
* ``coverage.json``: data coverage per input and the resulting panel window.
"""

from __future__ import annotations

import json
import logging

import pandas as pd

from tmdm.config import PROCESSED_DIR
from tmdm.ingest import portwatch, unctad
from tmdm.transform import flows as flows_mod
from tmdm.transform import freight, route_distances, supply
from tmdm.transform.tonne_miles import aggregate_tonne_miles

log = logging.getLogger(__name__)

# Routes whose shortest path uses a canal are detected from the variants:
# the avoiding variant must be at least this much longer than the shortest.
CANAL_DETECTION_RATIO = 1.02


def canal_users(distances: pd.DataFrame) -> pd.DataFrame:
    """Flag per route whether the baseline path uses the Suez or Panama canal, with the alternative distance.

    Canal use is judged against the route's baseline variant: for Capesize
    trades (baseline avoids Panama) the Suez alternative is the variant that
    avoids both canals; for the others it is the plain Suez-avoiding variant.
    Routes serving a port inside the Red Sea cannot avoid Bab el-Mandeb by
    sailing round the Cape, so they are never diverted.
    """
    from tmdm.transform.route_distances import baseline_variant

    piv = distances.pivot_table(index="route_id", columns="variant", values="distance_nm", aggfunc="first")
    red_sea = (
        distances.groupby("route_id")["red_sea_port"].any()
        if "red_sea_port" in distances.columns
        else pd.Series(False, index=piv.index)
    )
    rows = []
    for rid in piv.index:
        base_variant = baseline_variant(str(rid))
        base = float(piv.loc[rid, base_variant])
        if base_variant == "avoid_panama":
            suez_alt = float(piv.loc[rid, "avoid_suez_panama"])
            panama_alt = float("nan")
            uses_panama = False
        else:
            suez_alt = float(piv.loc[rid, "avoid_suez"])
            panama_alt = float(piv.loc[rid, "avoid_panama"])
            uses_panama = bool(panama_alt / base > CANAL_DETECTION_RATIO)
        uses_suez = bool(suez_alt / base > CANAL_DETECTION_RATIO) and not bool(red_sea.get(rid, False))
        rows.append(
            {
                "route_id": rid,
                "baseline_nm": base,
                "baseline_variant": base_variant,
                "uses_suez": uses_suez,
                "uses_panama": uses_panama,
                "red_sea_port": bool(red_sea.get(rid, False)),
                "suez_alt_nm": suez_alt if uses_suez else float("nan"),
                "panama_alt_nm": panama_alt if uses_panama else float("nan"),
            }
        )
    return pd.DataFrame(rows)


def monthly_route_distances(canal: pd.DataFrame, diversion: pd.DataFrame, months: pd.PeriodIndex) -> pd.DataFrame:
    """Expand route distances to months, lengthening canal routes by the diversion share."""
    sub = diversion[diversion["chokepoint"] == "red_sea"]
    red: dict[pd.Period, float] = dict(zip(sub["month"], sub["diversion_share"], strict=True))
    sub = diversion[diversion["chokepoint"] == "panama"]
    pan: dict[pd.Period, float] = dict(zip(sub["month"], sub["diversion_share"], strict=True))
    rows = []
    for rec in canal.to_dict(orient="records"):
        rid = str(rec["route_id"])
        base = float(rec["baseline_nm"])
        suez_alt = float(rec["suez_alt_nm"]) if pd.notna(rec["suez_alt_nm"]) else None
        panama_alt = float(rec["panama_alt_nm"]) if pd.notna(rec["panama_alt_nm"]) else None
        for m in months:
            d = base
            if bool(rec["uses_suez"]) and m in red and suez_alt is not None:
                d = (1 - red[m]) * base + red[m] * suez_alt
            elif bool(rec["uses_panama"]) and m in pan and panama_alt is not None:
                d = (1 - pan[m]) * base + pan[m] * panama_alt
            rows.append((m, rid, d, base))
    out = pd.DataFrame(rows, columns=["month", "route_id", "distance_nm", "baseline_nm"])
    return out


def run() -> None:
    """Build all processed series."""
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    # Freight index
    bdi = freight.monthly_bdi()
    bdi.to_csv(PROCESSED_DIR / "freight_monthly.csv", index=False)
    # Route flows
    raw_flows = flows_mod.build_route_flows()
    missing = flows_mod.missing_quantity_months(raw_flows)
    flows = flows_mod.complete_months(raw_flows, flows_mod.published_months(), missing)
    flows.to_csv(PROCESSED_DIR / "route_flows_monthly.csv", index=False)
    coverage = flows_mod.group_coverage(flows)
    coverage.to_csv(PROCESSED_DIR / "group_coverage.csv", index=False)
    # Distances
    distances, _ports = route_distances.run_distances()
    canal = canal_users(distances)
    canal.to_csv(PROCESSED_DIR / "route_canal_use.csv", index=False)
    # PortWatch aggregates and chokepoints
    ck_month = supply.chokepoint_monthly()
    ck_month.to_csv(PROCESSED_DIR / "chokepoints_monthly.csv", index=False)
    div = supply.diversion_shares(ck_month)
    div.to_csv(PROCESSED_DIR / "diversion_shares.csv", index=False)
    supply.portwatch_monthly_global().to_csv(PROCESSED_DIR / "portwatch_monthly_global.csv", index=False)
    supply.portwatch_weekly_global().to_csv(PROCESSED_DIR / "portwatch_weekly_global.csv", index=False)
    portwatch.load_monthly_by_country().to_csv(PROCESSED_DIR / "portwatch_monthly_country.csv", index=False)
    # Monthly distances and tonne-miles
    months = pd.period_range(flows["month"].min(), flows["month"].max(), freq="M")
    dist_m = monthly_route_distances(canal, div, months)
    dist_m.to_csv(PROCESSED_DIR / "route_distance_monthly.csv", index=False)
    # balanced window: months covered by every route group
    first = coverage["first_month"].max()
    last = coverage["last_month"].min()
    balanced = flows[(flows["month"] >= first) & (flows["month"] <= last)]
    by_route, totals = aggregate_tonne_miles(balanced, dist_m)
    by_route.to_csv(PROCESSED_DIR / "tonne_miles_by_route.csv", index=False)
    totals.to_csv(PROCESSED_DIR / "tonne_miles_total.csv", index=False)
    # Fleet and effective supply
    end_month = str(max(last, bdi["month"].max()))
    fleet_m = supply.fleet_monthly(end_month)
    fleet_m.to_csv(PROCESSED_DIR / "fleet_monthly.csv", index=False)
    fleet_a = unctad.world_bulk_fleet().merge(unctad.world_built_and_scrapped(), on="year", how="left")
    fleet_a.to_csv(PROCESSED_DIR / "fleet_annual.csv", index=False)
    ships = pd.Series(fleet_a["number_of_ships"].to_numpy(dtype=float), index=fleet_a["year"].to_numpy())
    try:
        pt = supply.port_time_semiannual()
        pt.to_csv(PROCESSED_DIR / "port_time_semiannual.csv", index=False)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        log.warning("port time table not available: %s", exc)
    sf = supply.supply_factor_monthly(end_month, ships)
    sf.to_csv(PROCESSED_DIR / "supply_factor_monthly.csv", index=False)
    unctad.world_seaborne_trade().to_csv(PROCESSED_DIR / "seaborne_trade_annual.csv", index=False)
    # Panel
    panel = (
        totals.merge(bdi.rename(columns={"bdi": "index"}), on="month", how="inner")
        .merge(fleet_m[["month", "fleet_dwt", "fleet_extrapolated"]], on="month", how="inner")
        .merge(sf[["month", "supply_factor", "supply_extrapolated"]], on="month", how="inner")
        .rename(columns={"fleet_dwt": "fleet"})
        .sort_values("month")
    )
    panel = panel.dropna(subset=["index", "tonnes", "tonne_miles", "fleet", "supply_factor"])
    panel.to_csv(PROCESSED_DIR / "panel_monthly.csv", index=False)
    cov = {
        "flows_balanced_window": [str(first), str(last)],
        "freight_index_window": [str(bdi["month"].min()), str(bdi["month"].max())],
        "panel_window": [str(panel["month"].min()), str(panel["month"].max())],
        "panel_months": len(panel),
        "route_groups": coverage.assign(
            first_month=coverage["first_month"].astype(str), last_month=coverage["last_month"].astype(str)
        ).to_dict(orient="records"),
        "routes": int(flows["route_id"].nunique()),
        "interpolated_months": flows_mod.interpolated_months(flows),
        "fill_summary": flows_mod.fill_summary(flows),
    }
    (PROCESSED_DIR / "coverage.json").write_text(json.dumps(cov, indent=2))
    log.info(
        "panel: %s to %s (%d months, %d routes)", panel["month"].min(), panel["month"].max(), len(panel), cov["routes"]
    )
