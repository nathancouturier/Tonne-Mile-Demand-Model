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
    flows = pd.concat([f for f in frames if not f.empty], ignore_index=True)
    flows["month"] = pd.PeriodIndex(flows["month"], freq="M")
    flows["tonnes"] = flows["tonnes"].astype(float)
    flows = flows[flows["tonnes"] > 0].sort_values(["route_id", "month"]).reset_index(drop=True)
    return flows


def published_months() -> dict[str, set[pd.Period]]:
    """Months for which each Comtrade reporter has published data (from the availability table)."""
    av = comtrade.load_availability()
    out: dict[str, set[pd.Period]] = {}
    for code, grp in av.groupby("reporterCode"):
        iso = CODE_TO_ISO3.get(int(str(code)))
        if iso is None:
            continue
        out[iso] = {pd.Period(f"{str(p)[:4]}-{str(p)[4:6]}", freq="M") for p in grp["period"]}
    return out


def complete_months(flows: pd.DataFrame, published: dict[str, set[pd.Period]] | None = None) -> pd.DataFrame:
    """Fill route-months without a record inside each group's published range.

    Customs data omit a partner in a month with no trade, so a route-month
    without a record in a month the reporter published is a true zero. A month
    the reporter did not publish at all (listed in the availability table) is
    a gap: it is filled by linear interpolation between the neighbouring
    published months and flagged in the ``interpolated`` column so that the
    validation report and the site can list it.
    """
    out = []
    flows = flows.copy()
    flows["month"] = pd.PeriodIndex(flows["month"], freq="M")
    for gid, g in flows.groupby("group_id"):
        reporter = str(gid).split("_")[0]
        months = pd.period_range(g["month"].min(), g["month"].max(), freq="M")
        # Comex Stat returns complete months; Comtrade reporters may skip months (see availability table)
        comex = "COMEX" in str(gid)
        pub = published.get(reporter) if (published and reporter in published and not comex) else None
        routes = g[["route_id", "origin", "destination", "commodity", "source"]].drop_duplicates()
        grid = routes.merge(pd.DataFrame({"month": months}), how="cross")
        merged = grid.merge(g[["month", "route_id", "tonnes"]], on=["month", "route_id"], how="left")
        if pub is None:
            merged["tonnes"] = merged["tonnes"].fillna(0.0)
            merged["interpolated"] = False
        else:
            is_pub = merged["month"].isin(pub)
            merged.loc[is_pub, "tonnes"] = merged.loc[is_pub, "tonnes"].fillna(0.0)
            merged["interpolated"] = ~is_pub
            merged = merged.sort_values(["route_id", "month"])
            merged["tonnes"] = merged.groupby("route_id")["tonnes"].transform(
                lambda s: s.interpolate(method="linear", limit_direction="both")
            )
        merged["group_id"] = gid
        out.append(merged[[*FLOW_COLUMNS, "interpolated"]])
    return pd.concat(out, ignore_index=True).sort_values(["route_id", "month"]).reset_index(drop=True)


def interpolated_months(flows: pd.DataFrame) -> dict[str, list[str]]:
    """Unpublished months filled by interpolation, per route group."""
    if "interpolated" not in flows.columns:
        return {}
    sub = flows[flows["interpolated"]]
    return {str(g): sorted({str(m) for m in grp["month"]}) for g, grp in sub.groupby("group_id")}


def group_coverage(flows: pd.DataFrame) -> pd.DataFrame:
    """First and last month with data for each route group (panel alignment)."""
    cov = flows.groupby("group_id")["month"].agg(["min", "max", "nunique"]).reset_index()
    cov.columns = ["group_id", "first_month", "last_month", "n_months"]
    return cov
