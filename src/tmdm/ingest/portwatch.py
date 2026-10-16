"""IMF PortWatch: AIS-based port activity and chokepoint transits.

PortWatch (IMF and University of Oxford) publishes daily, port-level estimates
of import and export volumes in metric tons by vessel type, derived from AIS
signals, from 1 January 2019, together with daily transit counts and capacity
through 28 maritime chokepoints. The data are served as public ArcGIS feature
layers. This module aggregates dry bulk volumes by country and month, builds a
daily global dry bulk series and downloads the chokepoint transits.

All aggregation is done server-side with ArcGIS ``outStatistics`` queries so
that a refresh needs a few hundred small requests rather than the full six
million daily rows.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import date
from typing import Any

import pandas as pd

from tmdm.http import get, session
from tmdm.io import DatasetMetadata, raw_dir, utc_now_iso, write_metadata

log = logging.getLogger(__name__)

DATASET_ID = "imf_portwatch"
BASE = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services"
PORTS_LAYER = f"{BASE}/Daily_Ports_Data/FeatureServer/0/query"
CHOKE_LAYER = f"{BASE}/Daily_Chokepoints_Data/FeatureServer/0/query"
PORTS_DB = f"{BASE}/PortWatch_ports_database/FeatureServer/0/query"
CHOKE_DB = f"{BASE}/PortWatch_chokepoints_database/FeatureServer/0/query"
PORTAL_URL = "https://portwatch.imf.org/"
PAGE = 1000
PAUSE_S = 0.5

DRY_BULK_STATS = json.dumps(
    [
        {
            "statisticType": "sum",
            "onStatisticField": "export_dry_bulk",
            "outStatisticFieldName": "export_dry_bulk_t",
        },
        {
            "statisticType": "sum",
            "onStatisticField": "import_dry_bulk",
            "outStatisticFieldName": "import_dry_bulk_t",
        },
        {
            "statisticType": "sum",
            "onStatisticField": "portcalls_dry_bulk",
            "outStatisticFieldName": "portcalls_dry_bulk",
        },
        {"statisticType": "sum", "onStatisticField": "export", "outStatisticFieldName": "export_all_t"},
        {"statisticType": "sum", "onStatisticField": "import", "outStatisticFieldName": "import_all_t"},
    ]
)

FILES = {
    "ports": "ports_database.csv",
    "chokepoints_db": "chokepoints_database.csv",
    "monthly_country": "dry_bulk_monthly_by_country.csv",
    "daily_global": "dry_bulk_daily_global.csv",
    "chokepoints_daily": "chokepoints_daily.csv",
}


def _query(url: str, params: dict[str, Any]) -> dict[str, Any]:
    """Run one ArcGIS query, retrying on in-body errors (which arrive with HTTP 200)."""
    sess = session()
    for attempt in range(6):
        r = get(url, params={**params, "f": "json"}, timeout=180, sess=sess)
        data = r.json()
        if "error" not in data:
            return data
        wait = 5 * (attempt + 1)
        log.warning("portwatch in-body error %s, retry in %ss", data["error"], wait)
        time.sleep(wait)
    raise RuntimeError(f"portwatch query failed: {url} {params}")


def _features(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [f["attributes"] for f in data.get("features", [])]


def _paginate(url: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        data = _query(url, {**params, "resultOffset": offset, "resultRecordCount": PAGE})
        feats = _features(data)
        rows.extend(feats)
        if not data.get("exceededTransferLimit") or not feats:
            break
        offset += len(feats)
        time.sleep(PAUSE_S)
    return rows


def fetch_reference_tables() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Download the ports and chokepoints reference tables (coordinates, names)."""
    d = raw_dir(DATASET_ID)
    ports = pd.DataFrame(_paginate(PORTS_DB, {"where": "1=1", "outFields": "*", "orderByFields": "portid"}))
    ports.to_csv(d / FILES["ports"], index=False)
    chokes = pd.DataFrame(_paginate(CHOKE_DB, {"where": "1=1", "outFields": "*", "orderByFields": "portid"}))
    chokes.to_csv(d / FILES["chokepoints_db"], index=False)
    log.info("portwatch: %d ports, %d chokepoints", len(ports), len(chokes))
    return ports, chokes


