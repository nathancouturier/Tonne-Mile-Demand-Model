"""Spot-check numbers shown on the site against the raw downloads and independent recomputation.

Each check prints the site value, the value recomputed from raw data and the
relative difference. Exit status is non-zero if any check fails its tolerance.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
PROC = ROOT / "data" / "processed"
SITE = ROOT / "site" / "data"

results: list[tuple[str, float, float, float, bool]] = []


def check(name: str, site_value: float, raw_value: float, tol: float = 1e-6, abs_tol: float = 0.0) -> None:
    rel = abs(site_value - raw_value) / (abs(raw_value) if raw_value else 1.0)
    ok = rel <= tol or abs(site_value - raw_value) <= abs_tol
    results.append((name, site_value, raw_value, rel, ok))


def load(name: str) -> dict:
    return json.loads((SITE / f"{name}.json").read_text())


def main() -> int:
    summary, series, fleet, routes, pw, rolling, episodes, model = (
        load(n) for n in ("summary", "series", "fleet", "routes", "portwatch", "rolling", "episodes", "model")
    )
    # 1. Latest BDI shown on the headline equals the last value of the raw ASUB table for that month
    bdi_raw = pd.read_csv(RAW / "asub_baltic_dry_index" / "baltic_dry_index_monthly.csv").set_index("month")["bdi"]
    check(
        "headline BDI, latest panel month", summary["latest"]["bdi"], float(bdi_raw[summary["latest"]["month"]]), 1e-3
    )
    # 2. Latest fleet in the annual chart equals the UNCTAD raw World bulk carrier row
    uf = pd.read_csv(RAW / "unctadstat_maritime" / "US_MerchantFleet.csv", dtype=str)
    w = uf[(uf["Economy Label"] == "World") & (uf["ShipType Label"] == "Bulk carriers")].copy()
    w["Year"] = w["Year"].astype(int)
    last_year = int(fleet["annual"]["year"][-1])
    raw_mdwt = float(w.loc[w["Year"] == last_year, "Dead weight tons in thousands"].iloc[0]) / 1000
    check(f"fleet {last_year} (Mdwt)", fleet["annual"]["fleet_mdwt"][-1], raw_mdwt, 1e-3)
    # 3. Brazil to China iron ore tonnes over the routes window equals the Comex Stat raw sum
    cx = pd.read_csv(RAW / "brazil_comexstat" / "exports_by_country_heading.csv", dtype={"headingCode": str})
    cx["month"] = cx["year"].astype(str) + "-" + cx["monthNumber"].astype(int).astype(str).str.zfill(2)
    w0, w1 = routes["window"]
    sub = cx[(cx["headingCode"] == "2601") & (cx["country"] == "China") & (cx["month"] >= w0) & (cx["month"] <= w1)]
    raw_mt = float(sub["metricKG"].sum()) / 1e9
    r = next(x for x in routes["routes"] if x["route_id"] == "BRA-CHN-iron_ore")
    check("Brazil to China iron ore, last 12 months (Mt)", r["tonnes_last12_mt"], raw_mt, 1e-3)
    # 4. Australia to China iron ore in the routes window from Comtrade raw records
    recs = pd.concat(
        [pd.read_csv(f, dtype={"cmdCode": str, "period": str}) for f in (RAW / "un_comtrade").glob("records*.csv")]
    )
    recs["month"] = recs["period"].str[:4] + "-" + recs["period"].str[4:6]
    sub = recs[
        (recs["reporterCode"] == 36)
        & (recs["flowCode"] == "X")
        & (recs["partnerCode"] == 156)
        & (recs["cmdCode"] == "2601")
        & (recs["freqCode"] == "M")
        & (recs["month"] >= w0)
        & (recs["month"] <= w1)
    ]
    r = next(x for x in routes["routes"] if x["route_id"] == "AUS-CHN-iron_ore")
    check(
        "Australia to China iron ore, last 12 months (Mt)",
        r["tonnes_last12_mt"],
        float(sub["netWgt"].sum()) / 1e9,
        1e-3,
    )
    # 5. Route distance recomputed with the routing network from the project's own port blends
    import searoute as sr

    from tmdm.reference import DISCHARGE, loading_blend

    ports = pd.read_csv(PROC / "ports.csv").set_index("port_id")
    dist = pd.read_csv(PROC / "route_distances.csv")
    total, weight = 0.0, 0.0
    for lp, lw in loading_blend("AUS", "iron_ore", "CHN"):
        for dp, dw in DISCHARGE[("CHN", "iron_ore")]:
            o, d = ports.loc[lp], ports.loc[dp]
            rec = sr.searoute(
                [o["lon"], o["lat"]],
                [d["lon"], d["lat"]],
                units="naut",
                restrictions=["northwest", "panama"],
                append_orig_dest=True,
            )
            total += lw * dw * float(rec.properties["length"])
            weight += lw * dw
    blended = total / weight
    site_nm = float(
        dist[(dist["route_id"] == "AUS-CHN-iron_ore") & (dist["variant"] == "avoid_panama")]["distance_nm"].iloc[0]
    )
    check("Australia to China iron ore distance (nm, blended)", site_nm, blended, 1e-3)
    # 6. PortWatch latest week export tonnage equals the sum of raw daily values
    daily = pd.read_csv(RAW / "imf_portwatch" / "dry_bulk_daily_global.csv", parse_dates=["date"])
    wk = pd.Timestamp(pw["latest_week"]["week"])
    raw_week = (
        float(daily[(daily["date"] >= wk) & (daily["date"] < wk + pd.Timedelta(days=7))]["export_dry_bulk_t"].sum())
        / 1e6
    )
    check("PortWatch latest week dry bulk exports (Mt)", pw["latest_week"]["export_mt"], raw_week, 1e-3)
    # 7. Bab el-Mandeb transits per day in the last month equal raw daily mean
    ck = pd.read_csv(RAW / "imf_portwatch" / "chokepoints_daily.csv", parse_dates=["date"])
    last_m = pw["chokepoints_monthly"]["month"][-1]
    sub = ck[(ck["portname"] == "Bab el-Mandeb Strait") & (ck["date"].dt.strftime("%Y-%m") == last_m)]
    check(
        f"Bab el-Mandeb dry bulk transits per day, {last_m}",
        pw["chokepoints_monthly"]["Bab el-Mandeb Strait"][-1],
        float(sub["n_dry_bulk"].mean()),
        1e-2,
    )
    # 8. Rolling attribution components add up to the total in the latest month
    i = -1
    keys = [
        "volume",
        "distance",
        "fleet",
        "effective_supply",
        *[k for k in rolling if k.startswith("signal_")],
        "residual",
    ]
    s = sum(rolling[k][i] for k in keys)
    # each component is rounded to four decimals in the site file, so allow 0.00005 per component
    check("rolling attribution adds up (latest month)", s, rolling["total"][i], 1e-3, abs_tol=5e-5 * len(keys))
    # 9. Tonne-miles total for the latest panel month equals the sum over routes of tonnes times distance
    br = pd.read_csv(PROC / "tonne_miles_by_route.csv")
    lm = series["month"][-1]
    sub = br[br["month"] == lm]
    check(
        f"tonne-miles total {lm} (bn)",
        series["tonne_miles_bn"][-1],
        float((sub["tonnes"] * sub["distance_nm"]).sum()) / 1e9,
        1e-3,
    )
    # 10. Episode totals equal log(index_end / index_start)
    e = episodes[-1]
    check(f"episode total {e['episode_id']}", e["total"], math.log(e["index_end"] / e["index_start"]), 1e-6)
    # 11. Headline chart, latest point: the index change uses three-month averages of the raw ASUB series, and the
    #     attributed change equals e x (12-month change in log tonne-miles minus log effective fleet), recomputed
    #     from the panel
    h = model["elasticity"]["headline"]
    panel = pd.read_csv(PROC / "panel_monthly.csv").set_index("month")
    c12 = series["change12"]
    lm12 = c12["month"][-1]
    end = pd.Period(lm12, "M")
    win1 = [str(end - k) for k in range(3)]
    win0 = [str(end - 12 - k) for k in range(3)]
    raw_bdi_change = float(bdi_raw[win1].mean() / bdi_raw[win0].mean() - 1) * 100
    check(f"index change, 12 months to {lm12} (pct, 3-month averages)", c12["actual_pct"][-1], raw_bdi_change, 1e-2)

    def balance(months: list[str]) -> float:
        sub = panel.loc[months]
        return math.log(sub["tonne_miles"].mean() / (sub["fleet"].mean() * sub["supply_factor"].mean()))

    from tmdm.ingest import worldbank

    pink = worldbank.load()
    sig = {d["name"]: d for d in rolling.get("signals", [])}

    def price_change(col: str, start: pd.Period, end: pd.Period, lag: int) -> float:
        s1, e1 = start - lag, end - lag
        return math.log(pink[col].loc[e1 - 2 : e1].mean() / pink[col].loc[s1 - 2 : s1].mean())

    def signal_part(start: pd.Period, end: pd.Period) -> float:
        cols = {"oil": "brent_usd_bbl", "iron_ore": "iron_ore_usd_dmtu"}
        return sum(d["coefficient"] * price_change(cols[n], start, end, d["lag"]) for n, d in sig.items())

    physical = h["elasticity"] * (balance(win1) - balance(win0))
    if "physical_pct" in c12:
        check(
            f"physical-only change, 12 months to {lm12} (pct)",
            c12["physical_pct"][-1],
            (math.exp(physical) - 1) * 100,
            1e-2,
        )
    implied = physical + (signal_part(end - 12, end) if sig else 0.0)
    check(f"attributed change, 12 months to {lm12} (pct)", c12["implied_pct"][-1], (math.exp(implied) - 1) * 100, 1e-2)
    # 12. Explained share on the tile: total and residual recomputed from raw index averages and the panel
    la = summary["latest_attribution"]
    end = pd.Period(la["end"], "M")
    w1 = [str(end - k) for k in range(3)]
    w0 = [str(end - 12 - k) for k in range(3)]
    total = math.log(float(bdi_raw[w1].mean()) / float(bdi_raw[w0].mean()))
    resid = total - h["elasticity"] * (balance(w1) - balance(w0)) - (signal_part(end - 12, end) if sig else 0.0)
    check("latest attribution total (log points)", la["total"], total, 1e-3)
    check("latest residual after forces and signals (log points)", la["residual"], resid, 1e-3, abs_tol=1e-4)
    tile = 1 - sum(abs(v) for v in rolling["residual"]) / sum(abs(v) for v in rolling["total"])
    if model.get("signals", {}).get("fit"):
        check(
            "typical-move explained share on the tile", tile, 1 - model["signals"]["fit"]["unexplained_extended"], 1e-3
        )
    # 13. Market-signal contributions in the latest window, recomputed from the raw Pink Sheet workbook
    cols = {"oil": "brent_usd_bbl", "iron_ore": "iron_ore_usd_dmtu"}
    for name, d in sig.items():
        start, end_ = pd.Period(la["start"], "M"), pd.Period(la["end"], "M")
        check(
            f"{name} signal contribution {la['start']} to {la['end']} (log points)",
            la[f"signal_{name}"],
            d["coefficient"] * price_change(cols[name], start, end_, d["lag"]),
            1e-6,
        )
    bad = 0
    for name, sv, rv, rel, ok in results:
        print(f"{'OK  ' if ok else 'FAIL'} {name}: site={sv:,.4f} raw={rv:,.4f} rel_diff={rel:.2e}")
        bad += 0 if ok else 1
    print(f"{len(results) - bad} of {len(results)} spot checks passed")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
