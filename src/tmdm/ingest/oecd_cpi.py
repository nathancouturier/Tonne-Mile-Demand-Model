"""US consumer price index, monthly, through the OECD SDMX API.

Used only to convert the Kilian index, which is deflated by US consumer
prices, back to nominal terms for the long-history freight series.
"""

from __future__ import annotations

import io
import logging

import pandas as pd

from tmdm.http import get
from tmdm.io import DatasetMetadata, raw_dir, utc_now_iso, write_metadata

log = logging.getLogger(__name__)

DATASET_ID = "oecd_cpi_usa"
API = "https://sdmx.oecd.org/public/rest/data/OECD.SDD.TPS,DSD_PRICES@DF_PRICES_ALL,1.0/USA.M.N.CPI.IX._T.N._Z"
FILE = "cpi_usa_monthly.csv"
START = "1960-01"


def fetch() -> pd.DataFrame:
    """Download the monthly US CPI (index, not seasonally adjusted) and save it with a metadata record."""
    d = raw_dir(DATASET_ID)
    r = get(API, params={"startPeriod": START, "format": "csvfilewithlabels"}, timeout=180)
    raw = pd.read_csv(io.StringIO(r.text))
    out = raw[["TIME_PERIOD", "OBS_VALUE"]].rename(columns={"TIME_PERIOD": "month", "OBS_VALUE": "cpi"})
    out = out.dropna().sort_values("month").reset_index(drop=True)
    base = str(raw["BASE_PER"].dropna().iloc[0]) if "BASE_PER" in raw and raw["BASE_PER"].notna().any() else ""
    out.to_csv(d / FILE, index=False)
    write_metadata(metadata(out, base, utc_now_iso()))
    log.info("oecd cpi usa: %d months", len(out))
    return out


def missing_months(months: pd.Series) -> list[str]:
    """Months between the first and the last observation that the release does not contain."""
    idx = pd.PeriodIndex(months.astype(str), freq="M")
    return [str(m) for m in pd.period_range(idx.min(), idx.max(), freq="M").difference(idx)]


def metadata(out: pd.DataFrame, base: str, retrieved_at: str) -> DatasetMetadata:
    """Metadata record for the saved series, listing any month missing from the release."""
    gaps = missing_months(out["month"])
    coverage = f"{out['month'].iloc[0]} to {out['month'].iloc[-1]}"
    limitations = ["Used only to convert the Kilian freight index back to nominal terms."]
    if gaps:
        coverage += f", except {', '.join(gaps)}"
        limitations.append(
            f"Months not in the release: {', '.join(gaps)}. A year with a missing month has no full-year proxy, and "
            "the calibration overlap is shorter by those months."
        )
    return DatasetMetadata(
        dataset_id=DATASET_ID,
        title="Consumer price index, United States, all items (OECD Prices database)",
        publisher="OECD (source: US Bureau of Labor Statistics)",
        source_url="https://data-explorer.oecd.org/",
        licence="OECD terms and conditions (free use with attribution)",
        frequency="monthly",
        coverage=coverage,
        units=f"index{', base ' + base if base else ''}, not seasonally adjusted",
        retrieved_at=retrieved_at,
        files=[FILE],
        limitations=limitations,
        extra={"api": API},
    )


def load() -> pd.Series:
    """Monthly US CPI indexed by month."""
    df = pd.read_csv(raw_dir(DATASET_ID) / FILE)
    return pd.Series(df["cpi"].to_numpy(), index=pd.PeriodIndex(df["month"], freq="M"), name="cpi_usa")
