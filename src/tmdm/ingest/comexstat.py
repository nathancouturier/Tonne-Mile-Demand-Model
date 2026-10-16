"""Brazil Comex Stat: monthly exports by destination country and HS heading.

Comex Stat is the official foreign trade statistics portal of the Brazilian
Ministry of Development, Industry, Trade and Services (MDIC). Its public API
returns monthly exports in kilograms and US dollars by partner country for any
HS4 heading from 1997 onward, typically within a week of month end. It is the
primary source for Brazilian iron ore, soybean and maize shipments and a
cross-check against the Brazilian figures reported to UN Comtrade.
"""

from __future__ import annotations

import logging
import time

import pandas as pd

from tmdm.http import get, post_json
from tmdm.io import DatasetMetadata, raw_dir, utc_now_iso, write_metadata

log = logging.getLogger(__name__)

DATASET_ID = "brazil_comexstat"
API = "https://api-comexstat.mdic.gov.br"
PORTAL = "https://comexstat.mdic.gov.br/"
HEADINGS = {"2601": "iron_ore", "1201": "soybeans", "1005": "maize", "2304": "soybean_meal"}
RECORDS_FILE = "exports_by_country_heading.csv"
COUNTRIES_FILE = "countries.json"
# The API rate-limits bursts of requests, so calls are spaced out and 429 responses wait a long time.
PAUSE_S = 8.0
YEARS_PER_CALL = 3


def _period_query(year_from: int, year_to: int, headings: list[int]) -> list[dict[str, str]]:
    body = {
        "flow": "export",
        "monthDetail": True,
        "period": {"from": f"{year_from}-01", "to": f"{year_to}-12"},
        "filters": [{"filter": "heading", "values": headings}],
        "details": ["country", "heading"],
        "metrics": ["metricKG", "metricFOB"],
    }
    r = post_json(f"{API}/general", body, params={"language": "en"}, backoff=60.0, retries=5)
    data = r.json()
    if not data.get("success", True):
        raise RuntimeError(f"comexstat error: {data.get('message')}")
    rows: list[dict[str, str]] = data["data"]["list"]
    return rows


def fetch(start_year: int = 1997, refresh_years: int = 2) -> pd.DataFrame:
    """Download exports by country and heading, one request per year, incrementally."""
    d = raw_dir(DATASET_ID)
    path = d / RECORDS_FILE
    existing = pd.read_csv(path, dtype={"headingCode": str}) if path.exists() else pd.DataFrame()
    r = get(f"{API}/tables/countries", params={"language": "en"})
    (d / COUNTRIES_FILE).write_bytes(r.content)
    time.sleep(PAUSE_S)
    upd = get(f"{API}/general/dates/updated", params={"language": "en"}).json()["data"]
    time.sleep(PAUSE_S)
    last_year, last_month = int(upd["year"]), int(upd["monthNumber"])
    log.info("comexstat: data updated %s through %s-%02d", upd.get("updated"), last_year, last_month)
    have_years = set(existing["year"].astype(int)) if not existing.empty else set()
    years = [y for y in range(start_year, last_year + 1) if y not in have_years or y >= last_year - refresh_years + 1]
    frames = [existing[~existing["year"].astype(int).isin(years)]] if not existing.empty else []
    chunks = [years[i : i + YEARS_PER_CALL] for i in range(0, len(years), YEARS_PER_CALL)]
    for chunk in chunks:
        rows = _period_query(chunk[0], chunk[-1], [int(h) for h in HEADINGS])
        df = pd.DataFrame(rows)
        if df.empty:
            continue
        df["year"] = df["year"].astype(int)
        df["monthNumber"] = df["monthNumber"].astype(int)
        df["metricKG"] = pd.to_numeric(df["metricKG"], errors="coerce")
        df["metricFOB"] = pd.to_numeric(df["metricFOB"], errors="coerce")
        df["retrieved_at"] = utc_now_iso()
        frames.append(df)
        log.info("comexstat %d to %d: %d rows", chunk[0], chunk[-1], len(df))
        time.sleep(PAUSE_S)
    out = pd.concat(frames, ignore_index=True)
    out = out.sort_values(["year", "monthNumber", "headingCode", "country"]).reset_index(drop=True)
    out.to_csv(path, index=False)
    meta = DatasetMetadata(
        dataset_id=DATASET_ID,
        title="Brazil Comex Stat: monthly exports by destination country for iron ore, soybeans, maize "
        "and soybean meal",
        publisher="Ministerio do Desenvolvimento, Industria, Comercio e Servicos (MDIC), Brazil",
        source_url=PORTAL,
        licence="Open government data (Brazilian open data policy); attribution to MDIC/Comex Stat",
        frequency="monthly",
        coverage=f"{start_year}-01 to {last_year}-{last_month:02d} (last update {upd.get('updated')})",
        units="kilograms (metricKG) and FOB US dollars (metricFOB)",
        retrieved_at=utc_now_iso(),
        files=[RECORDS_FILE, COUNTRIES_FILE],
        limitations=[
            "Destination is the declared country of destination at export, which can differ from the final "
            "discharge port.",
            "Figures for the latest two months are preliminary and revised in later releases; the last two years "
            "are refreshed every run.",
        ],
        extra={"headings": HEADINGS, "api": f"{API}/general"},
    )
    write_metadata(meta)
    return out


def load() -> pd.DataFrame:
    """Load the cached export records with a Period month column."""
    df = pd.read_csv(raw_dir(DATASET_ID) / RECORDS_FILE, dtype={"headingCode": str})
    df["month"] = pd.PeriodIndex(
        pd.to_datetime(df["year"].astype(str) + "-" + df["monthNumber"].astype(int).astype(str).str.zfill(2)), freq="M"
    )
    return df
