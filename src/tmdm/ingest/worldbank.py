"""World Bank Commodity Markets Outlook (Pink Sheet) monthly prices.

Iron ore, coal and grain prices move with the same demand shocks that drive
freight, and the crude oil price is the public proxy for bunker fuel cost.
The file is the official monthly historical data workbook. The World Bank
publishes it under a new address every year, so the current link is read from
the Commodity Markets page on each run, with the last known address as a
fallback.
"""

from __future__ import annotations

import logging
import re

import pandas as pd

from tmdm.http import get
from tmdm.io import DatasetMetadata, raw_dir, utc_now_iso, write_metadata

log = logging.getLogger(__name__)

DATASET_ID = "worldbank_cmo"
PAGE = "https://www.worldbank.org/en/research/commodity-markets"
# last known address (published January 2026); used only if the page cannot be read
URL = "https://thedocs.worldbank.org/en/doc/74e8be41ceb20fa0da750cda2f6b9e4e-0050012026/related/CMO-Historical-Data-Monthly.xlsx"
FILE = "CMO-Historical-Data-Monthly.xlsx"
LINK_RE = re.compile(r"https://thedocs\.worldbank\.org/en/doc/[0-9a-f]+-\d+/related/CMO-Historical-Data-Monthly\.xlsx")

# Column headers in the "Monthly Prices" sheet and the names used downstream.
SERIES = {
    "Crude oil, Brent": "brent_usd_bbl",
    "Iron ore, cfr spot": "iron_ore_usd_dmtu",
    "Coal, Australian": "coal_australia_usd_t",
    "Coal, South African **": "coal_south_africa_usd_t",
    "Wheat, US HRW": "wheat_us_hrw_usd_t",
    "Maize": "maize_usd_t",
    "Soybeans": "soybeans_usd_t",
    "Natural gas, Europe": "natural_gas_europe_usd_mmbtu",
}


def current_url() -> str:
    """Address of the current monthly workbook, read from the Commodity Markets page."""
    try:
        html = get(PAGE, timeout=60).text
        links = sorted(set(LINK_RE.findall(html)))
        if links:
            return str(links[0])
        log.warning("worldbank cmo: no workbook link on %s; using the last known address", PAGE)
    except Exception as exc:  # noqa: BLE001 - fall back to the last known address
        log.warning("worldbank cmo: could not read %s (%s); using the last known address", PAGE, exc)
    return URL


def fetch() -> None:
    """Download the workbook and write the metadata record."""
    d = raw_dir(DATASET_ID)
    url = current_url()
    r = get(url, timeout=180)
    (d / FILE).write_bytes(r.content)
    last = load().index.max()
    meta = DatasetMetadata(
        dataset_id=DATASET_ID,
        title="World Bank Commodity Markets Outlook, monthly prices (Pink Sheet)",
        publisher="World Bank",
        source_url=url,
        licence="Creative Commons Attribution 4.0 (CC BY 4.0), as stated by the World Bank for its datasets",
        frequency="monthly",
        coverage=f"1960-01 to {last}, nominal US dollars",
        units="US dollars per metric ton (iron ore: per dry metric ton unit; Brent: per barrel; gas: per mmbtu)",
        retrieved_at=utc_now_iso(),
        files=[FILE],
        limitations=[
            "Prices are benchmark assessments, not transaction volumes.",
            "Brent crude is a proxy for marine bunker fuel prices, which are not published free of charge.",
            "Series definitions change occasionally (for example the coal benchmark basis); the workbook "
            "footnotes apply.",
        ],
    )
    write_metadata(meta)
    log.info("worldbank cmo: saved %s (%d bytes)", FILE, len(r.content))


def load() -> pd.DataFrame:
    """Parse the monthly price sheet into a tidy frame indexed by month."""
    path = raw_dir(DATASET_ID) / FILE
    raw = pd.read_excel(path, sheet_name="Monthly Prices", header=None)
    # Row 4 holds series names, row 5 units, data starts at row 6; column 0 is the period as YYYYMmm.
    header_row = None
    for i in range(0, 15):
        vals = [str(v) for v in raw.iloc[i].tolist()]
        if any("Iron ore" in v for v in vals):
            header_row = i
            break
    if header_row is None:
        raise ValueError("could not locate the header row in the Pink Sheet")
    names = [str(v).strip() for v in raw.iloc[header_row].tolist()]
    data = raw.iloc[header_row + 2 :].copy()
    data.columns = names
    period_col = names[0]
    data = data[data[period_col].astype(str).str.match(r"^\d{4}M\d{2}$")]
    out = pd.DataFrame({"month": pd.PeriodIndex(data[period_col].astype(str).str.replace("M", "-"), freq="M")})
    for src, dst in SERIES.items():
        if src in data.columns:
            out[dst] = pd.to_numeric(data[src].to_numpy(), errors="coerce")
        else:
            log.warning("worldbank cmo: series %r not found", src)
    return out.set_index("month")
