"""Validate stage: data quality checks and cross-source reconciliation.

Checks run on the raw downloads and their immediate aggregates and are
written to ``data/processed/validation_report.json`` so that the site and
the research note can show them. A failing check does not stop the pipeline;
it is reported.
"""

from __future__ import annotations

import json
import logging

import numpy as np
import pandas as pd

from tmdm.config import PROCESSED_DIR, RAW_DIR
from tmdm.ingest import asub_bdi, comexstat, comtrade, comtrade_long, portwatch, unctad
from tmdm.io import all_metadata, rehash_sharded, sha256_of
from tmdm.reference import CODE_TO_ISO3, COMEX_COUNTRY_TO_ISO3, COMMODITY_GROUPS, COMTRADE_CODES, ROUTE_GROUPS
from tmdm.validate.checks import (
    CheckResult,
    check_missing,
    check_monthly_continuity,
    check_outliers,
    check_range,
    check_reconciliation,
)

log = logging.getLogger(__name__)


def _comtrade_series(records: pd.DataFrame, reporter: str, flow: str, partner: str, cmds: set[str]) -> pd.Series:
    sub = records[
        (records["reporterCode"] == COMTRADE_CODES[reporter])
        & (records["flowCode"] == flow)
        & (records["freqCode"] == "M")
        & (records["partnerCode"] == COMTRADE_CODES[partner])
        & (records["cmdCode"].astype(str).isin(cmds))
    ]
    s = sub.groupby("period")["netWgt"].sum() / 1000.0
    s.index = pd.PeriodIndex([f"{p[:4]}-{p[4:6]}" for p in s.index.astype(str)], freq="M")
    return s.sort_index()


def _comex_series(comex: pd.DataFrame, partner_iso: str, headings: set[str]) -> pd.Series:
    names = {k for k, v in COMEX_COUNTRY_TO_ISO3.items() if v == partner_iso}
    sub = comex[comex["country"].isin(names) & comex["headingCode"].astype(str).isin(headings)]
    s = sub.groupby("month")["metricKG"].sum() / 1000.0
    return s.sort_index()


def freight_checks() -> list[CheckResult]:
    """Checks on the monthly BDI."""
    bdi = asub_bdi.load()
    return [
        check_missing("asub_baltic_dry_index", bdi, ["bdi"]),
        check_monthly_continuity("asub_baltic_dry_index", bdi["month"]),
        check_range("asub_baltic_dry_index", bdi["bdi"], "bdi_index_points", 200, 15000),
        check_outliers("asub_baltic_dry_index", bdi["bdi"], "bdi"),
    ]


def comtrade_checks(records: pd.DataFrame) -> list[CheckResult]:
    """Structural checks on the Comtrade cache."""
    out = [check_missing("un_comtrade", records, ["netWgt", "period", "partnerCode"])]
    est = records["isNetWgtEstimated"].astype(str).str.lower().eq("true").mean() if len(records) else 0.0
    out.append(
        CheckResult(
            "un_comtrade",
            "estimated_weights_share",
            "pass" if est < 0.05 else "warn",
            f"{est:.1%} of records carry an estimated net weight",
            {"share_estimated": float(est), "rows": len(records)},
        )
    )
    monthly = records[records["freqCode"] == "M"]
    for spec_id, g in monthly.groupby("spec_id"):
        out.append(
            check_monthly_continuity(
                f"un_comtrade:{spec_id}", pd.Series([f"{p[:4]}-{p[4:6]}" for p in g["period"].astype(str).unique()])
            )
        )
    out.append(check_weightless_values(monthly))
    out.append(check_annual_unit_values(records))
    return out


def check_weightless_values(monthly: pd.DataFrame) -> CheckResult:
    """Share of monthly bilateral trade value reported without a net weight, by reporter."""
    # only reporters whose records feed the route panel (older cached reporters are ignored)
    used = {COMTRADE_CODES[g.reporter] for g in ROUTE_GROUPS if g.reporter in COMTRADE_CODES}
    sub = monthly[(monthly["partnerCode"] != 0) & (monthly["reporterCode"].isin(used))].copy()
    if sub.empty:
        return CheckResult("un_comtrade", "weightless_values", "warn", "no monthly bilateral records", {})
    sub["val"] = pd.to_numeric(sub["primaryValue"], errors="coerce").fillna(0.0)
    sub["noweight"] = ~(pd.to_numeric(sub["netWgt"], errors="coerce") > 0)
    sub["reporter"] = sub["reporterCode"].map(CODE_TO_ISO3).fillna(sub["reporterCode"].astype(str))
    sub["year"] = sub["period"].astype(str).str[:4]
    by_rep = sub.groupby("reporter").apply(lambda d: d.loc[d["noweight"], "val"].sum() / max(d["val"].sum(), 1.0))
    by_year = sub.groupby(["reporter", "year"]).apply(
        lambda d: d.loc[d["noweight"], "val"].sum() / max(d["val"].sum(), 1.0)
    )
    affected = {str(k): round(float(v), 3) for k, v in by_rep.items() if v > 0.01}
    years = {
        rep: sorted(str(kk[1]) for kk, vv in by_year.items() if str(kk[0]) == rep and vv > 0.2) for rep in affected
    }
    worst = max(affected.values(), default=0.0)
    msg = (
        "every reporter publishes net weight with its monthly values"
        if not affected
        else "value without net weight: "
        + "; ".join(
            f"{k} {v:.0%} (years mostly weightless: {', '.join(years[k]) or 'none'})" for k, v in affected.items()
        )
        + ". Tonnes for these records are estimated from annual unit values or interpolated (see coverage.json)."
    )
    return CheckResult(
        "un_comtrade",
        "weightless_values",
        "pass" if worst <= 0.01 else "warn",
        msg,
        {"share_by_reporter": affected, "years_mostly_weightless": years},
    )


