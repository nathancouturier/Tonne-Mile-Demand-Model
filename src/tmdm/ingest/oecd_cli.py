"""OECD Composite Leading Indicators (CLI), monthly, through the OECD SDMX API.

The CLI is built from survey, production and order data designed to lead the
business cycle by six to nine months, without freight rates among its inputs.
It is tested here as a demand-cycle signal for the dry bulk market (China,
G20 and OECD total), as a volume-and-survey based alternative to commodity
prices. Values are amplitude adjusted, long-term average = 100.
"""

from __future__ import annotations

import io
import logging

import pandas as pd

from tmdm.http import get
from tmdm.io import DatasetMetadata, raw_dir, utc_now_iso, write_metadata

log = logging.getLogger(__name__)

DATASET_ID = "oecd_cli"
API = "https://sdmx.oecd.org/public/rest/data/OECD.SDD.STES,DSD_STES@DF_CLI,4.1/.M.LI...AA...H"
FILE = "cli_monthly.csv"
START = "2005-01"
AREAS = {"CHN": "China", "G20": "G20", "OECD": "OECD total"}


def fetch() -> pd.DataFrame:
    """Download the amplitude-adjusted CLI for every area and save it with a metadata record."""
    d = raw_dir(DATASET_ID)
    r = get(API, params={"startPeriod": START, "format": "csvfilewithlabels"}, timeout=180)
    raw = pd.read_csv(io.StringIO(r.text))
    keep = [c for c in ("REF_AREA", "Reference area", "MEASURE", "ADJUSTMENT", "TIME_PERIOD", "OBS_VALUE") if c in raw]
    out = raw[keep].sort_values(["REF_AREA", "TIME_PERIOD"]).reset_index(drop=True)
    out.to_csv(d / FILE, index=False)
    months = out["TIME_PERIOD"].astype(str)
    write_metadata(
        DatasetMetadata(
            dataset_id=DATASET_ID,
            title="OECD Composite Leading Indicators, amplitude adjusted (long-term average = 100)",
            publisher="Organisation for Economic Co-operation and Development (OECD)",
            source_url=f"{API}?startPeriod={START}&format=csvfilewithlabels",
            licence="OECD terms and conditions (free reuse with attribution, CC BY 4.0 for OECD data)",
            frequency="monthly",
            coverage=f"{months.min()} to {months.max()}, {out['REF_AREA'].nunique()} areas",
            units="index, long-term average = 100",
            retrieved_at=utc_now_iso(),
            files=[FILE],
            limitations=[
                "Recent months are revised as the cycle is re-estimated; each refresh stores the current vintage.",
                "A leading indicator of the general business cycle, not of dry bulk trade specifically.",
            ],
        )
    )
    log.info("oecd cli: %d rows, %d areas, %s to %s", len(out), out["REF_AREA"].nunique(), months.min(), months.max())
    return out


def load(areas: tuple[str, ...] = tuple(AREAS)) -> pd.DataFrame:
    """Monthly CLI by area, one column per area (cli_chn, cli_g20, cli_oecd), indexed by month."""
    df = pd.read_csv(raw_dir(DATASET_ID) / FILE)
    df = df[df["REF_AREA"].isin(areas)]
    piv = df.pivot_table(index="TIME_PERIOD", columns="REF_AREA", values="OBS_VALUE", aggfunc="first")
    piv.index = pd.PeriodIndex(piv.index.astype(str), freq="M")
    piv.columns = [f"cli_{str(c).lower()}" for c in piv.columns]
    return piv.sort_index()
