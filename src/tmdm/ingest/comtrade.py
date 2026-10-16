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
import os
import re
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
class CallGroup:
    """Several reporters queried together: one call per period returns every reporter's rows."""

    group_id: str
    reporter_codes: tuple[int, ...]
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


QUOTA_RE = re.compile(r"replenished in (\d+):(\d+):(\d+)")


class QuotaExhaustedError(RuntimeError):
    """Raised when the hourly call quota is exhausted and waiting is not allowed."""


class ComtradeClient:
    """Thin client around the public preview endpoint with pacing, quota handling and retries."""

    def __init__(self, wait_for_quota: bool = True) -> None:
        """Create a session, a pacer and an empty fetch log."""
        self.sess = session()
        self.pacer = Pacer()
        self.wait_for_quota = wait_for_quota
        self.log_rows: list[dict[str, Any]] = []
        self.calls = 0

    def call(self, freq: str, params: dict[str, Any]) -> tuple[list[dict[str, Any]], int]:
        """Return (records, count) for one preview query, handling 429, 403 quota and 5xx."""
        url = PREVIEW.format(freq=freq)
        clean = {k: v for k, v in params.items() if v is not None}
        for attempt in range(10):
            self.pacer.wait()
            t0 = time.time()
            try:
                r = self.sess.get(url, params=clean, timeout=120)
            except (requests.ConnectionError, requests.Timeout) as exc:
                log.warning("comtrade network error %s, retrying", exc)
                time.sleep(10 * (attempt + 1))
                continue
            self.calls += 1
            entry = {
                "retrieved_at": utc_now_iso(),
                "url": r.url,
                "status": r.status_code,
                "elapsed_s": round(time.time() - t0, 2),
                "count": None,
            }
            if r.status_code == 429:
                wait = float(r.headers.get("Retry-After", "5")) + 1.0
                entry["count"] = "rate_limited"
                self.log_rows.append(entry)
                time.sleep(wait)
                continue
            if r.status_code == 403 and "quota" in r.text.lower():
                m = QUOTA_RE.search(r.text)
                wait = (int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + 20) if m else 1800
                entry["count"] = f"quota_exhausted_wait_{wait}s"
                self.log_rows.append(entry)
                if not self.wait_for_quota:
                    raise QuotaExhaustedError(r.text[:200])
                log.warning("comtrade quota exhausted after %d calls, sleeping %ds", self.calls, wait)
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


def fetch_period(
    client: ComtradeClient, group: CallGroup, reporters: tuple[int, ...], freq: str, period: str
) -> list[dict[str, Any]]:
    """Fetch one period for a reporter list, splitting on the record cap so nothing is truncated."""
    params = {
        "reporterCode": ",".join(str(r) for r in reporters),
        "period": period,
        "cmdCode": ",".join(group.cmd_codes),
        "flowCode": group.flow,
        "partnerCode": ",".join(str(p) for p in group.partner_codes),
        "partner2Code": 0,
        "customsCode": "C00",
        "motCode": 0,
    }
    recs, count = client.call(freq, params)
    if count < RECORD_CAP:
        return recs
    if len(reporters) > 1:
        half = len(reporters) // 2
        log.info("comtrade %s %s: %d records hit the cap, splitting reporters", group.group_id, period, count)
        return fetch_period(client, group, reporters[:half], freq, period) + fetch_period(
            client, group, reporters[half:], freq, period
        )
    out: list[dict[str, Any]] = []
    for cmd in group.cmd_codes:
        sub, sub_count = client.call(freq, {**params, "cmdCode": cmd})
        if sub_count >= RECORD_CAP:
            raise RuntimeError(f"comtrade {group.group_id} {period} {cmd}: single commodity still at the record cap")
        out.extend(sub)
    return out


def _shard() -> tuple[int, int]:
    raw = os.environ.get("TMDM_COMTRADE_SHARD", "0/1")
    k, n = raw.split("/")
    return int(k), int(n)


def _files(d: Path) -> tuple[Path, Path, Path]:
    k, n = _shard()
    suffix = "" if n == 1 else f"_shard{k}of{n}"
    return d / f"records{suffix}.csv", d / f"fetch_log{suffix}.csv", d / f"done{suffix}.csv"


