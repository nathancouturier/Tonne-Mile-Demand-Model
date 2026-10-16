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

from tmdm.config import PROCESSED_DIR
from tmdm.ingest import asub_bdi, comexstat, comtrade, portwatch, unctad
from tmdm.reference import COMEX_COUNTRY_TO_ISO3, COMTRADE_CODES
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
    return out


def reconciliation_checks(records: pd.DataFrame, comex: pd.DataFrame) -> list[CheckResult]:
    """Compare pairs of sources that measure the same flow."""
    out = []
    iron, coal, soy = {"2601"}, {"2701"}, {"1201"}
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


def run() -> None:
    """Run every check and write the report."""
    records = comtrade.load_records()
    comex = comexstat.load()
    results: list[CheckResult] = []
    for fn in (freight_checks, fleet_checks, portwatch_checks):
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
