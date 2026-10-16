"""Route-level monthly cargo flows from customs statistics.

Each route group in :mod:`tmdm.reference` names the reporter whose customs
data measure the flow. Exporter-reported groups give origin = reporter and
destination = partner; importer-reported groups give the reverse. Brazilian
flows come from Comex Stat; everything else from UN Comtrade. Quantities are
net weight in kilograms converted to metric tonnes.
"""

from __future__ import annotations

import logging

import pandas as pd

from tmdm.ingest import comexstat, comtrade
from tmdm.reference import (
    CMD_TO_GROUP,
    CODE_TO_ISO3,
    COMEX_COUNTRY_TO_ISO3,
    COMMODITY_GROUPS,
    COMTRADE_CODES,
    MIRROR_ONLY,
    ROUTE_GROUPS,
    RouteGroup,
)

log = logging.getLogger(__name__)

FLOW_COLUMNS = ["month", "route_id", "origin", "destination", "commodity", "tonnes", "source", "group_id"]


def route_id(origin: str, destination: str, commodity: str) -> str:
    """Stable identifier for a route."""
    return f"{origin}-{destination}-{commodity}"


def _comtrade_group_flows(records: pd.DataFrame, g: RouteGroup) -> pd.DataFrame:
    rep = COMTRADE_CODES[g.reporter]
    cmds = set(COMMODITY_GROUPS[g.commodity])
    partners = {COMTRADE_CODES[p]: p for p in g.partners}
    sub = records[
        (records["reporterCode"] == rep)
        & (records["flowCode"] == g.flow)
        & (records["freqCode"] == "M")
        & (records["cmdCode"].isin(cmds))
        & (records["partnerCode"].isin(partners))
    ].copy()
    if sub.empty:
        return pd.DataFrame(columns=FLOW_COLUMNS)
    sub["tonnes"] = pd.to_numeric(sub["netWgt"], errors="coerce").fillna(0.0) / 1000.0
    sub["partner_iso"] = sub["partnerCode"].map(partners)
    if g.flow == "X":
        sub["origin"], sub["destination"] = g.reporter, sub["partner_iso"]
    else:
        sub["origin"], sub["destination"] = sub["partner_iso"], g.reporter
    sub["month"] = pd.PeriodIndex([f"{p[:4]}-{p[4:6]}" for p in sub["period"].astype(str)], freq="M")
    out = (
        sub.groupby(["month", "origin", "destination"], as_index=False)["tonnes"]
        .sum()
        .assign(commodity=g.commodity, source=f"UN Comtrade, reporter {g.reporter} ({g.flow})", group_id=g.group_id)
    )
    out["route_id"] = [route_id(o, d, g.commodity) for o, d in zip(out["origin"], out["destination"], strict=True)]
    return pd.DataFrame(out[FLOW_COLUMNS])


def _comex_group_flows(comex: pd.DataFrame, g: RouteGroup) -> pd.DataFrame:
    cmds = set(COMMODITY_GROUPS[g.commodity])
    sub = comex[comex["headingCode"].astype(str).isin(cmds)].copy()
    sub["destination"] = sub["country"].map(COMEX_COUNTRY_TO_ISO3)
    unmapped = sorted(sub.loc[sub["destination"].isna(), "country"].unique())
    if unmapped:
        log.debug("comex: %d destination names not mapped (not covered routes)", len(unmapped))
    sub = sub[sub["destination"].isin(g.partners)]
    if sub.empty:
        return pd.DataFrame(columns=FLOW_COLUMNS)
    sub["tonnes"] = pd.to_numeric(sub["metricKG"], errors="coerce").fillna(0.0) / 1000.0
    out = sub.groupby(["month", "destination"], as_index=False)["tonnes"].sum()
    out["origin"] = "BRA"
    out["commodity"] = g.commodity
    out["source"] = "Brazil Comex Stat (exports)"
    out["group_id"] = g.group_id
    out["route_id"] = [route_id("BRA", d, g.commodity) for d in out["destination"]]
    return pd.DataFrame(out[FLOW_COLUMNS])


def reporter_world_totals(records: pd.DataFrame) -> pd.DataFrame:
    """Monthly reporter totals (partner World) per commodity group, for coverage shares."""
    sub = records[(records["partnerCode"] == 0) & (records["freqCode"] == "M")].copy()
    sub["commodity"] = sub["cmdCode"].astype(str).map(CMD_TO_GROUP)
    sub = sub.dropna(subset=["commodity"])
    sub["tonnes"] = pd.to_numeric(sub["netWgt"], errors="coerce").fillna(0.0) / 1000.0
    sub["reporter"] = sub["reporterCode"].map(CODE_TO_ISO3)
    sub["month"] = pd.PeriodIndex([f"{p[:4]}-{p[4:6]}" for p in sub["period"].astype(str)], freq="M")
    return pd.DataFrame(sub.groupby(["month", "reporter", "flowCode", "commodity"], as_index=False)["tonnes"].sum())


def build_route_flows() -> pd.DataFrame:
    """Assemble monthly tonnes for every covered route."""
    records = comtrade.load_records()
    comex = comexstat.load()
    frames = []
    for g in ROUTE_GROUPS:
        if g.group_id in MIRROR_ONLY:
            continue
        df = _comex_group_flows(comex, g) if g.reporter == "BRA_COMEX" else _comtrade_group_flows(records, g)
        log.info("flows %s: %d route-months", g.group_id, len(df))
        frames.append(df)
    flows = pd.concat(frames, ignore_index=True)
    flows = flows[flows["tonnes"] > 0].sort_values(["route_id", "month"]).reset_index(drop=True)
    return flows


def complete_months(flows: pd.DataFrame) -> pd.DataFrame:
    """Fill route-months without a record with zero tonnes inside each group's reported range.

    Customs data omit a partner in a month with no trade, so a missing
    route-month inside the reporter's published range is a true zero, not a
    gap. Months outside the reporter's published range stay missing.
    """
    out = []
    for gid, g in flows.groupby("group_id"):
        months = pd.period_range(g["month"].min(), g["month"].max(), freq="M")
        routes = g[["route_id", "origin", "destination", "commodity", "source"]].drop_duplicates()
        grid = routes.merge(pd.DataFrame({"month": months}), how="cross")
        merged = grid.merge(g[["month", "route_id", "tonnes"]], on=["month", "route_id"], how="left")
        merged["tonnes"] = merged["tonnes"].fillna(0.0)
        merged["group_id"] = gid
        out.append(merged[FLOW_COLUMNS])
    return pd.concat(out, ignore_index=True).sort_values(["route_id", "month"]).reset_index(drop=True)


def group_coverage(flows: pd.DataFrame) -> pd.DataFrame:
    """First and last month with data for each route group (panel alignment)."""
    cov = flows.groupby("group_id")["month"].agg(["min", "max", "nunique"]).reset_index()
    cov.columns = ["group_id", "first_month", "last_month", "n_months"]
    return cov