def _write_records(path: Path, frames: list[pd.DataFrame]) -> pd.DataFrame:
    out = (
        pd.concat([f for f in frames if not f.empty], ignore_index=True)
        if any(not f.empty for f in frames)
        else pd.DataFrame(columns=["spec_id", *COLUMNS])
    )
    out = out.drop_duplicates(["freqCode", "period", "reporterCode", "flowCode", "partnerCode", "cmdCode"], keep="last")
    out = out.sort_values(["spec_id", "period", "reporterCode", "cmdCode", "partnerCode"]).reset_index(drop=True)
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
    groups: list[CallGroup],
    start_month: str,
    refresh_months: int = 15,
    annual_years: tuple[int, int] = (2000, 2009),
    checkpoint_every: int = 10,
) -> pd.DataFrame:
    """Download (incrementally) every call group for this shard and persist records, log and done list.

    Periods are assigned to shards round-robin so that parallel jobs, each with
    its own hourly quota, cover disjoint months. Each shard writes its own
    files; :func:`load_records` merges them.
    """
    d = raw_dir(DATASET_ID)
    rec_path, log_path, done_path = _files(d)
    k, n = _shard()
    existing = (
        pd.read_csv(rec_path, dtype={"cmdCode": str, "period": str})
        if rec_path.exists()
        else pd.DataFrame(columns=["spec_id", *COLUMNS])
    )
    done = (
        pd.read_csv(done_path, dtype={"period": str})
        if done_path.exists()
        else pd.DataFrame(columns=["spec_id", "freq", "period", "n_records", "retrieved_at"])
    )
    client = ComtradeClient(wait_for_quota=os.environ.get("TMDM_COMTRADE_WAIT_QUOTA", "1") == "1")
    reporter_codes = sorted({r for g in groups for r in g.reporter_codes})
    if k == 0:
        for url, name in ((REF_REPORTERS, "reporters.json"), (REF_PARTNERS, "partnerAreas.json")):
            client.pacer.wait()
            r = client.sess.get(url, timeout=120)
            r.raise_for_status()
            (d / name).write_bytes(r.content)
        avail = client.availability("M", reporter_codes)
        avail.to_csv(d / AVAIL_FILE, index=False)
    else:
        # other shards reuse the availability table written by shard 0 or the previous run
        client.pacer.wait()
        avail = client.availability("M", reporter_codes)
    published: dict[int, set[str]] = {}
    latest: dict[int, str] = {}
    if not avail.empty:
        for code, grp in avail.groupby("reporterCode"):
            periods = {str(p) for p in grp["period"]}
            published[int(str(code))] = periods
            latest[int(str(code))] = max(periods)
    now = pd.Period(datetime.now(UTC).strftime("%Y-%m"), freq="M")
    all_months = _month_periods(start_month, str(now - 1))
    months = [m for i, m in enumerate(all_months) if i % n == k]
    years = [str(y) for y in range(annual_years[0], annual_years[1] + 1)]
    years = [y for i, y in enumerate(years) if i % n == k]
    done_keys = set(zip(done["spec_id"], done["freq"], done["period"], strict=True))
    frames = [existing]
    done_rows: list[dict[str, Any]] = []
    todo: list[tuple[CallGroup, str, str, tuple[int, ...]]] = []
    for g in groups:
        for m in months:
            reps = tuple(r for r in g.reporter_codes if m in published.get(r, set()))
            if not reps:
                continue
            refresh_from = {
                r: str(pd.Period(f"{latest[r][:4]}-{latest[r][4:6]}", freq="M") - refresh_months)
                for r in reps
                if r in latest
            }
            is_recent = any(f"{m[:4]}-{m[4:6]}" >= refresh_from[r] for r in reps if r in refresh_from)
            if (g.group_id, "M", m) in done_keys and not is_recent:
                continue
            todo.append((g, "M", m, reps))
        for y in years:
            if (g.group_id, "A", y) in done_keys:
                continue
            todo.append((g, "A", y, g.reporter_codes))
    log.info("comtrade shard %d/%d: %d calls planned", k, n, len(todo))
    since_checkpoint = 0
    try:
        for g, freq, period, reps in todo:
            recs = fetch_period(client, g, reps, freq, period)
            new = _tidy(recs, utc_now_iso())
            new.insert(0, "spec_id", g.group_id)
            frames[0] = frames[0][
                ~(
                    (frames[0]["spec_id"] == g.group_id)
                    & (frames[0]["freqCode"] == freq)
                    & (frames[0]["period"].astype(str) == period)
                )
            ]
            frames.append(new)
            done_rows.append(
                {
                    "spec_id": g.group_id,
                    "freq": freq,
                    "period": period,
                    "n_records": len(new),
                    "retrieved_at": utc_now_iso(),
                }
            )
            since_checkpoint += 1
            if since_checkpoint >= checkpoint_every:
                frames = [_write_records(rec_path, frames)]
                _write_log(log_path, client)
                _write_done(done_path, done, done_rows)
                since_checkpoint = 0
                log.info("comtrade shard %d/%d: %d of %d calls done", k, n, len(done_rows), len(todo))
    finally:
        out = _write_records(rec_path, frames)
        _write_log(log_path, client)
        _write_done(done_path, done, done_rows)
    if k == 0:
        write_metadata(_metadata(start_month, groups, out))
    return out


