"""Export stage: site JSON, CSV downloads and figures.

Every number shown on the site is written here from the processed files, with
its source and retrieval date carried alongside, so that the page never
computes a figure that is not also in a downloadable file.
"""

from __future__ import annotations

import json
import logging
import math
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from tmdm import __version__
from tmdm.config import PROCESSED_DIR, SITE_DATA_DIR, SITE_DIR
from tmdm.io import all_metadata, utc_now_iso
from tmdm.model.attribution import log_points_to_pct
from tmdm.reference import DISCHARGE, ECONOMY_NAMES, IMPORTER_ORIGIN_LOAD, ROUTE_GROUPS, loading_blend

log = logging.getLogger(__name__)

DOWNLOADS = {
    "panel_monthly.csv": "Monthly panel: freight index, tonnes, tonne-miles, fleet and effective supply factor",
    "route_flows_monthly.csv": "Monthly tonnes per route (origin, destination, commodity)",
    "tonne_miles_by_route.csv": "Monthly tonne-miles and shares per route",
    "tonne_miles_total.csv": "Monthly total tonnes, tonne-miles and average haul",
    "route_distances.csv": "Sea distance per route and routing variant (nautical miles)",
    "route_distance_monthly.csv": "Monthly route distance after chokepoint diversion",
    "ports.csv": "Ports used in the distance matrix (World Port Index coordinates)",
    "freight_monthly.csv": "Monthly Baltic Dry Index (ASUB)",
    "fleet_annual.csv": "World bulk carrier fleet, ships built and scrapped (UNCTAD)",
    "fleet_monthly.csv": "Monthly bulk carrier fleet (interpolated)",
    "supply_factor_monthly.csv": "Effective supply factor (at-sea share)",
    "port_time_semiannual.csv": "Median time in port, dry bulk carriers (UNCTAD)",
    "chokepoints_monthly.csv": "Monthly dry bulk transits by chokepoint (PortWatch)",
    "diversion_shares.csv": "Red Sea and Panama diversion shares",
    "portwatch_weekly_global.csv": "Weekly global dry bulk port tonnage (PortWatch)",
    "portwatch_monthly_country.csv": "Monthly dry bulk port tonnage by country (PortWatch)",
    "decomposition_rolling.csv": "Rolling 12-month decomposition of the index",
    "model_fitted.csv": "Twelve-month log changes used by the regression: actual, implied, fitted, residual",
    "haul_split_rolling.csv": "Trade-mix versus rerouting split of the haul effect",
    "validation_report.json": "Validation and reconciliation report",
    "seaborne_trade_annual.csv": "World seaborne trade loaded by cargo type (UNCTAD)",
    "signals_monthly.csv": "Monthly prices used as market signals (World Bank Pink Sheet) and OECD leading indicators",
    "model_signals.json": "Market-signal coefficients and the out-of-sample comparison with the physical model",
    "model_extensions.json": "Extensions tested against the headline: minor bulks and OECD leading indicators",
    "minor_bulks_monthly.csv": "Monthly tonnes and tonne-miles of the minor bulks (extension, not in the panel)",
    "minor_bulk_routes.csv": "Minor-bulk routes: baseline distance, canal use and tonnes over the window",
    "minor_bulk_ports.csv": "Ports and weights used for minor-bulk distances (IMF PortWatch ports table)",
}


def _read(name: str, **kw: Any) -> pd.DataFrame:
    return pd.DataFrame(pd.read_csv(PROCESSED_DIR / name, **kw))


def _f(x: object) -> float:
    """Coerce a scalar read from a frame to float."""
    return float(str(x))


def _clean(obj: Any) -> Any:
    """Replace NaN and infinities with null recursively so the JSON is strictly valid."""
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_clean(v) for v in obj]
    if isinstance(obj, float | np.floating):
        return None if not np.isfinite(obj) else float(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    if isinstance(obj, pd.Period | pd.Timestamp):
        return str(obj)
    if obj is pd.NA or obj is pd.NaT:
        return None
    return obj


def _json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(_clean(obj), separators=(",", ":"), default=_default, allow_nan=False))


