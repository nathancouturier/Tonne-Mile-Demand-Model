"""Fleet supply, effective supply factor and PortWatch chokepoint diversion shares.

Fleet
    World bulk carrier fleet (UNCTAD, 1 January, thousand dwt) interpolated
    log-linearly to months. Months after the last 1 January observation are
    extrapolated at the last annual growth rate and flagged.

Effective supply factor
    The share of fleet time spent at sea, derived from UNCTAD port call
    statistics: median time in port per call for dry bulk carriers multiplied
    by the number of dry bulk port calls per ship gives port days per ship;
    one minus port days over calendar days is the at-sea share. The factor is
    the at-sea share relative to its value in the base year, so a rise in
    port time (congestion) lowers effective supply. Semiannual observations
    are interpolated to months and held constant beyond the last observation
    (flagged). Speed changes are not observable from public data and remain
    in the residual.

Chokepoint diversion
    PortWatch daily dry bulk transits through Bab el-Mandeb (Red Sea) and the
    Panama Canal, aggregated to months, relative to a pre-disruption baseline.
    The diversion share (one minus the ratio, bounded between zero and one)
    is applied to routes that normally use the canal to lengthen their
    distance towards the alternative route.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from tmdm.ingest import portwatch, unctad
from tmdm.transform.fleet import annual_to_monthly_loglinear

log = logging.getLogger(__name__)

# PortWatch chokepoint names used for routing adjustments
RED_SEA = "Bab el-Mandeb Strait"
PANAMA = "Panama Canal"
SUEZ = "Suez Canal"
CAPE = "Cape of Good Hope"
RED_SEA_BASELINE = ("2023-01", "2023-10")
PANAMA_BASELINE = ("2021-01", "2022-12")
# Diversion shares are only applied from the start of each disruption; earlier months are zero
# (the Houthi attacks on shipping began in November 2023, the Panama Canal draught restrictions in 2023).
RED_SEA_DISRUPTION_START = "2023-11"
PANAMA_DISRUPTION_START = "2023-01"


def fleet_monthly(end_month: str) -> pd.DataFrame:
    """Monthly bulk carrier fleet (dwt) interpolated from UNCTAD annual data."""
    annual = unctad.world_bulk_fleet()
    s = pd.Series(annual["fleet_dwt_thousand"].to_numpy() * 1000.0, index=annual["year"].to_numpy())
    m = annual_to_monthly_loglinear(s, end_month)
    m = m.rename(columns={"value": "fleet_dwt", "extrapolated": "fleet_extrapolated"})
    return m


def port_time_semiannual() -> pd.DataFrame:
    """Dry bulk median time in port per call, world, per half-year (UNCTAD, from 2018)."""
    df = unctad._read("US_PortCalls_S.csv")
    mask = (df["Economy Label"] == "World") & df["CommercialMarket Label"].str.contains(
        "dry bulk", case=False, na=False
    )
    w = df[mask].copy()
    per = w["Period"].astype(str)
    w["year"] = per.str.extract(r"(\d{4})")[0].astype(int)
    w["half"] = per.str.extract(r"S0?(\d)")[0].astype(int)
    out = pd.DataFrame(
        {
            "year": w["year"],
            "half": w["half"],
            "median_port_days": pd.to_numeric(w["Median time in port (days)"], errors="coerce"),
        }
    )
    # Place each half-year observation at its middle month (March or September).
    out["month"] = pd.PeriodIndex(
        [f"{y}-{3 if h == 1 else 9:02d}" for y, h in zip(out["year"].tolist(), out["half"].tolist(), strict=True)],
        freq="M",
    )
    return out.dropna(subset=["median_port_days"]).sort_values("month").reset_index(drop=True)


def _interp_months(obs: pd.Series, months: pd.PeriodIndex) -> tuple[np.ndarray, np.ndarray]:
    """Linear interpolation of a Period-indexed series to months; flat outside the observed range."""
    base = obs.index[0]
    x = np.array([(m.year - base.year) * 12 + (m.month - base.month) for m in months], dtype=float)
    kx = np.array([(m.year - base.year) * 12 + (m.month - base.month) for m in obs.index], dtype=float)
    vals = np.interp(x, kx, obs.to_numpy(dtype=float))
    outside = (x < kx[0]) | (x > kx[-1])
    return vals, outside


def supply_factor_monthly(end_month: str, fleet_ships: pd.Series | None = None) -> pd.DataFrame:
    """Effective supply factor: share of fleet time at sea, relative to the first observed month.

    Port days per ship in a month are the dry bulk port calls observed by PortWatch
    divided by the number of bulk carriers (UNCTAD), multiplied by the UNCTAD
    median time in port per call. One minus port days over calendar days is the
    at-sea share. Months before the first PortWatch observation take a factor of
    one and are flagged, as are months where the median port time is held at its
    last published value.
    """
    months = pd.period_range("2000-01", end_month, freq="M")
    out = pd.DataFrame(
        {"month": months, "supply_factor": 1.0, "supply_extrapolated": True, "port_days_per_ship": np.nan}
    )
    try:
        pt = port_time_semiannual()
        calls = portwatch_monthly_global()
    except (FileNotFoundError, KeyError, ValueError) as exc:
        log.warning("port time or port call data unavailable (%s); supply factor set to 1", exc)
        return out
    if fleet_ships is None or pt.empty or calls.empty:
        log.warning("fleet count missing; supply factor set to 1")
        return out
    calls = calls[calls["days"] >= 28].copy()
    calls["month"] = pd.PeriodIndex(calls["month"], freq="M")
    calls_by_month: dict[pd.Period, float] = dict(
        zip(calls["month"].tolist(), calls["portcalls_dry_bulk"].astype(float).tolist(), strict=True)
    )
    ships_df = annual_to_monthly_loglinear(fleet_ships.dropna(), end_month)
    ships_by_month: dict[pd.Period, float] = dict(
        zip(ships_df["month"].tolist(), ships_df["value"].astype(float).tolist(), strict=True)
    )
    med_obs = pd.Series(pt["median_port_days"].to_numpy(), index=pd.PeriodIndex(pt["month"], freq="M"))
    med_vals, med_outside = _interp_months(med_obs, months)
    rows = []
    for i, m in enumerate(months):
        if m not in calls_by_month or m not in ships_by_month:
            continue
        port_days = calls_by_month[m] / ships_by_month[m] * float(med_vals[i])
        at_sea = 1.0 - port_days / m.days_in_month
        rows.append((m, port_days, at_sea, bool(med_outside[i])))
    if not rows:
        return out
    obs = pd.DataFrame(rows, columns=["month", "port_days_per_ship", "at_sea_share", "median_held"]).set_index("month")
    base_val = float(obs["at_sea_share"].iloc[0])
    out = out.set_index("month")
    out.loc[obs.index, "supply_factor"] = (obs["at_sea_share"] / base_val).to_numpy()
    out.loc[obs.index, "port_days_per_ship"] = obs["port_days_per_ship"].to_numpy()
    out.loc[obs.index, "supply_extrapolated"] = obs["median_held"].to_numpy()
    out["at_sea_share"] = np.nan
    out.loc[obs.index, "at_sea_share"] = obs["at_sea_share"].to_numpy()
    return out.reset_index()


def chokepoint_monthly() -> pd.DataFrame:
    """Monthly dry bulk transits by chokepoint (sum of daily counts and capacity)."""
    ck = portwatch.load_chokepoints_daily()
    ck["month"] = pd.PeriodIndex(pd.to_datetime(ck["date"]), freq="M")
    out = (
        ck.groupby(["month", "portname"], as_index=False)
        .agg(n_dry_bulk=("n_dry_bulk", "sum"), capacity_dry_bulk=("capacity_dry_bulk", "sum"), days=("date", "nunique"))
        .sort_values(["portname", "month"])
    )
    out["n_dry_bulk_per_day"] = out["n_dry_bulk"] / out["days"]
    return out


def diversion_shares(choke_monthly: pd.DataFrame) -> pd.DataFrame:
    """Share of normal dry bulk transits lost at the Red Sea and Panama, by month.

    The share is one minus transits relative to the pre-disruption baseline,
    clipped to [0, 1], and set to zero for months before the disruption
    started so that ordinary variation in earlier years does not lengthen
    routes.
    """
    rows = []
    for name, (b0, b1), start, label in (
        (RED_SEA, RED_SEA_BASELINE, RED_SEA_DISRUPTION_START, "red_sea"),
        (PANAMA, PANAMA_BASELINE, PANAMA_DISRUPTION_START, "panama"),
    ):
        s = choke_monthly[choke_monthly["portname"] == name].set_index("month")["n_dry_bulk_per_day"]
        base = s[(s.index >= pd.Period(b0, "M")) & (s.index <= pd.Period(b1, "M"))].mean()
        share = (1.0 - s / base).clip(lower=0.0, upper=1.0)
        share[s.index < pd.Period(start, "M")] = 0.0
        rows.append(
            pd.DataFrame(
                {
                    "month": s.index,
                    "chokepoint": label,
                    "transits_per_day": s.to_numpy(),
                    "baseline_per_day": base,
                    "diversion_share": share.to_numpy(),
                }
            )
        )
    return pd.concat(rows, ignore_index=True)


def portwatch_monthly_global() -> pd.DataFrame:
    """Global dry bulk export and import tonnage by month from PortWatch daily totals."""
    d = portwatch.load_daily_global()
    d["month"] = pd.PeriodIndex(pd.to_datetime(d["date"]), freq="M")
    g = d.groupby("month", as_index=False).agg(
        export_dry_bulk_t=("export_dry_bulk_t", "sum"),
        import_dry_bulk_t=("import_dry_bulk_t", "sum"),
        portcalls_dry_bulk=("portcalls_dry_bulk", "sum"),
        days=("date", "nunique"),
    )
    return g


def portwatch_weekly_global() -> pd.DataFrame:
    """Weekly (Monday-start) global dry bulk tonnage, complete weeks only."""
    d = portwatch.load_daily_global()
    d["date"] = pd.to_datetime(d["date"])
    d["week"] = d["date"].dt.to_period("W-SUN").dt.start_time
    g = d.groupby("week", as_index=False).agg(
        export_dry_bulk_t=("export_dry_bulk_t", "sum"),
        import_dry_bulk_t=("import_dry_bulk_t", "sum"),
        portcalls_dry_bulk=("portcalls_dry_bulk", "sum"),
        days=("date", "nunique"),
    )
    return g[g["days"] == 7].reset_index(drop=True)