def _write_done(done_path: Path, done: pd.DataFrame, rows: list[dict[str, Any]]) -> None:
    if not rows and done.empty:
        return
    new = pd.concat([done, pd.DataFrame(rows)], ignore_index=True) if rows else done
    new = new.drop_duplicates(["spec_id", "freq", "period"], keep="last").sort_values(["spec_id", "freq", "period"])
    new.to_csv(done_path, index=False)


def _metadata(start_month: str, groups: list[CallGroup], out: pd.DataFrame) -> DatasetMetadata:
    return DatasetMetadata(
        dataset_id=DATASET_ID,
        title="UN Comtrade: monthly bilateral trade in iron ore, coal, grains and soybeans (net weight)",
        publisher="United Nations Statistics Division (UN Comtrade)",
        source_url=PORTAL,
        licence="UN Comtrade public data; free for non-commercial use with attribution (see Comtrade terms of use)",
        frequency="monthly (annual extension for 2000 to 2009)",
        coverage=f"{start_month} to latest published month per reporter; {len(groups)} reporter groups",
        units="kilograms (netWgt); values in current US dollars (primaryValue)",
        retrieved_at=utc_now_iso(),
        files=[*sorted(p.name for p in raw_dir(DATASET_ID).glob("*.csv")), "reporters.json", "partnerAreas.json"],
        limitations=[
            "Reporter customs data are used as reported; mirror flows between two reporters differ by timing, "
            "valuation and routing.",
            "Monthly data are revised by reporters; the last 15 months are refreshed on every run.",
            "Mode of transport is not separated for most reporters, so overland flows between neighbours are "
            "excluded by route design.",
            "China publishes monthly detail to Comtrade with a lag of more than a year, so China-reported series "
            "are used only for cross-checks, not for the monthly panel.",
            "The public preview API returns at most 500 records per call and enforces hourly call quotas; queries "
            "are split so that no response is truncated and the client waits when the quota is exhausted.",
        ],
        extra={"groups": [g.__dict__ for g in groups], "rows": len(out)},
    )


def load_records() -> pd.DataFrame:
    """Load and merge the cached tidy records of every shard."""
    d = raw_dir(DATASET_ID)
    files = sorted(d.glob("records*.csv"))
    if not files:
        raise FileNotFoundError("no Comtrade records downloaded yet")
    frames = [pd.read_csv(f, dtype={"cmdCode": str, "period": str, "freqCode": str}) for f in files]
    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(["freqCode", "period", "reporterCode", "flowCode", "partnerCode", "cmdCode"], keep="last")
    return out.reset_index(drop=True)


def load_availability() -> pd.DataFrame:
    """Load the published-periods table saved during the last ingest."""
    return pd.read_csv(raw_dir(DATASET_ID) / AVAIL_FILE, dtype={"period": str})


def load_partner_names() -> dict[int, str]:
    """Map partner codes to names from the cached reference table."""
    p = raw_dir(DATASET_ID) / "partnerAreas.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    names: dict[int, str] = {}
    for r in data["results"]:
        if str(r["id"]).isdigit():
            names[int(r["id"])] = str(r["text"])
    return names
