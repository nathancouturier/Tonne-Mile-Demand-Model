"""UN Comtrade bilateral trade flows (public preview API).

The public preview endpoint needs no subscription key but returns at most 500
records per call and allows roughly one call every three seconds. Queries are
therefore planned so that each call stays well under the record cap, calls are
paced, and every response is cached as tidy rows so that a refresh only
requests recent months.

Quantities are taken from ``netWgt`` (net weight in kilograms) which is the
field customs authorities report for bulk commodities.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from tmdm.http import session
from tmdm.io import DatasetMetadata, raw_dir, utc_now_iso, write_metadata

log = logging.getLogger(__name__)

DATASET_ID = "un_comtrade"
PREVIEW = "https://comtradeapi.un.org/public/v1/preview/C/{freq}/HS"
AVAILABILITY = "https://comtradeapi.un.org/public/v1/getDa/C/{freq}/HS"
REF_REPORTERS = "https://comtradeapi.un.org/files/v1/app/reference/Reporters.json"
REF_PARTNERS = "https://comtradeapi.un.org/files/v1/app/reference/partnerAreas.json"
PORTAL = "https://comtradeplus.un.org/"
RECORD_CAP = 500
MIN_INTERVAL_S = 3.6

RECORDS_FILE = "records.csv"
LOG_FILE = "fetch_log.csv"
AVAIL_FILE = "availability.csv"

COLUMNS = [
    "freqCode",
    "period",
    "reporterCode",
    "reporterISO",
    "flowCode",
    "partnerCode",
    "partnerISO",
    "cmdCode",
    "netWgt",
    "isNetWgtEstimated",
    "qty",
    "qtyUnitAbbr",
    "primaryValue",
    "isReported",
    "isAggregate",
    "retrieved_at",
]


@dataclass(frozen=True)
class QuerySpec:
    """One reporter, flow and commodity set with an explicit partner list."""

    spec_id: str
    reporter_code: int
    flow: str  # X exports or M imports
    cmd_codes: tuple[str, ...]
    partner_codes: tuple[int, ...]
    note: str = ""


@dataclass
class Pacer:
    """Enforce the minimum spacing between calls."""

    last_call: float = field(default=0.0)

    def wait(self) -> None:
        """Sleep until the minimum interval since the previous call has elapsed."""
        now = time.monotonic()
        delta = now - self.last_call
        if delta < MIN_INTERVAL_S:
            time.sleep(MIN_INTERVAL_S - delta)
        self.last_call = time.monotonic()


class ComtradeClient:
    """Thin client around the public preview endpoint with pacing and retries."""

    def __init__(self) -> None:
        """Create a session, a pacer and an empty fetch log."""
        self.sess = session()
        self.pacer = Pacer()
        self.log_rows: list[dict[str, Any]] = []

    def call(self, freq: str, params: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        """Return (records, count) for one preview query, retrying on 429 and 5xx."""
        url = PREVIEW.format(freq=freq)
        clean = {k: v for k, v in params.items() if v is not None}
        for attempt in range(8):
            self.pacer.wait()
            t0 = time.time()
            try:
                r = self.sess.get(url, params=clean, timeout=120)
            except (requests.ConnectionError, requests.Timeout) as exc:
                log.warning("comtrade network error %s, retrying", exc)
                time.sleep(10 * (attempt + 1))
                continue
            entry = {
                "retrieved_at": utc_now_iso(),
                "url": r.url,
                "status": r.status_code,
                "elapsed_s": round(time.time() - t0, 2),
                "count": None,
            }
            if r.status_code == 429:
                wait = float(r.headers.get("Retry-After", "5")) + 1.0
                log.info("comtrade 429, waiting %.0fs", wait)
                entry["count"] = "rate_limited"
                self.log_rows.append(entry)
                time.sleep(wait)
                continue
            if r.status_code >= 500:
                self.log_rows.append(entry)
                time.sleep(15 * (attempt + 1))
                continue
            if r.status_code != 200:
                entry["count"] = f"error: {r.text[:200]}"
                self.log_rows.append(entry)
                raise RuntimeError(f"comtrade HTTP {r.status_code}: {r.text[:300]}")
            data = r.json()
            recs = data.get("data", [])
            entry["count"] = len(recs)
            self.log_rows.append(entry)
            return recs, int(data.get("count", len(recs)))
        raise RuntimeError("comtrade: too many retries")

    def availability(self, freq: str, reporter_codes: list[int]) -> pd.DataFrame:
        """Return the published periods per reporter (data availability endpoint)."""
        url = AVAILABILITY.format(freq=freq)
        self.pacer.wait()
        r = self.sess.get(url, params={"reporterCode": ",".join(str(c) for c in reporter_codes)}, timeout=120)
        r.raise_for_status()
        rows = r.json().get("data", [])
        df = pd.DataFrame(rows)
        if df.empty:
            return pd.DataFrame(columns=["reporterCode", "period", "lastReleased"])
        keep = [
            c
            for c in ["reporterCode", "reporterISO", "period", "firstReleased", "lastReleased", "totalRecords"]
            if c in df
        ]
        return df[keep]


def _tidy(recs: list[dict[str, Any]], retrieved_at: str) -> pd.DataFrame:
    if not recs:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.DataFrame(recs)
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = None
    df["retrieved_at"] = retrieved_at
    return df[COLUMNS]


def fetch_spec(client: ComtradeClient, spec: QuerySpec, freq: str, periods: list[str]) -> pd.DataFrame:
    """Fetch every period for a spec (the preview API accepts one period per call).

    A response that reaches the record cap is re-requested one commodity code at
    a time so that no month is silently truncated.
    """
    frames = []
    for period in periods:
        params = {
            "reporterCode": spec.reporter_code,
            "period": period,
            "cmdCode": ",".join(spec.cmd_codes),
            "flowCode": spec.flow,
            "partnerCode": ",".join(str(p) for p in spec.partner_codes),
            "partner2Code": 0,
            "customsCode": "C00",
            "motCode": 0,
        }
        recs, count = client.call(freq, params)
        if count >= RECORD_CAP and len(spec.cmd_codes) > 1:
            log.info("comtrade %s %s: %d records hit the cap, splitting by commodity", spec.spec_id, period, count)
            recs = []
            for cmd in spec.cmd_codes:
                sub, sub_count = client.call(freq, {**params, "cmdCode": cmd})
                if sub_count >= RECORD_CAP:
                    raise RuntimeError(
                        f"comtrade {spec.spec_id} {period} {cmd}: single commodity still at the record cap"
                    )
                recs.extend(sub)
        elif count >= RECORD_CAP:
            raise RuntimeError(f"comtrade {spec.spec_id} {period}: response at the record cap, narrow the partner list")
        frames.append(_tidy(recs, utc_now_iso()))
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=COLUMNS)
    out.insert(0, "spec_id", spec.spec_id)
    return out


def _write_records(path: Path, frames: list[pd.DataFrame]) -> pd.DataFrame:
    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(
        ["spec_id", "freqCode", "period", "reporterCode", "flowCode", "partnerCode", "cmdCode"], keep="last"
    )
    out = out.sort_values(["spec_id", "period", "cmdCode", "partnerCode"]).reset_index(drop=True)
    out.to_csv(path, index=False)
    return out


def _write_log(log_path: Path, client: ComtradeClient) -> None:
    log_df = pd.DataFrame(client.log_rows)
    if log_df.empty:
        return
    if log_path.exists():
        log_df = pd.concat([pd.read_csv(log_path), log_df], ignore_index=True)
    log_df.to_csv(log_path, index=False)
    client.log_rows = []


def _month_periods(start: str, end: str) -> list[str]:
    return [p.strftime("%Y%m") for p in pd.period_range(start, end, freq="M")]


def fetch(
    specs: list[QuerySpec],
    start_month: str,
    refresh_months: int = 24,
    annual_specs: list[QuerySpec] | None = None,
    annual_years: tuple[int, int] = (2000, 2009),
) -> pd.DataFrame:
    """Download (incrementally) every spec and persist the tidy records and fetch log."""
    d = raw_dir(DATASET_ID)
    path = d / RECORDS_FILE
    existing = (
        pd.read_csv(path, dtype={"cmdCode": str, "period": str})
        if path.exists()
        else pd.DataFrame(columns=["spec_id", *COLUMNS])
    )
    client = ComtradeClient()
    # reference tables for traceability
    for url, name in ((REF_REPORTERS, "reporters.json"), (REF_PARTNERS, "partnerAreas.json")):
        client.pacer.wait()
        r = client.sess.get(url, timeout=120)
        r.raise_for_status()
        (d / name).write_bytes(r.content)
    now = pd.Period(datetime.now(UTC).strftime("%Y-%m"), freq="M")
    reporter_codes = sorted({s.reporter_code for s in specs})
    avail = client.availability("M", reporter_codes)
    avail.to_csv(d / AVAIL_FILE, index=False)
    latest_by_reporter: dict[int, str] = {}
    if not avail.empty:
        latest_by_reporter = avail.groupby("reporterCode")["period"].max().astype(str).to_dict()
    frames = [existing]
    for spec in specs:
        latest = latest_by_reporter.get(spec.reporter_code)
        end = pd.Period(f"{latest[:4]}-{latest[4:6]}", freq="M") if latest else now - 1
        all_periods = _month_periods(start_month, str(end))
        have = (
            set(existing.loc[existing["spec_id"] == spec.spec_id, "period"].astype(str))
            if not existing.empty
            else set()
        )
        refresh_from = end - refresh_months
        todo = [p for p in all_periods if p not in have or pd.Period(f"{p[:4]}-{p[4:6]}", freq="M") >= refresh_from]
        if not todo:
            continue
        log.info("comtrade %s: fetching %d months (%s to %s)", spec.spec_id, len(todo), todo[0], todo[-1])
        new = fetch_spec(client, spec, "M", todo)
        frames.append(new)
        # drop stale rows for refreshed periods, then checkpoint to disk
        frames[0] = frames[0][~((frames[0]["spec_id"] == spec.spec_id) & (frames[0]["period"].astype(str).isin(todo)))]
        _write_records(path, frames)
        _write_log(d / LOG_FILE, client)
    for spec in annual_specs or []:
        years = [str(y) for y in range(annual_years[0], annual_years[1] + 1)]
        have = (
            set(existing.loc[existing["spec_id"] == spec.spec_id, "period"].astype(str))
            if not existing.empty
            else set()
        )
        todo = [y for y in years if y not in have]
        if not todo:
            continue
        log.info("comtrade annual %s: fetching %d years", spec.spec_id, len(todo))
        frames.append(fetch_spec(client, spec, "A", todo))
    out = _write_records(path, frames)
    _write_log(d / LOG_FILE, client)
    meta = DatasetMetadata(
        dataset_id=DATASET_ID,
        title="UN Comtrade: monthly bilateral trade in iron ore, coal, grains and soybeans (net weight)",
        publisher="United Nations Statistics Division (UN Comtrade)",
        source_url=PORTAL,
        licence="UN Comtrade public data; free for non-commercial use with attribution (see Comtrade terms of use)",
        frequency="monthly (annual extension for 2000 to 2009)",
        coverage=(
            f"{start_month} to latest published month per reporter; "
            f"{out['spec_id'].nunique()} reporter and flow specifications"
        ),
        units="kilograms (netWgt); values in current US dollars (primaryValue)",
        retrieved_at=utc_now_iso(),
        files=[RECORDS_FILE, LOG_FILE, AVAIL_FILE, "reporters.json", "partnerAreas.json"],
        limitations=[
            "Reporter customs data are used as reported; mirror flows between two reporters differ by timing, "
            "valuation and routing.",
            "Monthly data are revised by reporters; the last 24 months are refreshed on every run.",
            "Mode of transport is not separated for most reporters, so overland flows between neighbours are "
            "excluded by route design.",
            "China publishes monthly detail to Comtrade with a lag of more than a year, so China-reported series "
            "are used only for cross-checks, not for the monthly panel.",
            "The public preview API returns at most 500 records per call; queries are split so that no response "
            "is truncated.",
        ],
        extra={"specs": [spec.__dict__ for spec in specs], "annual_specs": [s.__dict__ for s in annual_specs or []]},
    )
    write_metadata(meta)
    return out


def load_records() -> pd.DataFrame:
    """Load the cached tidy records."""
    return pd.read_csv(raw_dir(DATASET_ID) / RECORDS_FILE, dtype={"cmdCode": str, "period": str, "freqCode": str})


def load_partner_names() -> dict[int, str]:
    """Map partner codes to names from the cached reference table."""
    p = raw_dir(DATASET_ID) / "partnerAreas.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    return {int(r["id"]): r["text"] for r in data["results"] if str(r["id"]).isdigit()}