def _date_bounds(url: str) -> tuple[date, date]:
    stats = json.dumps(
        [
            {"statisticType": "min", "onStatisticField": "date", "outStatisticFieldName": "dmin"},
            {"statisticType": "max", "onStatisticField": "date", "outStatisticFieldName": "dmax"},
        ]
    )
    data = _query(url, {"where": "1=1", "outStatistics": stats})
    a = _features(data)[0]
    return date.fromisoformat(a["dmin"][:10]), date.fromisoformat(a["dmax"][:10])


def fetch_monthly_by_country(refresh_months: int = 3) -> pd.DataFrame:
    """Sum dry bulk tonnage by country (ISO3) and month, incrementally."""
    d = raw_dir(DATASET_ID)
    path = d / FILES["monthly_country"]
    existing = pd.read_csv(path) if path.exists() else pd.DataFrame()
    dmin, dmax = _date_bounds(PORTS_LAYER)
    months = pd.period_range(pd.Period(dmin, "M"), pd.Period(dmax, "M"), freq="M")
    done = set(existing["month"].astype(str)) if not existing.empty else set()
    refresh = {str(m) for m in months[-refresh_months:]}
    todo = [m for m in months if str(m) not in done or str(m) in refresh]
    frames = [existing[~existing["month"].astype(str).isin([str(m) for m in todo])]] if not existing.empty else []
    for m in todo:
        start = m.to_timestamp(how="start").date()
        end = (m + 1).to_timestamp(how="start").date()
        where = f"date >= DATE '{start}' AND date < DATE '{end}'"
        data = _query(
            PORTS_LAYER,
            {"where": where, "groupByFieldsForStatistics": "ISO3,country", "outStatistics": DRY_BULK_STATS},
        )
        rows = _features(data)
        if data.get("exceededTransferLimit"):
            raise RuntimeError(f"portwatch monthly by country exceeded transfer limit for {m}")
        df = pd.DataFrame(rows)
        df.insert(0, "month", str(m))
        df["days_in_data"] = (min(end, dmax + pd.Timedelta(days=1).to_pytimedelta()) - start).days
        frames.append(df)
        log.info("portwatch monthly %s: %d countries", m, len(df))
        time.sleep(PAUSE_S)
    out = pd.concat(frames, ignore_index=True).sort_values(["month", "ISO3"]).reset_index(drop=True)
    out.to_csv(path, index=False)
    return out


def fetch_daily_global() -> pd.DataFrame:
    """Sum dry bulk tonnage over all ports for every day, one request per year."""
    d = raw_dir(DATASET_ID)
    dmin, dmax = _date_bounds(PORTS_LAYER)
    frames = []
    for year in range(dmin.year, dmax.year + 1):
        where = f"year = {year}"
        data = _query(
            PORTS_LAYER,
            {
                "where": where,
                "groupByFieldsForStatistics": "date",
                "outStatistics": DRY_BULK_STATS,
                "orderByFields": "date",
            },
        )
        frames.append(pd.DataFrame(_features(data)))
        time.sleep(PAUSE_S)
    out = pd.concat(frames, ignore_index=True)
    out["date"] = out["date"].astype(str).str[:10]
    out = out.sort_values("date").reset_index(drop=True)
    out.to_csv(d / FILES["daily_global"], index=False)
    log.info("portwatch daily global: %d days (%s to %s)", len(out), out["date"].iloc[0], out["date"].iloc[-1])
    return out


