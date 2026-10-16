"""Fleet supply series: annual stock to monthly, with explicit extrapolation flags.

UNCTAD publishes the merchant fleet (deadweight tonnes) as of 1 January each
year. The monthly series is obtained by log-linear interpolation between
consecutive 1 January observations, which assumes a constant growth rate
within each year. Months after the last observation are extrapolated at the
last observed annual growth rate and flagged, so that the site and report can
label them.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def annual_to_monthly_loglinear(annual: pd.Series, end_month: str) -> pd.DataFrame:
    """Interpolate a 1 January annual stock series to month-start values.

    Args:
        annual: Series indexed by year (int) with the stock as of 1 January.
        end_month: Last month to produce, ``YYYY-MM``.

    Returns:
        Frame with columns ``month`` (Period[M]), ``value`` and ``extrapolated``.
    """
    s = annual.dropna().sort_index()
    if len(s) < 2:
        raise ValueError("need at least two annual observations")
    if (s <= 0).any():
        raise ValueError("stock values must be positive for log interpolation")
    years = s.index.to_numpy(dtype=int)
    logs = np.log(s.to_numpy(dtype=float))
    # month index as fractional years measured from 1 January of the first year
    first = pd.Period(f"{years[0]}-01", freq="M")
    last = pd.Period(end_month, freq="M")
    months = pd.period_range(first, last, freq="M")
    x = np.array([(m.year - years[0]) + (m.month - 1) / 12.0 for m in months])
    known_x = (years - years[0]).astype(float)
    interp = np.interp(x, known_x, logs)
    last_growth = (logs[-1] - logs[-2]) / (known_x[-1] - known_x[-2])
    beyond = x > known_x[-1]
    interp[beyond] = logs[-1] + last_growth * (x[beyond] - known_x[-1])
    out = pd.DataFrame({"month": months, "value": np.exp(interp), "extrapolated": beyond})
    return out