def check_annual_unit_values(records: pd.DataFrame) -> CheckResult:
    """Annual unit values outside the plausible range for dry bulk commodities (unit errors in the source)."""
    from tmdm.transform.flows import UNIT_VALUE_BOUNDS

    a = records[(records["freqCode"] == "A") & (records["partnerCode"] != 0)].copy()
    if a.empty:
        return CheckResult("un_comtrade", "annual_unit_values", "warn", "no annual records", {})
    a["wgt"] = pd.to_numeric(a["netWgt"], errors="coerce")
    a["val"] = pd.to_numeric(a["primaryValue"], errors="coerce")
    a["reporter"] = a["reporterCode"].map(CODE_TO_ISO3).fillna(a["reporterCode"].astype(str))
    g = a.groupby(["reporter", "cmdCode", "period"], as_index=False)[["wgt", "val"]].sum()
    g = g[(g["wgt"] > 0) & (g["val"] > 0)]
    g["unit_value"] = g["val"] / g["wgt"] * 1000.0
    bad = g[(g["unit_value"] < UNIT_VALUE_BOUNDS[0]) | (g["unit_value"] > UNIT_VALUE_BOUNDS[1])]
    # only sizeable flows matter: more than one million US dollars in the year
    bad = bad[bad["val"] > 1e6]
    listing = {
        f"{r['reporter']} {r['cmdCode']} {r['period']}": round(float(r["unit_value"]), 1)
        for r in bad.to_dict(orient="records")
    }
    msg = (
        f"{len(g)} reporter-commodity-years checked; all unit values between {UNIT_VALUE_BOUNDS[0]:.0f} and "
        f"{UNIT_VALUE_BOUNDS[1]:.0f} US dollars per tonne"
        if bad.empty
        else f"{len(bad)} of {len(g)} reporter-commodity-years have implausible unit values (US dollars per tonne) "
        f"and are not used for proxies: {', '.join(f'{k} {v:,.0f}' for k, v in list(listing.items())[:12])}"
        + (" and more" if len(listing) > 12 else "")
    )
    return CheckResult(
        "un_comtrade",
        "annual_unit_values",
        "pass" if bad.empty else "warn",
        msg,
        {"implausible": listing, "checked": len(g)},
    )


def reconciliation_checks(records: pd.DataFrame, comex: pd.DataFrame) -> list[CheckResult]:
    """Compare pairs of sources that measure the same flow."""
    out = []
    iron, coal, soy = {"2601"}, set(COMMODITY_GROUPS["coal"]), {"1201"}
    pairs = [
        (
            "Brazil iron ore exports to China",
            _comex_series(comex, "CHN", iron),
            _comtrade_series(records, "BRA", "X", "CHN", iron),
            "Comex Stat",
            "Comtrade BRA",
        ),
        (
            "Brazil soybean exports to China",
            _comex_series(comex, "CHN", soy),
            _comtrade_series(records, "BRA", "X", "CHN", soy),
            "Comex Stat",
            "Comtrade BRA",
        ),
        (
            "Australia iron ore to China",
            _comtrade_series(records, "AUS", "X", "CHN", iron),
            _comtrade_series(records, "CHN", "M", "AUS", iron),
            "AUS exports",
            "CHN imports",
        ),
        (
            "Brazil iron ore to China",
            _comex_series(comex, "CHN", iron),
            _comtrade_series(records, "CHN", "M", "BRA", iron),
            "Comex Stat exports",
            "CHN imports",
        ),
        (
            "Indonesia coal to China",
            _comtrade_series(records, "IDN", "X", "CHN", coal),
            _comtrade_series(records, "CHN", "M", "IDN", coal),
            "IDN exports",
            "CHN imports",
        ),
        (
            "US soybeans to China",
            _comtrade_series(records, "USA", "X", "CHN", soy),
            _comtrade_series(records, "CHN", "M", "USA", soy),
            "USA exports",
            "CHN imports",
        ),
        (
            "Australia coal to Japan",
            _comtrade_series(records, "AUS", "X", "JPN", coal),
            _comtrade_series(records, "JPN", "M", "AUS", coal),
            "AUS exports",
            "JPN imports",
        ),
    ]
    for label, a, b, la, lb in pairs:
        if a.empty or b.empty:
            out.append(CheckResult("reconciliation", f"reconcile:{label}", "warn", "one of the series is empty", {}))
            continue
        # 12-month rolling sums remove the shipping-time lag between export and import months
        a12 = a.rolling(12).sum().dropna()
        b12 = b.rolling(12).sum().dropna()
        r = check_reconciliation("reconciliation", a12, b12, la, lb)
        r.check = f"reconcile:{label}"
        r.details["basis"] = "12-month rolling sums, tonnes"
        out.append(r)
    return out


