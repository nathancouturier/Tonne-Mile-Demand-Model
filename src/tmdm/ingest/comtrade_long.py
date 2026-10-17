"""UN Comtrade annual trade since 1980 for the long-history decomposition (all partners).

The monthly panel starts in 2010, when UN Comtrade monthly data begin. For the
multi-decade view the annual records go further back, in two classifications:

* HS as reported, from the first HS year of each reporter (1988 to 1993, 2000
  for South Africa, whose earlier HS records are under the Southern African
  Customs Union);
* SITC Revision 2 for 1980 to 1995, which covers the years before HS and
  overlaps it, so that annual changes can be measured within one
  classification for every year pair.

Exporters are queried for every partner (the World total included), so the
route set is not limited to today's destinations: Japan and Europe dominated
these trades in the 1980s and 1990s. Russian and Mozambican coal is measured
from Indian and Japanese import records, as in the monthly panel. Records are
stored gzip-compressed per shard with a done list, so a refresh only requests
years not yet downloaded and the latest two years.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from tmdm.ingest.comtrade import (
    PORTAL,
    CallGroup,
    ComtradeClient,
    _shard,
    _write_log,
    fetch_period,
)
from tmdm.io import DatasetMetadata, raw_dir, utc_now_iso, write_metadata

log = logging.getLogger(__name__)

DATASET_ID = "un_comtrade_long"
FIRST_YEAR = 1980
SITC_LAST_YEAR = 1995
REFRESH_YEARS = 2
GZIP = {"method": "gzip", "compresslevel": 6, "mtime": 0}

# Comtrade reporter codes. 711 is the Southern African Customs Union (South Africa's reporter before 2000).
EXPORTERS = {"AUS": 36, "BRA": 76, "IDN": 360, "ZAF": 710, "ZAF_SACU": 711, "USA": 842, "CAN": 124, "UKR": 804,
             "ARG": 32, "COL": 170}  # fmt: skip
IMPORTERS = {"IND": 699, "JPN": 392}
# Years outside which a reporter has no records (from the Comtrade availability tables), to avoid empty calls.
REPORTER_YEARS: dict[str, tuple[int, int]] = {"ZAF": (2000, 9999), "ZAF_SACU": (1976, 1999), "UKR": (1996, 9999)}
# Russian Federation, former USSR, Mozambique
IMPORTER_PARTNERS = (643, 810, 508)

# Commodity codes by classification: HS as reported, and SITC Revision 2.
CODES: dict[str, dict[str, tuple[str, ...]]] = {
    "HS": {
        "minerals": ("2601", "2701", "2702"),
        "grains": ("1001", "1003", "1005", "1007", "1201"),
        "coal": ("2701", "2702"),
    },
    "S2": {
        # 281 iron ore and concentrates; 3221 anthracite, 3222 other coal, 3223 lignite (peat, 3224, excluded)
        "minerals": ("281", "3221", "3222", "3223"),
        # 041 wheat, 043 barley, 044 maize, 045 other cereals (sorghum, rye, oats), 2222 soya beans
        "grains": ("041", "043", "044", "045", "2222"),
        "coal": ("3221", "3222", "3223"),
    },
}
COLUMNS = [
    "classificationCode",
    "period",
    "reporterCode",
    "flowCode",
    "partnerCode",
    "cmdCode",
    "netWgt",
    "qty",
    "qtyUnitCode",
    "primaryValue",
    "isAggregate",
]


def call_groups(cls: str) -> list[CallGroup]:
    """One call group per exporter and commodity block (all partners), plus the importer coal groups."""
    out = []
    for iso, code in EXPORTERS.items():
        for block in ("minerals", "grains"):
            out.append(CallGroup(f"{iso}_{block}", (code,), "X", CODES[cls][block], ()))
    for iso, code in IMPORTERS.items():
        out.append(CallGroup(f"{iso}_coal_imports", (code,), "M", CODES[cls]["coal"], IMPORTER_PARTNERS))
    return out


def years_for(cls: str, last_year: int) -> list[int]:
    """Years requested in a classification."""
    if cls == "S2":
        return list(range(FIRST_YEAR, SITC_LAST_YEAR + 1))
    return list(range(1988, last_year + 1))


def _files(d: Path) -> tuple[Path, Path, Path]:
    k, n = _shard()
    suffix = "" if n == 1 else f"_shard{k}of{n}"
    return d / f"records{suffix}.csv.gz", d / f"fetch_log{suffix}.csv", d / f"done{suffix}.csv"


def _tidy(recs: list[dict[str, Any]], group: CallGroup, cls: str, retrieved_at: str) -> pd.DataFrame:
    df = pd.DataFrame(recs)
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = None
    df = df[COLUMNS].copy()
    df.insert(0, "group_id", group.group_id)
    df.insert(1, "classification", cls)
    df["retrieved_at"] = retrieved_at
    return df


def _save(path: Path, frames: list[pd.DataFrame]) -> pd.DataFrame:
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    key = ["classification", "period", "reporterCode", "flowCode", "partnerCode", "cmdCode"]
    out = out.drop_duplicates(key, keep="last").sort_values(["group_id", *key]).reset_index(drop=True)
    out.to_csv(path, index=False, compression=GZIP)
    return out


def fetch() -> pd.DataFrame:
    """Download (incrementally) the annual records of this shard and write a metadata record."""
    d = raw_dir(DATASET_ID)
    rec_path, log_path, done_path = _files(d)
    k, n = _shard()
    existing = (
        pd.read_csv(rec_path, dtype={"cmdCode": str, "period": str}, compression="gzip")
        if rec_path.exists()
        else pd.DataFrame()
    )
    done = (
        pd.read_csv(done_path, dtype={"period": str})
        if done_path.exists()
        else pd.DataFrame(columns=["classification", "group_id", "period", "n_records", "retrieved_at"])
    )
    done_keys = set(zip(done["classification"], done["group_id"], done["period"].astype(str), strict=True))
    last_year = datetime.now(UTC).year - 1
    client = ComtradeClient()
    pending = []
    for cls in ("S2", "HS"):
        years = [y for i, y in enumerate(years_for(cls, last_year)) if i % n == k]
        for g in call_groups(cls):
            lo, hi = REPORTER_YEARS.get(g.group_id.rsplit("_", 1)[0], (0, 9999))
            for y in years:
                if not lo <= y <= hi:
                    continue
                recent = cls == "HS" and y > last_year - REFRESH_YEARS
                if (cls, g.group_id, str(y)) in done_keys and not recent:
                    continue
                pending.append((cls, g, str(y)))
    log.info("comtrade long shard %d/%d: %d calls planned", k, n, len(pending))
    frames = [existing]
    rows: list[dict[str, Any]] = []
    try:
        for i, (cls, g, period) in enumerate(pending, start=1):
            recs = fetch_period(client, g, g.reporter_codes, "A", period, cls)
            new = _tidy(recs, g, cls, utc_now_iso())
            if not frames[0].empty:
                frames[0] = frames[0][
                    ~(
                        (frames[0]["classification"] == cls)
                        & (frames[0]["group_id"] == g.group_id)
                        & (frames[0]["period"].astype(str) == period)
                    )
                ]
            frames.append(new)
            rows.append(
                {
                    "classification": cls,
                    "group_id": g.group_id,
                    "period": period,
                    "n_records": len(new),
                    "retrieved_at": utc_now_iso(),
                }
            )
            if i % 20 == 0:
                frames = [_save(rec_path, frames)]
                _write_done(done_path, done, rows)
                _write_log(log_path, client)
                log.info("comtrade long shard %d/%d: %d of %d calls done", k, n, i, len(pending))
    finally:
        out = _save(rec_path, frames)
        _write_done(done_path, done, rows)
        _write_log(log_path, client)
    if k == 0:
        write_metadata(_metadata(last_year))
    return out


def _write_done(path: Path, done: pd.DataFrame, rows: list[dict[str, Any]]) -> None:
    if not rows and done.empty:
        return
    new = pd.concat([done, pd.DataFrame(rows)], ignore_index=True) if rows else done
    new = new.drop_duplicates(["classification", "group_id", "period"], keep="last")
    new.sort_values(["classification", "group_id", "period"]).to_csv(path, index=False)


def _metadata(last_year: int) -> DatasetMetadata:
    return DatasetMetadata(
        dataset_id=DATASET_ID,
        title="UN Comtrade: annual exports of iron ore, coal and grains to every partner since 1980 (net weight)",
        publisher="United Nations Statistics Division (UN Comtrade)",
        source_url=PORTAL,
        licence="UN Comtrade public data; free for non-commercial use with attribution (see Comtrade terms of use)",
        frequency="annual",
        coverage=f"SITC Revision 2 {FIRST_YEAR} to {SITC_LAST_YEAR}; HS as reported 1988 to {last_year}",
        units="kilograms (netWgt); values in current US dollars (primaryValue)",
        retrieved_at=utc_now_iso(),
        files=sorted(p.name for p in raw_dir(DATASET_ID).glob("*") if p.name != "metadata.json"),
        limitations=[
            "Reporters enter the record in different years (HS from 1988 to 1993; the United States from 1981, "
            "Brazil from 1983 in SITC Revision 2; Ukraine from 1996); annual changes are measured on the reporters "
            "present in both years.",
            "SITC Revision 2 groups differ slightly from the HS headings (other cereals include rye and oats); "
            "changes are always measured within one classification.",
            "Partners that no longer exist (USSR, the two German states, Yugoslavia) are mapped to a successor's "
            "ports for distances.",
            "Mode of transport is not separated, so exports to land neighbours are excluded by route design.",
            "Russian exporter records are not used (Russia reports only from 1996 and stopped in 2022); Russian "
            "coal is measured from Indian and Japanese import records.",
        ],
        extra={
            "exporters": EXPORTERS,
            "importers": IMPORTERS,
            "importer_partners": IMPORTER_PARTNERS,
            "codes": {c: {b: list(v) for b, v in blocks.items()} for c, blocks in CODES.items()},
        },
    )


def load_records() -> pd.DataFrame:
    """All shards merged."""
    d = raw_dir(DATASET_ID)
    files = sorted(d.glob("records*.csv.gz"))
    if not files:
        raise FileNotFoundError("no long-history Comtrade records downloaded yet")
    frames = [pd.read_csv(f, dtype={"cmdCode": str, "period": str}, compression="gzip") for f in files]
    out = pd.concat(frames, ignore_index=True)
    key = ["classification", "period", "reporterCode", "flowCode", "partnerCode", "cmdCode"]
    return out.drop_duplicates(key, keep="last").reset_index(drop=True)
