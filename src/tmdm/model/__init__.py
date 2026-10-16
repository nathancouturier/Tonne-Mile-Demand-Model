"""Model stage: elasticity, rolling attribution, episodes and robustness checks.

Reads ``data/processed/panel_monthly.csv`` and writes:

* ``model_elasticity.json``: headline levels regression and robustness variants.
* ``model_difference_regression.json``: unrestricted first-difference regression.
* ``decomposition_rolling.csv``: 12-month attribution for every month.
* ``decomposition_episodes.json``: attribution for each covered named episode.
* ``model_fitted.csv``: fitted index from the levels regression and residuals.
* ``haul_split_rolling.csv``: trade-mix versus rerouting split of the haul effect.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import numpy as np
import pandas as pd

from tmdm.config import PROCESSED_DIR
from tmdm.model.decomposition import run_model
from tmdm.transform.tonne_miles import haul_split

log = logging.getLogger(__name__)

WINDOW = 3
HORIZON = 12


def load_panel() -> pd.DataFrame:
    """Load the monthly panel with a Period index."""
    p = pd.read_csv(PROCESSED_DIR / "panel_monthly.csv")
    p["month"] = pd.PeriodIndex(p["month"], freq="M")
    return p.set_index("month")


def _json_ready(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {str(k): _json_ready(v) for k, v in obj.items() if k != "residuals"}
    if isinstance(obj, list | tuple):
        return [_json_ready(v) for v in obj]
    if isinstance(obj, pd.Period):
        return str(obj)
    if hasattr(obj, "item"):
        return obj.item()
    return obj


def rolling_haul_split(horizon: int, window: int) -> pd.DataFrame:
    """Split the 12-month haul change into mix and rerouting for each month."""
    by_route = pd.read_csv(PROCESSED_DIR / "tonne_miles_by_route.csv")
    by_route["month"] = pd.PeriodIndex(by_route["month"], freq="M")
    months = sorted(by_route["month"].unique())
    rows = []

    def window_mean(end: pd.Period) -> tuple[pd.Series, pd.Series]:
        sub = by_route[(by_route["month"] > end - window) & (by_route["month"] <= end)]
        t = sub.groupby("route_id")["tonnes"].mean()
        d = sub.groupby("route_id")["distance_nm"].mean()
        return t, d

    for end in months:
        start = end - horizon
        if start - (window - 1) < months[0]:
            continue
        t0, d0 = window_mean(start)
        t1, d1 = window_mean(end)
        dlog, mix, reroute = haul_split(t0, t1, d0, d1)
        rows.append({"month": end, "dlog_haul": dlog, "mix": mix, "rerouting": reroute})
    return pd.DataFrame(rows)


def run() -> None:
    """Fit the model and write every output."""
    panel = load_panel()
    out = run_model(panel, window=WINDOW, horizon=HORIZON)
    (PROCESSED_DIR / "model_elasticity.json").write_text(
        json.dumps(
            {
                "headline": out.elasticity.summary(),
                "robustness": _json_ready(out.elasticity_robustness),
                "window_months": WINDOW,
                "horizon_months": HORIZON,
                "panel_window": [str(panel.index[0]), str(panel.index[-1])],
                "n_obs": len(panel),
            },
            indent=2,
        )
    )
    (PROCESSED_DIR / "model_difference_regression.json").write_text(
        json.dumps(_json_ready(out.difference_regression), indent=2)
    )
    roll = out.rolling.copy()
    for c in ("month", "start", "end"):
        roll[c] = roll[c].astype(str)
    roll.to_csv(PROCESSED_DIR / "decomposition_rolling.csv", index=False)
    (PROCESSED_DIR / "decomposition_episodes.json").write_text(json.dumps(_json_ready(out.episodes), indent=2))
    # Twelve-month change actual versus implied by the balance (the headline specification)
    log_index = np.log(panel["index"])
    balance = np.log(panel["tonne_miles"] / (panel["fleet"] * panel["supply_factor"]))
    d12 = pd.DataFrame({"actual_dlog12": log_index.diff(12), "balance_dlog12": balance.diff(12)}).dropna()
    e = out.elasticity
    d12["implied_dlog12"] = e.intercept + e.elasticity * d12["balance_dlog12"]
    d12["residual"] = d12["actual_dlog12"] - d12["implied_dlog12"]
    d12.insert(0, "month", [str(m) for m in d12.index])
    d12.to_csv(PROCESSED_DIR / "model_fitted.csv", index=False)
    hs = rolling_haul_split(HORIZON, WINDOW)
    hs["month"] = hs["month"].astype(str)
    hs.to_csv(PROCESSED_DIR / "haul_split_rolling.csv", index=False)
    log.info(
        "model: elasticity %.2f (se %.2f, R2 %.2f), %d episodes, %d rolling months",
        out.elasticity.elasticity,
        out.elasticity.std_error,
        out.elasticity.r_squared,
        len(out.episodes),
        len(out.rolling),
    )
