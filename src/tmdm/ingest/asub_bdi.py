"""Baltic Dry Index, monthly, from Statistics and Research Aland (ASUB).

ASUB, the official statistics office of the Aland Islands (Finland), publishes
the Baltic Dry Index by year and month in its PxWeb database under
Tourism, transports and shipping, Shipping, Business cycle statistics for
shipping (table SJ102). The index is the Baltic Exchange composite of dry
bulk timecharter averages (1985 = 1,000). This is the freight index used
throughout the project because it is a public statistical source with a
documented API and a history back to 1985.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import quote

import pandas as pd

from tmdm.http import get, post_json
from tmdm.io import DatasetMetadata, raw_dir, utc_now_iso, write_metadata

log = logging.getLogger(__name__)

DATASET_ID = "asub_baltic_dry_index"
API = "https://pxweb.asub.ax/PXWeb/api/v1/en/Statistik"
TABLE_PATH = f"TUTRSJ/SJ/{quote('Konjunkturstatistik för sjöfarten')}/SJ102.px"
TABLE_URL = f"{API}/{TABLE_PATH}"
WEB_URL = "https://pxweb.asub.ax/PXWeb/pxweb/en/Statistik/Statistik__TUTRSJ__SJ__Konjunkturstatistik%20f%C3%B6r%20sj%C3%B6farten/SJ102.px/"
META_FILE = "sj102_metadata.json"
DATA_FILE = "sj102_jsonstat2.json"
CSV_FILE = "baltic_dry_index_monthly.csv"

MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def _find_table() -> str:
    """Return the table URL, crawling the folder if the expected path moved."""
    r = get(TABLE_URL)
    if r.status_code == 200 and "variables" in r.json():
        return TABLE_URL
    folder = get(f"{API}/TUTRSJ/SJ").json()
    for item in folder:
        if item.get("type") == "l":
            sub = get(f"{API}/TUTRSJ/SJ/{quote(item['id'])}").json()
            for t in sub:
                if "Baltic" in t.get("text", ""):
                    return f"{API}/TUTRSJ/SJ/{quote(item['id'])}/{t['id']}"
    raise RuntimeError("ASUB Baltic Dry Index table not found")


def _parse_jsonstat2(js: dict[str, Any]) -> pd.DataFrame:
    dims = js["id"]
    sizes = js["size"]
    cats = {d: js["dimension"][d]["category"] for d in dims}
    labels = {
        d: [cats[d]["label"][k] for k in sorted(cats[d]["index"], key=lambda k: cats[d]["index"][k])] for d in dims
    }
    values = js["value"]
    rows = []
    import itertools

    for idx, combo in enumerate(itertools.product(*[range(s) for s in sizes])):
        rec = {d: labels[d][i] for d, i in zip(dims, combo, strict=True)}
        rec["value"] = values[idx]
        rows.append(rec)
    df = pd.DataFrame(rows)
    year_col = next(c for c in df.columns if c.lower().startswith(("år", "year", "ar")))
    month_col = next(c for c in df.columns if c.lower().startswith(("månad", "month", "manad")))
    df["year"] = df[year_col].astype(int)
    df["month_num"] = df[month_col].str.lower().map(MONTHS)
    df = df.dropna(subset=["month_num", "value"])
    df["month"] = pd.PeriodIndex(
        pd.to_datetime(df["year"].astype(str) + "-" + df["month_num"].astype(int).astype(str).str.zfill(2)), freq="M"
    )
    out = df[["month", "value"]].rename(columns={"value": "bdi"}).sort_values("month").reset_index(drop=True)
    out["bdi"] = pd.to_numeric(out["bdi"], errors="coerce")
    return out.dropna(subset=["bdi"])


def fetch() -> pd.DataFrame:
    """Download table metadata and all values, store raw JSON and a tidy CSV."""
    d = raw_dir(DATASET_ID)
    url = _find_table()
    meta_json = get(url).json()
    (d / META_FILE).write_text(json.dumps(meta_json, indent=1, ensure_ascii=False), encoding="utf-8")
    query = {
        "query": [{"code": v["code"], "selection": {"filter": "all", "values": ["*"]}} for v in meta_json["variables"]],
        "response": {"format": "json-stat2"},
    }
    r = post_json(url, query)
    js = r.json()
    (d / DATA_FILE).write_text(json.dumps(js, ensure_ascii=False), encoding="utf-8")
    df = _parse_jsonstat2(js)
    df.to_csv(d / CSV_FILE, index=False)
    notes = [n.get("text", "") for n in meta_json.get("variables", []) if isinstance(n, dict) and n.get("text")]
    meta = DatasetMetadata(
        dataset_id=DATASET_ID,
        title=meta_json.get("title", "Baltic Dry Index by year and month"),
        publisher="Statistics and Research Aland (ASUB), official statistics of Aland",
        source_url=WEB_URL,
        licence="ASUB open statistics (free use with attribution); index values are produced by the Baltic Exchange",
        frequency="monthly",
        coverage=f"{df['month'].iloc[0]} to {df['month'].iloc[-1]}",
        units="index points, 1985 = 1,000",
        retrieved_at=utc_now_iso(),
        files=[META_FILE, DATA_FILE, CSV_FILE],
        limitations=[
            "A monthly figure for a daily index; the table documentation defines the monthly value "
            "(see metadata notes).",
            "Secondary publication of Baltic Exchange data; the Baltic Exchange is the originator of the index.",
        ],
        extra={"api_url": url, "variables": notes},
    )
    write_metadata(meta)
    log.info("asub bdi: %d months (%s to %s)", len(df), df["month"].iloc[0], df["month"].iloc[-1])
    return df


def load() -> pd.DataFrame:
    """Load the tidy monthly BDI."""
    df = pd.read_csv(raw_dir(DATASET_ID) / CSV_FILE)
    df["month"] = pd.PeriodIndex(df["month"], freq="M")
    return df