def fetch_chokepoints_daily() -> pd.DataFrame:
    """Download daily transits and capacity through every chokepoint (incremental)."""
    d = raw_dir(DATASET_ID)
    path = d / FILES["chokepoints_daily"]
    existing = pd.read_csv(path) if path.exists() else pd.DataFrame()
    where = "1=1"
    if not existing.empty:
        last = pd.to_datetime(existing["date"]).max() - pd.Timedelta(days=21)
        where = f"date >= DATE '{last.date()}'"
    rows = _paginate(
        CHOKE_LAYER,
        {
            "where": where,
            "outFields": "date,portid,portname,n_dry_bulk,capacity_dry_bulk,n_total,capacity,n_tanker,n_container",
            "orderByFields": "portid,date",
        },
    )
    new = pd.DataFrame(rows)
    new["date"] = new["date"].astype(str).str[:10]
    if not existing.empty:
        keep = existing[~(existing["date"] >= new["date"].min())]
        new = pd.concat([keep, new], ignore_index=True)
    new = new.drop_duplicates(["portid", "date"]).sort_values(["portid", "date"]).reset_index(drop=True)
    new.to_csv(path, index=False)
    log.info("portwatch chokepoints: %d rows", len(new))
    return new


def fetch() -> None:
    """Download every PortWatch table used by the project and write metadata."""
    ports, chokes = fetch_reference_tables()
    monthly = fetch_monthly_by_country()
    daily = fetch_daily_global()
    choke_daily = fetch_chokepoints_daily()
    meta = DatasetMetadata(
        dataset_id=DATASET_ID,
        title="IMF PortWatch: daily port activity and chokepoint transits (AIS-based)",
        publisher="International Monetary Fund and University of Oxford (PortWatch)",
        source_url=PORTAL_URL,
        licence="Free for non-commercial and research use under the IMF PortWatch terms of use; attribution required",
        frequency="daily (aggregated here to months and to global daily totals)",
        coverage=f"{daily['date'].iloc[0]} to {daily['date'].iloc[-1]}, {len(ports)} ports, {len(chokes)} chokepoints",
        units="metric tons of cargo (import and export estimates), number of port calls, number of transits",
        retrieved_at=utc_now_iso(),
        files=list(FILES.values()),
        limitations=[
            "Volumes are statistical estimates derived from AIS draught changes and vessel capacity, not customs data.",
            "Export and import tonnage are estimated independently, so world exports and world imports differ.",
            "The ports database covers roughly 2,000 ports; small terminals are not covered.",
            "Chokepoint capacity is deadweight capacity of transiting vessels, not cargo on board.",
            "Known reporting gaps (blackout days) and GPS interference episodes are documented on the PortWatch site.",
        ],
        extra={
            "arcgis_layers": {
                "Daily_Ports_Data": PORTS_LAYER,
                "Daily_Chokepoints_Data": CHOKE_LAYER,
                "PortWatch_ports_database": PORTS_DB,
                "PortWatch_chokepoints_database": CHOKE_DB,
            },
            "monthly_rows": len(monthly),
            "chokepoint_rows": len(choke_daily),
        },
    )
    write_metadata(meta)


def load_monthly_by_country() -> pd.DataFrame:
    """Load the monthly country aggregates."""
    return pd.read_csv(raw_dir(DATASET_ID) / FILES["monthly_country"])


def load_daily_global() -> pd.DataFrame:
    """Load the daily global dry bulk series."""
    return pd.read_csv(raw_dir(DATASET_ID) / FILES["daily_global"], parse_dates=["date"])


def load_chokepoints_daily() -> pd.DataFrame:
    """Load the daily chokepoint transits."""
    return pd.read_csv(raw_dir(DATASET_ID) / FILES["chokepoints_daily"], parse_dates=["date"])


def load_ports() -> pd.DataFrame:
    """Load the PortWatch ports reference table."""
    return pd.read_csv(raw_dir(DATASET_ID) / FILES["ports"])
