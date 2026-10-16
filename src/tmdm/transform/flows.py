"""Route-level monthly cargo flows from customs statistics.

Each route group in :mod:`tmdm.reference` names the reporter whose customs
data measure the flow. Exporter-reported groups give origin = reporter and
destination = partner; importer-reported groups give the reverse. Brazilian
flows come from Comex Stat; everything else from UN Comtrade. Quantities are
net weight in kilograms converted to metric tonnes.

Some reporters publish monthly values without net weight (the United States
before 2016 and Egypt in several years). For those records the tonnage is
estimated as the reported value divided by the reporter's annual unit value
for the same partner and commodity, taken from the annual Comtrade series,
and flagged as a proxy. A route-month in which more than
:data:`ROUTE_MISSING_SHARE` of the value still has no usable weight is a gap:
it is interpolated between the route's neighbouring months, never counted as
zero. Group-months in which more than :data:`MISSING_VALUE_SHARE` of the
group's value has no usable weight are treated like unpublished months:
interpolated inside the group's range and trimmed at its ends.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from tmdm.ingest import comexstat, comtrade
from tmdm.reference import (
    CMD_TO_GROUP,
    CODE_TO_ISO3,
    COMEX_COUNTRY_TO_ISO3,
    COMMODITY_GROUPS,
    COMTRADE_CODES,
    MIRROR_ONLY,
    PANEL_COMMODITIES,
    ROUTE_GROUPS,
    RouteGroup,
)

log = logging.getLogger(__name__)

FLOW_COLUMNS = ["month", "route_id", "origin", "destination", "commodity", "tonnes", "source", "group_id"]
# extra columns carried by Comtrade flows for the quantity-quality bookkeeping
QUALITY_COLUMNS = ["proxy_tonnes", "value_usd", "value_missing_usd"]
# plausible range of customs unit values in US dollars per tonne for dry bulk commodities;
# annual weights outside it (for example tonnes labelled as kilograms) are not used
UNIT_VALUE_BOUNDS = (5.0, 3000.0)
# a group-month whose value without a usable weight exceeds this share is treated as unpublished
MISSING_VALUE_SHARE = 0.2
# a route-month whose value without a usable weight exceeds this share of the route's value is a gap
ROUTE_MISSING_SHARE = 0.01


def route_id(origin: str, destination: str, commodity: str) -> str:
    """Stable identifier for a route."""
    return f"{origin}-{destination}-{commodity}"


def annual_unit_values(records: pd.DataFrame) -> pd.DataFrame:
    """Annual unit values (US dollars per tonne) by reporter, flow, partner, commodity and year.

    Built from the annual Comtrade records, which carry net weight for
    reporters whose monthly records do not. Years whose implied unit value is
    outside :data:`UNIT_VALUE_BOUNDS` are dropped.
    """
    a = records[(records["freqCode"] == "A") & (records["partnerCode"] != 0)].copy()
    if a.empty:
        return pd.DataFrame(columns=["reporterCode", "flowCode", "partnerCode", "cmdCode", "year", "unit_value"])
    a["wgt"] = pd.to_numeric(a["netWgt"], errors="coerce")
    a["val"] = pd.to_numeric(a["primaryValue"], errors="coerce")
    a = a[(a["wgt"] > 0) & (a["val"] > 0)]
    a["unit_value"] = a["val"] / a["wgt"] * 1000.0
    a = a[(a["unit_value"] >= UNIT_VALUE_BOUNDS[0]) & (a["unit_value"] <= UNIT_VALUE_BOUNDS[1])]
    a["year"] = a["period"].astype(str).str[:4].astype(int)
    a["cmdCode"] = a["cmdCode"].astype(str)
    cols = ["reporterCode", "flowCode", "partnerCode", "cmdCode", "year", "unit_value"]
    return pd.DataFrame(a[cols].drop_duplicates(cols[:-1], keep="last").reset_index(drop=True))


def _comtrade_group_flows(
    records: pd.DataFrame, g: RouteGroup, unit_values: pd.DataFrame | None = None
) -> pd.DataFrame:
    rep = COMTRADE_CODES[g.reporter]
    cmds = set(COMMODITY_GROUPS[g.commodity])
    partners = {COMTRADE_CODES[p]: p for p in g.partners}
    sub = records[
        (records["reporterCode"] == rep)
        & (records["flowCode"] == g.flow)
        & (records["freqCode"] == "M")
        & (records["cmdCode"].astype(str).isin(cmds))
        & (records["partnerCode"].isin(partners))
    ].copy()
    if sub.empty:
        return pd.DataFrame(columns=[*FLOW_COLUMNS, *QUALITY_COLUMNS])
    sub["cmdCode"] = sub["cmdCode"].astype(str)
    wgt = pd.to_numeric(sub["netWgt"], errors="coerce")
    val = pd.to_numeric(sub["primaryValue"], errors="coerce").fillna(0.0)
    reported = wgt > 0
    sub["value_usd"] = val
    sub["tonnes"] = (wgt / 1000.0).where(reported, 0.0)
    sub["proxy_tonnes"] = 0.0
    sub["value_missing_usd"] = val.where(~reported, 0.0)
    needs_proxy = (~reported) & (val > 0)
    if needs_proxy.any() and unit_values is not None and not unit_values.empty:
        sub["year"] = sub["period"].astype(str).str[:4].astype(int)
        uv = unit_values[(unit_values["reporterCode"] == rep) & (unit_values["flowCode"] == g.flow)]
        merged = sub.merge(
            uv[["partnerCode", "cmdCode", "year", "unit_value"]], how="left", on=["partnerCode", "cmdCode", "year"]
        )
        merged.index = sub.index
        have_uv = needs_proxy & merged["unit_value"].notna()
        proxy = (val / merged["unit_value"]).where(have_uv, 0.0)
        sub["proxy_tonnes"] = proxy
        sub["tonnes"] = sub["tonnes"] + proxy
        sub.loc[have_uv, "value_missing_usd"] = 0.0
    sub["partner_iso"] = sub["partnerCode"].map(partners)
    if g.flow == "X":
        sub["origin"], sub["destination"] = g.reporter, sub["partner_iso"]
    else:
        sub["origin"], sub["destination"] = sub["partner_iso"], g.reporter
    sub["month"] = pd.PeriodIndex([f"{p[:4]}-{p[4:6]}" for p in sub["period"].astype(str)], freq="M")
    out = (
        sub.groupby(["month", "origin", "destination"], as_index=False)[["tonnes", *QUALITY_COLUMNS]]
        .sum()
        .assign(commodity=g.commodity, source=f"UN Comtrade, reporter {g.reporter} ({g.flow})", group_id=g.group_id)
    )
    out["route_id"] = [route_id(o, d, g.commodity) for o, d in zip(out["origin"], out["destination"], strict=True)]
    return pd.DataFrame(out[[*FLOW_COLUMNS, *QUALITY_COLUMNS]])


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
    out["proxy_tonnes"] = 0.0
    out["value_usd"] = pd.to_numeric(
        sub.groupby(["month", "destination"])["metricFOB"].sum(), errors="coerce"
    ).to_numpy()
    out["value_missing_usd"] = 0.0
    return pd.DataFrame(out[[*FLOW_COLUMNS, *QUALITY_COLUMNS]])


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
    uv = annual_unit_values(records)
    frames = []
    for g in ROUTE_GROUPS:
        if g.group_id in MIRROR_ONLY or g.commodity not in PANEL_COMMODITIES:
            continue
        df = _comex_group_flows(comex, g) if g.reporter == "BRA_COMEX" else _comtrade_group_flows(records, g, uv)
        n_proxy = int((df["proxy_tonnes"] > 0).sum()) if not df.empty else 0
        log.info("flows %s: %d route-months (%d with proxy tonnage)", g.group_id, len(df), n_proxy)
        frames.append(df)
    flows = pd.concat([f for f in frames if not f.empty], ignore_index=True)
    flows["month"] = pd.PeriodIndex(flows["month"], freq="M")
    for c in ["tonnes", *QUALITY_COLUMNS]:
        flows[c] = flows[c].astype(float)
    flows = (
        flows[(flows["tonnes"] > 0) | (flows["value_missing_usd"] > 0)]
        .sort_values(["route_id", "month"])
        .reset_index(drop=True)
    )
    return flows


def missing_quantity_months(flows: pd.DataFrame) -> dict[str, set[pd.Period]]:
    """Group-months whose value without a usable weight exceeds :data:`MISSING_VALUE_SHARE`."""
    if "value_missing_usd" not in flows.columns:
        return {}
    out: dict[str, set[pd.Period]] = {}
    tot = flows.groupby(["group_id", "month"], as_index=False)[["value_usd", "value_missing_usd"]].sum()
    tot["share"] = tot["value_missing_usd"] / tot["value_usd"].where(tot["value_usd"] > 0)
    for rec in tot[tot["share"] > MISSING_VALUE_SHARE].to_dict(orient="records"):
        out.setdefault(str(rec["group_id"]), set()).add(pd.Period(rec["month"], freq="M"))
    return out


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


def complete_months(
    flows: pd.DataFrame,
    published: dict[str, set[pd.Period]] | None = None,
    missing: dict[str, set[pd.Period]] | None = None,
) -> pd.DataFrame:
    """Fill route-months without a record inside each group's published range.

    Customs data omit a partner in a month with no trade, so a route-month
    without a record in a month the reporter published is a true zero. The
    following are gaps, filled by linear interpolation between the route's
    neighbouring usable months and flagged in ``interpolated`` (with the
    reason in ``fill``):

    * a month the reporter did not publish (absent from the availability
      table and without any record in the cache);
    * a group-month in which most of the value has no usable weight (see
      :func:`missing_quantity_months`);
    * a route-month in which more than :data:`ROUTE_MISSING_SHARE` of the
      route's value has no usable weight.

    Group-level gaps at the start or end of a group's range are trimmed rather
    than extrapolated. A route that has no usable month at all keeps zero
    tonnes and is flagged ``missing_no_weight``.
    """
    out = []
    flows = flows.copy()
    flows["month"] = pd.PeriodIndex(flows["month"], freq="M")
    for c in ("proxy_tonnes", "value_usd", "value_missing_usd"):
        if c not in flows.columns:
            flows[c] = 0.0
    for gid, g in flows.groupby("group_id"):
        reporter = str(gid).split("_")[0]
        gaps = set(missing.get(str(gid), set())) if missing else set()
        usable = g[~g["month"].isin(gaps) & (g["tonnes"] > 0)]
        if usable.empty:
            log.warning("flows %s: no month with usable quantities, group dropped", gid)
            continue
        months = pd.period_range(usable["month"].min(), usable["month"].max(), freq="M")
        # Comex Stat returns complete months; Comtrade reporters may skip months (see availability table).
        # A month with records in the cache counts as published even if the availability table missed it.
        comex = "COMEX" in str(gid)
        pub: set[pd.Period] | None = None
        if published and reporter in published and not comex:
            present = set(g.loc[(g["tonnes"] > 0) | (g["value_usd"] > 0), "month"])
            pub = set(published[reporter]) | present
        routes = g[["route_id", "origin", "destination", "commodity", "source"]].drop_duplicates()
        grid = routes.merge(pd.DataFrame({"month": months}), how="cross")
        cols = ["month", "route_id", "tonnes", "proxy_tonnes", "value_usd", "value_missing_usd"]
        merged = grid.merge(g[cols], on=["month", "route_id"], how="left")
        for c in ("proxy_tonnes", "value_usd", "value_missing_usd"):
            merged[c] = merged[c].fillna(0.0)
        is_pub = merged["month"].isin(pub) if pub is not None else pd.Series(True, index=merged.index)
        route_gap = merged["value_missing_usd"] > ROUTE_MISSING_SHARE * merged["value_usd"]
        is_gap = merged["month"].isin(gaps) | route_gap
        ok = is_pub & ~is_gap
        merged.loc[ok, "tonnes"] = merged.loc[ok, "tonnes"].fillna(0.0)
        merged.loc[~ok, "tonnes"] = float("nan")
        merged.loc[~ok, "proxy_tonnes"] = 0.0
        merged["interpolated"] = ~ok
        merged["fill"] = "reported"
        merged.loc[merged["proxy_tonnes"] > 0, "fill"] = "proxy_unit_value"
        merged.loc[~is_pub, "fill"] = "interpolated_unpublished"
        merged.loc[is_gap & is_pub, "fill"] = "interpolated_no_weight"
        if (~ok).any():
            merged = merged.sort_values(["route_id", "month"])
            merged["tonnes"] = merged.groupby("route_id")["tonnes"].transform(
                lambda s: s.interpolate(method="linear", limit_direction="both")
            )
        still = merged["tonnes"].isna()
        if still.any():
            log.warning(
                "flows %s: %d route-months on %d routes have no usable weight in any month",
                gid,
                int(still.sum()),
                merged.loc[still, "route_id"].nunique(),
            )
            merged.loc[still, "tonnes"] = 0.0
            merged.loc[still, "fill"] = "missing_no_weight"
        merged["group_id"] = gid
        out.append(merged[[*FLOW_COLUMNS, "proxy_tonnes", "interpolated", "fill"]])
    return pd.concat(out, ignore_index=True).sort_values(["route_id", "month"]).reset_index(drop=True)


def interpolated_months(flows: pd.DataFrame) -> dict[str, list[str]]:
    """Months filled by interpolation, per route group."""
    if "interpolated" not in flows.columns:
        return {}
    sub = flows[flows["interpolated"]]
    return {str(g): sorted({str(m) for m in grp["month"]}) for g, grp in sub.groupby("group_id")}


def fill_summary(flows: pd.DataFrame, window: tuple[pd.Period, pd.Period] | None = None) -> dict[str, dict[str, Any]]:
    """Per route group: months by fill method and the share of tonnes that is proxy or interpolated.

    A month is listed under a fill method if any route of the group has that
    method in that month. With ``window`` the summary covers only those months
    (the balanced panel window used by the model).
    """
    if "fill" not in flows.columns:
        return {}
    sub = flows
    if window is not None:
        sub = flows[(flows["month"] >= window[0]) & (flows["month"] <= window[1])]
    out: dict[str, dict[str, Any]] = {}
    for gid, grp in sub.groupby("group_id"):
        tot = float(grp["tonnes"].sum())
        months_by_fill = {
            str(f): sorted({str(m) for m in grp.loc[grp["fill"] == f, "month"]})
            for f in sorted(grp["fill"].unique())
            if str(f) != "reported"
        }
        out[str(gid)] = {
            "proxy_tonnes_share": round(float(grp["proxy_tonnes"].sum()) / tot, 4) if tot else 0.0,
            "interpolated_tonnes_share": round(float(grp.loc[grp["interpolated"], "tonnes"].sum()) / tot, 4)
            if tot
            else 0.0,
            "months": months_by_fill,
        }
    return out


def group_coverage(flows: pd.DataFrame) -> pd.DataFrame:
    """First and last month with data for each route group (panel alignment)."""
    cov = flows.groupby("group_id")["month"].agg(["min", "max", "nunique"]).reset_index()
    cov.columns = ["group_id", "first_month", "last_month", "n_months"]
    return cov
