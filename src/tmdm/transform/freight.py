"""Freight index series: monthly Baltic Dry Index from the Aland statistics office."""

from __future__ import annotations

import pandas as pd

from tmdm.ingest import asub_bdi


def monthly_bdi() -> pd.DataFrame:
    """Return the monthly BDI with a Period index and a continuity check."""
    df = asub_bdi.load().set_index("month").sort_index()
    full = pd.period_range(df.index[0], df.index[-1], freq="M")
    out = df.reindex(full)
    out.index.name = "month"
    out["bdi"] = pd.to_numeric(out["bdi"], errors="coerce")
    return out.reset_index()