def portwatch_checks() -> list[CheckResult]:
    """Checks on PortWatch aggregates, including a comparison with UNCTAD seaborne trade."""
    out = []
    daily = portwatch.load_daily_global()
    out.append(check_missing("imf_portwatch", daily, ["export_dry_bulk_t", "import_dry_bulk_t"]))
    full = pd.date_range(daily["date"].min(), daily["date"].max(), freq="D")
    gaps = len(full) - daily["date"].nunique()
    out.append(
        CheckResult(
            "imf_portwatch",
            "daily_continuity",
            "pass" if gaps == 0 else "warn",
            f"{gaps} missing days",
            {"days": len(daily)},
        )
    )
    out.append(
        check_outliers(
            "imf_portwatch",
            daily.set_index("date")["export_dry_bulk_t"].resample("W").sum().iloc[1:-1],
            "weekly_export_dry_bulk",
        )
    )
    # annual PortWatch dry bulk exports versus UNCTAD world dry cargo loaded
    yearly = daily.assign(year=daily["date"].dt.year).groupby("year")["export_dry_bulk_t"].sum() / 1000.0
    days = daily.assign(year=daily["date"].dt.year).groupby("year")["date"].nunique()
    yearly = yearly[days >= 365]
    sea = unctad.world_seaborne_trade().set_index("year")
    col = next((c for c in sea.columns if "dry_cargo_loaded" in c), None)
    if col is not None:
        ratio = (yearly / sea[col]).dropna()
        out.append(
            CheckResult(
                "imf_portwatch",
                "dry_bulk_share_of_unctad_dry_cargo",
                "pass" if ratio.between(0.3, 0.8).all() else "warn",
                "PortWatch dry bulk exports as a share of UNCTAD world dry cargo loaded: "
                + ", ".join(f"{int(str(y))}: {v:.0%}" for y, v in ratio.items()),
                {"ratio_by_year": {str(y): float(v) for y, v in ratio.items()}},
            )
        )
    return out


def fleet_checks() -> list[CheckResult]:
    """Checks on the UNCTAD fleet series."""
    f = unctad.world_bulk_fleet()
    growth = np.log(f["fleet_dwt_thousand"]).diff().dropna()
    return [
        check_missing("unctadstat_maritime", f, ["fleet_dwt_thousand"]),
        check_range("unctadstat_maritime", growth, "annual_fleet_log_growth", -0.05, 0.20),
        CheckResult(
            "unctadstat_maritime",
            "fleet_years",
            "pass",
            f"bulk carrier fleet {int(f['year'].min())} to {int(f['year'].max())}, "
            f"latest {f['fleet_dwt_thousand'].iloc[-1] / 1000:.0f} million dwt",
            {"first_year": int(f["year"].min()), "last_year": int(f["year"].max())},
        ),
    ]


def metadata_checks() -> list[CheckResult]:
    """Every file listed in a metadata record exists and has the recorded SHA-256 hash."""
    out = []
    for meta in all_metadata():
        bad = [f for f, h in meta.sha256.items() if not (RAW_DIR / meta.dataset_id / f).exists()
               or sha256_of(RAW_DIR / meta.dataset_id / f) != h]  # fmt: skip
        out.append(
            CheckResult(
                meta.dataset_id,
                "metadata_hashes",
                "fail" if bad else "pass",
                f"{len(bad)} of {len(meta.sha256)} files differ from their recorded hash"
                if bad
                else f"{len(meta.sha256)} files match their recorded hash",
                {"files": bad},
            )
        )
    return out


def run() -> None:
    """Bring the sharded downloads' metadata up to date, run every check and write the report."""
    for dataset_id in (comtrade.DATASET_ID, comtrade_long.DATASET_ID):
        rehash_sharded(dataset_id)
    records = comtrade.load_records()
    comex = comexstat.load()
    results: list[CheckResult] = []
    for fn in (metadata_checks, freight_checks, fleet_checks, portwatch_checks):
        try:
            results.extend(fn())
        except (FileNotFoundError, KeyError, ValueError) as exc:
            results.append(CheckResult(fn.__name__, "ran", "fail", f"check group failed: {exc}", {}))
    results.extend(comtrade_checks(records))
    results.extend(reconciliation_checks(records, comex))
    report = {
        "generated_at": pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "summary": {s: sum(1 for r in results if r.status == s) for s in ("pass", "warn", "fail")},
        "checks": [r.as_dict() for r in results],
    }
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    (PROCESSED_DIR / "validation_report.json").write_text(json.dumps(report, indent=2, default=str))
    log.info("validation: %s", report["summary"])
