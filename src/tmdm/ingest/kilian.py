"""Kilian index of global real economic activity (Federal Reserve Bank of Dallas), monthly since 1968.

The index is built from a panel of single-voyage dry cargo ocean freight rates,
deflated by the US consumer price index and expressed as a percent deviation
from a linear trend (Kilian 2009, 2019). It is the only public monthly series
of dry bulk freight rates found that reaches back before 2011, the first year
of the public Baltic Dry Index table. The long-history decomposition uses it,
converted back to nominal terms and calibrated on the years where both series
exist, as a clearly labelled proxy for the Baltic Dry Index before 2011.
"""

from __future__ import annotations

import io
import logging

import pandas as pd

from tmdm.http import get
from tmdm.io import DatasetMetadata, raw_dir, utc_now_iso, write_metadata

log = logging.getLogger(__name__)

DATASET_ID = "dallasfed_kilian_index"
URL = "https://www.dallasfed.org/-/media/Documents/research/igrea/igrea.xlsx"
PAGE = "https://www.dallasfed.org/research/igrea"
XLSX = "igrea.xlsx"
CSV = "kilian_index_monthly.csv"


def parse(content: bytes) -> pd.DataFrame:
    """Month and index value from the Dallas Fed workbook (first sheet: date, value)."""
    df = pd.read_excel(io.BytesIO(content), header=0)
    df = df.iloc[:, :2]
    df.columns = ["date", "kilian_index"]
    df = df.dropna()
    df["month"] = pd.PeriodIndex(pd.to_datetime(df["date"]), freq="M").astype(str)
    out = df[["month", "kilian_index"]].astype({"kilian_index": float})
    if out["month"].duplicated().any():
        raise ValueError("duplicate months in the Kilian index workbook")
    return out.reset_index(drop=True)


def fetch() -> pd.DataFrame:
    """Download the workbook, keep it as published, and write a tidy monthly CSV with metadata."""
    d = raw_dir(DATASET_ID)
    r = get(URL, timeout=120)
    (d / XLSX).write_bytes(r.content)
    out = parse(r.content)
    out.to_csv(d / CSV, index=False)
    write_metadata(
        DatasetMetadata(
            dataset_id=DATASET_ID,
            title="Index of global real economic activity (Kilian index), based on dry cargo ocean freight rates",
            publisher="Federal Reserve Bank of Dallas (index by Lutz Kilian)",
            source_url=PAGE,
            licence="Public research data of the Federal Reserve Bank of Dallas; cite Kilian (2009, 2019)",
            frequency="monthly",
            coverage=f"{out['month'].iloc[0]} to {out['month'].iloc[-1]}",
            units="percent deviation of real (US CPI deflated) freight rates from a linear trend",
            retrieved_at=utc_now_iso(),
            files=[XLSX, CSV],
            limitations=[
                "A real, detrended index of dry cargo single-voyage freight rates, not the Baltic Dry Index; "
                "the project converts it back to nominal terms with the US CPI and estimates the trend on the "
                "years where both series exist (2011 onward).",
                "The trend and the scaling are re-estimated by the publisher with each update, so past values "
                "can change slightly.",
            ],
            extra={"workbook_url": URL},
        )
    )
    log.info("kilian: %d months, %s to %s", len(out), out["month"].iloc[0], out["month"].iloc[-1])
    return out


def load() -> pd.Series:
    """Monthly Kilian index indexed by month."""
    df = pd.read_csv(raw_dir(DATASET_ID) / CSV)
    return pd.Series(df["kilian_index"].to_numpy(), index=pd.PeriodIndex(df["month"], freq="M"), name="kilian_index")
