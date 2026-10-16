"""World Bank Commodity Markets Outlook (Pink Sheet) monthly prices.

Used as market context and as a cross-check on the dry bulk cycle: iron ore,
coal and grain prices move with the same demand shocks that drive freight.
The file is the official monthly historical data workbook.
"""

from __future__ import annotations

import logging

import pandas as pd

from tmdm.http import get
from tmdm.io import DatasetMetadata, raw_dir, utc_now_iso, write_metadata

log = logging.getLogger(__name__)

DATASET_ID = "worldbank_cmo"
URL = "https://thedocs.worldbank.org/en/doc/5d903e848db1d1b83e0ec8f744e55570-0350012021/related/CMO-Historical-Data-Monthly.xlsx"
FILE = "CMO-Historical-Data-Monthly.xlsx"

# Column headers in the "Monthly Prices" sheet and the names used downstream.
SERIES = {
    "Iron ore, cfr spot": "iron_ore_usd_dmtu",
    "Coal, Australian": "coal_australia_usd_t",
    "Coal, South African **": "coal_south_africa_usd_t",
    "Wheat, US HRW": "wheat_us_hrw_usd_t",
    "Maize": "maize_usd_t",
    "Soybeans": "soybeans_usd_t",
}


def fetch() -> None:
    """Download the workbook and write the metadata record."""
    d = raw_dir(DATASET_ID)
    r = get(URL, timeout=180)
    (d / FILE).write_bytes(r.content)
    meta = DatasetMetadata(
        dataset_id=DATASET_ID,
        title="World Bank Commodity Markets Outlook, monthly prices (Pink Sheet)",
        publisher="World Bank",
        source_url=URL,
        licence="Creative Commons Attribution 4.0 (CC BY 4.0), as stated by the World Bank for its datasets",
        frequency="monthly",
        coverage="1960 onward, nominal US dollars",
        units="US dollars per metric ton (iron ore: per dry metric ton unit)",
        retrieved_at=utc_now_iso(),
        files=[FILE],
        limitations=[
            "Prices are benchmark assessments, not transaction volumes; used only as market context and a cross-check.",
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
    for i in range(0, 12):
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