def _default(o: Any) -> Any:
    if isinstance(o, pd.Period | pd.Timestamp):
        return str(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return None if np.isnan(o) else float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(f"not serialisable: {type(o)}")


def _col(df: pd.DataFrame, c: str, nd: int | None = None) -> list[Any]:
    s = df[c]
    if nd is not None:
        s = s.astype(float).round(nd)
    return [None if (isinstance(v, float) and np.isnan(v)) else v for v in s.tolist()]


def _name(iso: str) -> str:
    return ECONOMY_NAMES.get(iso, iso)


def _group_for_route(route_id: str) -> tuple[str, str]:
    origin, dest, commodity = route_id.split("-", 2)
    for g in ROUTE_GROUPS:
        if g.commodity != commodity or g.group_id.endswith("_mirror"):
            continue
        if g.flow == "X" and g.reporter.replace("_COMEX", "") == origin and dest in g.partners:
            return g.group_id, g.load_key
        if g.flow == "M" and g.reporter == dest and origin in g.partners:
            return g.group_id, IMPORTER_ORIGIN_LOAD[(g.group_id, origin)]
    raise KeyError(route_id)


GROUP_COMMODITY = {"iron_ore": "iron ore", "coal": "coal", "grains": "grain and soybean"}


def group_label(group_id: str) -> str:
    """Readable name of a route group, for example "Ukraine grain and soybean exports"."""
    g = next((x for x in ROUTE_GROUPS if x.group_id == group_id), None)
    if g is None:
        return group_id
    reporter = g.reporter.replace("_COMEX", "")
    name = ECONOMY_NAMES.get(reporter, reporter)
    flow = "exports" if g.flow == "X" else "imports"
    return f"{name} {GROUP_COMMODITY.get(g.commodity, g.commodity)} {flow}"


def export_series(panel: pd.DataFrame, bdi: pd.DataFrame, roll: pd.DataFrame) -> None:
    """Panel series for the overview chart.

    ``change12`` is the twelve-month change of the index and the part of it
    attributed to the four forces, from the rolling decomposition (endpoints
    are three-month averages), so that the headline chart, the tiles and the
    decomposition explorer show the same numbers.
    """
    _json(
        SITE_DATA_DIR / "series.json",
        {
            "month": _col(panel, "month"),
            "bdi": _col(panel, "index", 1),
            "tonnes_mt": (panel["tonnes"] / 1e6).round(3).tolist(),
            "tonne_miles_bn": (panel["tonne_miles"] / 1e9).round(3).tolist(),
            "avg_haul_nm": _col(panel, "avg_haul_nm", 1),
            "fleet_mdwt": (panel["fleet"] / 1e6).round(2).tolist(),
            "fleet_extrapolated": [bool(v) for v in panel["fleet_extrapolated"]],
            "supply_factor": _col(panel, "supply_factor", 4),
            "supply_extrapolated": [bool(v) for v in panel["supply_extrapolated"]],
            # twelve-month changes from the rolling decomposition (percent), actual and implied by the four forces
            "change12": {
                "month": _col(roll, "month"),
                "actual_pct": [round((math.exp(_f(v)) - 1) * 100, 2) for v in roll["total"]],
                "implied_pct": [
                    round((math.exp(_f(t) - _f(r)) - 1) * 100, 2)
                    for t, r in zip(roll["total"], roll["residual"], strict=True)
                ],
                "residual_lp": [round(_f(v), 4) for v in roll["residual"]],
                "physical_pct": [
                    round((math.exp(_f(t) - _f(r)) - 1) * 100, 2)
                    for t, r in zip(roll["total"], roll.get("residual_physical", roll["residual"]), strict=True)
                ],
            },
            "bdi_full": {"month": _col(bdi, "month"), "bdi": _col(bdi, "bdi", 1)},
            "prices": _signal_prices(panel),
        },
    )


def _signal_prices(panel: pd.DataFrame) -> dict[str, list[Any]]:
    """Monthly signal prices from six months before the panel to its end, for windows chosen on the site."""
    path = PROCESSED_DIR / "signals_monthly.csv"
    if not path.exists():
        return {}
    pr = pd.read_csv(path)
    first = str(pd.Period(str(panel["month"].iloc[0]), "M") - 6)
    last = str(panel["month"].iloc[-1])
    pr = pr[(pr["month"] >= first) & (pr["month"] <= last)]
    cols = [c for c in ("brent_usd_bbl", "iron_ore_usd_dmtu") if c in pr.columns]
    return {"month": pr["month"].astype(str).tolist(), **{c: [round(_f(v), 3) for v in pr[c]] for c in cols}}


def export_rolling(roll: pd.DataFrame, haul: pd.DataFrame, signals: dict[str, Any] | None = None) -> None:
    """Rolling attribution with the market signals and the mix and rerouting split."""
    h = haul.set_index("month")
    sig = signals or {}
    _json(
        SITE_DATA_DIR / "rolling.json",
        {
            "signals": [
                d | {"coefficient": sig.get("fit", {}).get("coefficients", {}).get(d["name"])}
                for d in sig.get("definitions", [])
            ],
            "month": _col(roll, "month"),
            "start": _col(roll, "start"),
            "total": _col(roll, "total", 4),
            "volume": _col(roll, "volume", 4),
            "distance": _col(roll, "distance", 4),
            "fleet": _col(roll, "fleet", 4),
            "effective_supply": _col(roll, "effective_supply", 4),
            "residual": _col(roll, "residual", 4),
            **{c: _col(roll, c, 4) for c in roll.columns if c.startswith("signal_") or c == "residual_physical"},
            # twelve-month log change of the average haul itself (not multiplied by the elasticity), split into
            # trade mix (shares shifting at fixed distances) and rerouting (distances changing at fixed shares)
            "haul_dlog": [None if m not in h.index else round(float(h.loc[m, "dlog_haul"]), 4) for m in roll["month"]],
            "haul_dlog_mix": [None if m not in h.index else round(float(h.loc[m, "mix"]), 4) for m in roll["month"]],
            "haul_dlog_rerouting": [
                None if m not in h.index else round(float(h.loc[m, "rerouting"]), 4) for m in roll["month"]
            ],
            "elasticity": _f(roll["elasticity"].iloc[0]) if len(roll) else None,
        },
    )


def _port_entry(pt: pd.DataFrame, port_id: str, weight: float) -> dict[str, Any]:
    row = pt.loc[port_id]
    return {
        "port_id": port_id,
        "name": str(row["name"]),
        "lat": _f(row["lat"]),
        "lon": _f(row["lon"]),
        "weight": weight,
    }


def export_routes(
    by_route: pd.DataFrame, distances: pd.DataFrame, canal: pd.DataFrame, ports: pd.DataFrame, geoms: dict[str, Any]
) -> dict[str, Any]:
    """Route table with latest twelve-month volumes, shares and geometries."""
    by_route["month"] = pd.PeriodIndex(by_route["month"], freq="M")
    last = by_route["month"].max()
    win = by_route[by_route["month"] > last - 12]
    agg = win.groupby("route_id").agg(
        tonnes=("tonnes", "sum"), tonne_miles=("tonne_miles", "sum"), months=("month", "nunique")
    )
    total_tm = float(agg["tonne_miles"].sum())
    total_t = float(agg["tonnes"].sum())
    base = distances[distances["is_baseline"]].set_index("route_id")
    canal_i = canal.set_index("route_id")
    pt = ports.set_index("port_id")
    routes = []
    for rid, r in agg.iterrows():
        rid = str(rid)
        origin, dest, commodity = rid.split("-", 2)
        gid, load_key = _group_for_route(rid)
        g = next(x for x in ROUTE_GROUPS if x.group_id == gid)
        load = loading_blend(origin, load_key, dest)
        disc = DISCHARGE[(dest, commodity)]
        variant = str(base.loc[rid, "variant"])
        routes.append(
            {
                "route_id": rid,
                "origin": origin,
                "origin_name": _name(origin),
                "destination": dest,
                "destination_name": _name(dest),
                "commodity": commodity,
                "distance_nm": _f(base.loc[rid, "distance_nm"]),
                "baseline_variant": variant,
                "uses_suez": bool(canal_i.loc[rid, "uses_suez"]),
                "uses_panama": bool(canal_i.loc[rid, "uses_panama"]),
                "tonnes_last12_mt": round(_f(r["tonnes"]) / 1e6, 3),
                "tonne_miles_last12_bn": round(_f(r["tonne_miles"]) / 1e9, 3),
                "share_last12": round(_f(r["tonne_miles"]) / total_tm, 5) if total_tm else None,
                "months_in_window": int(r["months"]),
                "source": "Brazil Comex Stat"
                if g.reporter == "BRA_COMEX"
                else f"UN Comtrade, reporter {g.reporter} ({'exports' if g.flow == 'X' else 'imports'})",
                "load_ports": [_port_entry(pt, p, w) for p, w in load],
                "discharge_ports": [_port_entry(pt, p, w) for p, w in disc],
                "geometry": [[round(x, 3), round(y, 3)] for x, y in geoms.get(f"{rid}|{variant}", [])],
            }
        )
    routes.sort(key=lambda d: -_f(d["tonne_miles_last12_bn"] or 0))
    # per-commodity and per-origin monthly aggregates for the routes view
    by_route["commodity"] = by_route["route_id"].str.split("-").str[2]
    by_route["origin"] = by_route["route_id"].str.split("-").str[0]
    months = sorted(by_route["month"].unique())
    com = (
        by_route.pivot_table(index="month", columns="commodity", values="tonne_miles", aggfunc="sum")
        .reindex(months)
        .fillna(0.0)
    )
    org = (
        by_route.pivot_table(index="month", columns="origin", values="tonne_miles", aggfunc="sum")
        .reindex(months)
        .fillna(0.0)
    )
    top_origins = org.sum().sort_values(ascending=False).index.tolist()
    out = {
        "window": [str(last - 11), str(last)],
        "total_tonnes_last12_mt": round(total_t / 1e6, 2),
        "total_tonne_miles_last12_bn": round(total_tm / 1e9, 2),
        "routes": routes,
        "ports": [
            {
                "port_id": str(i),
                "name": r["name"],
                "country": r["country"],
                "lat": float(r["lat"]),
                "lon": float(r["lon"]),
            }
            for i, r in pt.iterrows()
        ],
        "monthly": {
            "month": [str(m) for m in months],
            "by_commodity": {c: (com[c] / 1e9).round(3).tolist() for c in com.columns},
            "by_origin": {o: (org[o] / 1e9).round(3).tolist() for o in top_origins},
            "origin_names": {o: _name(o) for o in top_origins},
        },
    }
    _json(SITE_DATA_DIR / "routes.json", out)
    return out


def export_fleet() -> None:
    """Fleet, port time and effective supply series."""
    fa = _read("fleet_annual.csv")
    fm = _read("fleet_monthly.csv")
    sf = _read("supply_factor_monthly.csv")
    pt_path = PROCESSED_DIR / "port_time_semiannual.csv"
    pt = pd.read_csv(pt_path) if pt_path.exists() else pd.DataFrame(columns=["month", "median_port_days"])
    _json(
        SITE_DATA_DIR / "fleet.json",
        {
            "annual": {
                "year": _col(fa, "year"),
                "fleet_mdwt": (fa["fleet_dwt_thousand"] / 1000).round(2).tolist(),
                "number_of_ships": _col(fa, "number_of_ships"),
                "average_age_years": _col(fa, "average_age_years", 2),
                "fleet_growth_pct": (fa["fleet_dwt_thousand"].pct_change() * 100)
                .round(2)
                .where(lambda s: s.notna(), None)
                .tolist(),
                "built_gt_m": (fa["built_gt"] / 1e6).round(2).where(lambda s: s.notna(), None).tolist(),
                "scrapped_gt_m": (fa["scrapped_gt"] / 1e6).round(2).where(lambda s: s.notna(), None).tolist(),
                "total_fleet_mdwt": (fa["total_fleet_dwt_thousand"] / 1000)
                .round(1)
                .where(lambda s: s.notna(), None)
                .tolist(),
            },
            "monthly": {
                "month": _col(fm, "month"),
                "fleet_mdwt": (fm["fleet_dwt"] / 1e6).round(3).tolist(),
                "extrapolated": [bool(v) for v in fm["fleet_extrapolated"]],
            },
            "port_time": {
                "month": _col(pt, "month"),
                "median_port_days": _col(pt, "median_port_days", 3),
                "port_calls": _col(pt, "port_calls") if "port_calls" in pt else [],
            },
            "supply_factor": {
                "month": _col(sf, "month"),
                "supply_factor": _col(sf, "supply_factor", 4),
                "extrapolated": [bool(v) for v in sf["supply_extrapolated"]],
                "port_days_per_ship": _col(sf, "port_days_per_ship", 2),
            },
        },
    )


def export_chokepoints_and_portwatch() -> dict[str, Any]:
    """Chokepoint transits, diversion shares and PortWatch tonnage (complete months only)."""
    ck = _read("chokepoints_monthly.csv")
    # a month is complete when every day has a record (the current month is usually partial)
    ck = ck[ck["days"] >= pd.PeriodIndex(ck["month"], freq="M").days_in_month]
    names = [
        "Suez Canal",
        "Bab el-Mandeb Strait",
        "Cape of Good Hope",
        "Panama Canal",
        "Malacca Strait",
        "Bosporus Strait",
        "Gibraltar Strait",
        "Taiwan Strait",
    ]
    piv = (
        ck[ck["portname"].isin(names)]
        .pivot_table(index="month", columns="portname", values="n_dry_bulk_per_day", aggfunc="first")
        .sort_index()
    )
    div = _read("diversion_shares.csv")
    dv = div.pivot_table(index="month", columns="chokepoint", values="diversion_share", aggfunc="first").sort_index()
    wk = _read("portwatch_weekly_global.csv")
    mo = _read("portwatch_monthly_global.csv")
    ctry = _read("portwatch_monthly_country.csv")
    ctry["month"] = pd.PeriodIndex(ctry["month"], freq="M")
    last = ctry["month"].max()
    c12 = (
        ctry[ctry["month"] > last - 12]
        .groupby(["ISO3", "country"], as_index=False)
        .agg(export_mt=("export_dry_bulk_t", "sum"), import_mt=("import_dry_bulk_t", "sum"))
    )
    c12["export_mt"] = (c12["export_mt"] / 1e6).round(2)
    c12["import_mt"] = (c12["import_mt"] / 1e6).round(2)
    c12 = c12.sort_values("export_mt", ascending=False)
    latest_week = wk.iloc[-1]
    year_ago = wk[wk["week"] == str((pd.Timestamp(latest_week["week"]) - pd.Timedelta(days=364)).date())]
    out = {
        "chokepoints_monthly": {
            "month": [str(m) for m in piv.index],
            **{c: piv[c].round(2).where(piv[c].notna(), None).tolist() for c in piv.columns},
        },
        "diversion": {
            "month": [str(m) for m in dv.index],
            **{c: dv[c].round(4).where(dv[c].notna(), None).tolist() for c in dv.columns},
        },
        "weekly": {
            "week": _col(wk, "week"),
            "export_mt": (wk["export_dry_bulk_t"] / 1e6).round(3).tolist(),
            "import_mt": (wk["import_dry_bulk_t"] / 1e6).round(3).tolist(),
            "portcalls": _col(wk, "portcalls_dry_bulk"),
        },
        "monthly": {
            "month": _col(mo, "month"),
            "export_mt": (mo["export_dry_bulk_t"] / 1e6).round(3).tolist(),
            "import_mt": (mo["import_dry_bulk_t"] / 1e6).round(3).tolist(),
            "days": _col(mo, "days"),
        },
        "country_last12": {"window": [str(last - 11), str(last)], "rows": c12.head(40).to_dict(orient="records")},
        "latest_week": {
            "week": str(latest_week["week"]),
            "export_mt": round(float(latest_week["export_dry_bulk_t"]) / 1e6, 3),
            "export_mt_year_ago": round(float(year_ago["export_dry_bulk_t"].iloc[0]) / 1e6, 3)
            if len(year_ago)
            else None,
        },
    }
    _json(SITE_DATA_DIR / "portwatch.json", out)
    return out


def export_downloads() -> list[dict[str, Any]]:
    """Copy processed files into the site downloads folder and return the manifest."""
    dl = SITE_DIR / "downloads"
    dl.mkdir(parents=True, exist_ok=True)
    manifest = []
    for name, desc in DOWNLOADS.items():
        src = PROCESSED_DIR / name
        if not src.exists():
            continue
        shutil.copyfile(src, dl / name)
        manifest.append({"file": name, "description": desc, "bytes": src.stat().st_size})
    return manifest


def run() -> None:
    """Write every site data file."""
    SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    panel = _read("panel_monthly.csv")
    bdi = _read("freight_monthly.csv")
    roll = _read("decomposition_rolling.csv")
    haul = _read("haul_split_rolling.csv")
    by_route = _read("tonne_miles_by_route.csv")
    distances = _read("route_distances.csv")
    canal = _read("route_canal_use.csv")
    ports = _read("ports.csv")
    geoms = json.loads((PROCESSED_DIR / "route_geometries.json").read_text())
    episodes = json.loads((PROCESSED_DIR / "decomposition_episodes.json").read_text())
    model = json.loads((PROCESSED_DIR / "model_elasticity.json").read_text())
    diff_reg = json.loads((PROCESSED_DIR / "model_difference_regression.json").read_text())
    sig_path = PROCESSED_DIR / "model_signals.json"
    signals = json.loads(sig_path.read_text()) if sig_path.exists() else {}
    ext_path = PROCESSED_DIR / "model_extensions.json"
    extensions = json.loads(ext_path.read_text()) if ext_path.exists() else {}
    validation = json.loads((PROCESSED_DIR / "validation_report.json").read_text())
    coverage = json.loads((PROCESSED_DIR / "coverage.json").read_text())
    for g in coverage.get("route_groups", []):
        g["label"] = group_label(str(g["group_id"]))
    export_series(panel, bdi, roll)
    export_rolling(roll, haul, signals)
    routes_out = export_routes(by_route, distances, canal, ports, geoms)
    export_fleet()
    pw = export_chokepoints_and_portwatch()
    _json(SITE_DATA_DIR / "episodes.json", episodes)
    oos = {k: v for k, v in signals.get("out_of_sample", {}).items() if k != "predictions"}
    _json(
        SITE_DATA_DIR / "model.json",
        {
            "elasticity": model,
            "difference_regression": diff_reg,
            "signals": {
                "definitions": signals.get("definitions", []),
                "fit": signals.get("fit", {}),
                "out_of_sample": oos,
            },
            "extensions": extensions,
        },
    )
    _json(SITE_DATA_DIR / "validation.json", validation)
    sources = [m.__dict__ for m in all_metadata()]
    for s in sources:
        s.pop("sha256", None)
    _json(SITE_DATA_DIR / "sources.json", sources)
    manifest = export_downloads()
    last_row = panel.iloc[-1]
    latest_roll = roll.iloc[-1]
    comex_cov = next((s for s in sources if s["dataset_id"] == "brazil_comexstat"), {})
    summary = {
        "built_at": utc_now_iso(),
        "version": __version__,
        "panel": {"first": str(panel["month"].iloc[0]), "last": str(panel["month"].iloc[-1]), "n_months": len(panel)},
        "latest": {
            "month": str(last_row["month"]),
            "bdi": round(_f(last_row["index"]), 1),
            "bdi_12m_ago": round(_f(panel["index"].iloc[-13]), 1) if len(panel) > 12 else None,
            "tonnes_mt": round(_f(last_row["tonnes"]) / 1e6, 2),
            "tonne_miles_bn": round(_f(last_row["tonne_miles"]) / 1e9, 2),
            "avg_haul_nm": round(_f(last_row["avg_haul_nm"]), 0),
            "fleet_mdwt": round(_f(last_row["fleet"]) / 1e6, 1),
            "supply_factor": round(_f(last_row["supply_factor"]), 4),
        },
        "latest_attribution": {
            k: float(latest_roll[k])
            for k in (
                "total",
                "volume",
                "distance",
                "fleet",
                "effective_supply",
                "residual",
                "elasticity",
                *[c for c in roll.columns if c.startswith("signal_") or c == "residual_physical"],
            )
        }
        | {
            "start": str(latest_roll["start"]),
            "end": str(latest_roll["end"]),
            "total_pct": log_points_to_pct(float(latest_roll["total"])),
        },
        "elasticity": model["headline"],
        "episodes": [e["episode_id"] for e in episodes],
        "routes": {
            "count": len(routes_out["routes"]),
            "window": routes_out["window"],
            "total_tonnes_last12_mt": routes_out["total_tonnes_last12_mt"],
            "total_tonne_miles_last12_bn": routes_out["total_tonne_miles_last12_bn"],
        },
        "portwatch_latest_week": pw["latest_week"],
        "comex_coverage": comex_cov.get("coverage"),
        "coverage": coverage,
        "validation_summary": validation["summary"],
        "downloads": manifest,
        "sources": [
            {
                "dataset_id": s["dataset_id"],
                "title": s["title"],
                "publisher": s["publisher"],
                "retrieved_at": s["retrieved_at"],
                "source_url": s["source_url"],
            }
            for s in sources
        ],
    }
    _json(SITE_DATA_DIR / "summary.json", summary)
    log.info("export: wrote site data for panel %s to %s", summary["panel"]["first"], summary["panel"]["last"])
