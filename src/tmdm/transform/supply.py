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


def fleet_monthly(end_month: str) -> pd.DataFrame:
    """Monthly bulk carrier fleet (dwt) interpolated from UNCTAD annual data."""
    annual = unctad.world_bulk_fleet()
    s = pd.Series(annual["fleet_dwt_thousand"].to_numpy() * 1000.0, index=annual["year"].to_numpy())
    m = annual_to_monthly_loglinear(s, end_month)
    m = m.rename(columns={"value": "fleet_dwt", "extrapolated": "fleet_extrapolated"})
    return m


def port_time_semiannual() -> pd.DataFrame:
    """Dry bulk median time in port and number of port calls per half-year, world."""
    df = unctad._read("US_PortCalls_S.csv")
    mask = (df["Economy Label"] == "World") & df["CommercialMarket Label"].str.contains(
        "dry bulk", case=False, na=False
    )
    w = df[mask].copy()
    # Period labels look like "2018S1" or "2018-S1"; keep the year and the half.
    per = w["Period Label"] if "Period Label" in w.columns else w["Period"].astype(str)
    w["year"] = per.astype(str).str.extract(r"(\d{4})")[0].astype(int)
    w["half"] = per.astype(str).str.extract(r"S\s?(\d)")[0].astype(int)
    out = pd.DataFrame(
        {
            "year": w["year"],
            "half": w["half"],
            "median_port_days": pd.to_numeric(w["Median time in port (days)"], errors="coerce"),
        }
    )
    calls_col = next(
        (
            c
            for c in w.columns
            if c.lower().startswith("number of port calls") and "Footnote" not in c and "Missing" not in c
        ),
        None,
    )
    if calls_col:
        out["port_calls"] = pd.to_numeric(w[calls_col], errors="coerce")
    # Place each half-year observation at its middle month (March or September).
    out["month"] = pd.PeriodIndex(
        [f"{y}-{3 if h == 1 else 9:02d}" for y, h in zip(out["year"].tolist(), out["half"].tolist(), strict=True)],
        freq="M",
    )
    return out.dropna(subset=["median_port_days"]).sort_values("month").reset_index(drop=True)


def supply_factor_monthly(end_month: str, fleet_ships: pd.Series | None = None) -> pd.DataFrame:
    """Effective supply factor (at-sea share relative to the first observation)."""
    try:
        pt = port_time_semiannual()
    except (FileNotFoundError, KeyError, ValueError) as exc:
        log.warning("port time data unavailable (%s); supply factor set to 1", exc)
        months = pd.period_range("2010-01", end_month, freq="M")
        return pd.DataFrame(
            {"month": months, "supply_factor": 1.0, "supply_extrapolated": True, "port_days_per_ship": np.nan}
        )
    if "port_calls" in pt.columns and fleet_ships is not None:
        ships = fleet_ships.reindex(pt["year"]).to_numpy()
        port_days = pt["median_port_days"].to_numpy() * pt["port_calls"].to_numpy() / ships
        at_sea = 1.0 - port_days / 182.5
    else:
        # Without call counts, scale port time by the median call count of the sample
        port_days = pt["median_port_days"].to_numpy()
        at_sea = 1.0 - port_days / pt["median_port_days"].iloc[0] * 0.25
    factor = at_sea / at_sea[0]
    obs = pd.Series(factor, index=pd.PeriodIndex(pt["month"], freq="M"))
    months = pd.period_range(min(pd.Period("2010-01", freq="M"), obs.index[0]), end_month, freq="M")
    x = np.array([(m.year - obs.index[0].year) * 12 + (m.month - obs.index[0].month) for m in months], dtype=float)
    kx = np.array([(m.year - obs.index[0].year) * 12 + (m.month - obs.index[0].month) for m in obs.index], dtype=float)
    interp = np.interp(x, kx, np.log(obs.to_numpy()))
    out = pd.DataFrame({"month": months, "supply_factor": np.exp(interp)})
    out["supply_extrapolated"] = (x < kx[0]) | (x > kx[-1])
    out["port_days_per_ship"] = np.interp(x, kx, port_days, left=np.nan, right=np.nan)
    return out


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
    """Share of normal dry bulk transits lost at the Red Sea and Panama, by month."""
    rows = []
    for name, (b0, b1), label in ((RED_SEA, RED_SEA_BASELINE, "red_sea"), (PANAMA, PANAMA_BASELINE, "panama")):
        s = choke_monthly[choke_monthly["portname"] == name].set_index("month")["n_dry_bulk_per_day"]
        base = s[(s.index >= pd.Period(b0, "M")) & (s.index <= pd.Period(b1, "M"))].mean()
        share = (1.0 - s / base).clip(lower=0.0, upper=1.0)
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
