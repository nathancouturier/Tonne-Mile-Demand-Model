"""Model stage: elasticity, rolling attribution, episodes and robustness checks.

Reads ``data/processed/panel_monthly.csv`` and writes:

* ``model_elasticity.json``: headline twelve-month change regression and robustness variants.
* ``model_difference_regression.json``: unrestricted first-difference regression.
* ``decomposition_rolling.csv``: 12-month attribution for every month.
* ``decomposition_episodes.json``: attribution for each covered named episode.
* ``model_fitted.csv``: twelve-month log changes of single months: actual, implied by the balance, fitted
  (with the regression intercept) and residual.
* ``haul_split_rolling.csv``: trade-mix versus rerouting split of the haul effect.
* ``model_signals.json``: market-signal coefficients and the out-of-sample comparison.
* ``model_extensions.json``: extensions tested against the headline (minor bulks, leading indicators).
* ``model_long.json``, ``long_attribution_annual.csv``: the annual decomposition since 1980.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import numpy as np
import pandas as pd

from tmdm.config import PROCESSED_DIR
from tmdm.model.decomposition import run_model
from tmdm.model.extensions import run_extensions
from tmdm.transform.tonne_miles import haul_split

log = logging.getLogger(__name__)

WINDOW = 3
HORIZON = 12


def load_prices() -> pd.DataFrame | None:
    """Monthly market-signal prices, or None before the transform stage has written them."""
    path = PROCESSED_DIR / "signals_monthly.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path)
    df["month"] = pd.PeriodIndex(df["month"], freq="M")
    return df.set_index("month")


def load_minor_bulks() -> pd.DataFrame | None:
    """Monthly minor-bulk tonnes and tonne-miles, or None before the transform stage has written them."""
    path = PROCESSED_DIR / "minor_bulks_monthly.csv"
    if not path.exists():
        return None
    df = pd.read_csv(path)
    df["month"] = pd.PeriodIndex(df["month"], freq="M")
    return df


def run_long() -> None:
    """Annual long-history model, when the transform stage has built its panel."""
    from tmdm.model.long_history import run_long_model

    path = PROCESSED_DIR / "long_panel_annual.csv"
    out_path = PROCESSED_DIR / "model_long.json"
    if not path.exists():
        out_path.unlink(missing_ok=True)
        (PROCESSED_DIR / "long_attribution_annual.csv").unlink(missing_ok=True)
        return
    panel = pd.read_csv(path)
    prices = pd.read_csv(PROCESSED_DIR / "long_prices_annual.csv").set_index("year")
    out = run_long_model(panel, prices)
    out_path.write_text(json.dumps(_json_ready(out), indent=2))
    pd.DataFrame(out["annual"]).to_csv(PROCESSED_DIR / "long_attribution_annual.csv", index=False)
    log.info(
        "long model: %d to %d, elasticity %.2f (se %.2f)",
        int(panel["year"].min()),
        int(panel["year"].max()),
        out["elasticity"]["elasticity"],
        out["elasticity"]["std_error"],
    )


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
        # tonne-weighted distance per route, so that sum(t * d) equals the window mean of tonne-miles and
        # the split adds up to the same haul change as the decomposition (which uses window means of totals)
        sub = by_route[(by_route["month"] > end - window) & (by_route["month"] <= end)]
        g = sub.groupby("route_id")
        t = g["tonnes"].mean()
        tm = g["tonne_miles"].mean()
        d = (tm / t.where(t > 0)).fillna(g["distance_nm"].mean())
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
    prices = load_prices()
    out = run_model(panel, window=WINDOW, horizon=HORIZON, prices=prices)
    if out.signals:
        (PROCESSED_DIR / "model_signals.json").write_text(json.dumps(_json_ready(out.signals), indent=2))
    if prices is not None:
        ext = run_extensions(panel, prices, load_minor_bulks(), HORIZON, WINDOW)
        (PROCESSED_DIR / "model_extensions.json").write_text(json.dumps(_json_ready(ext), indent=2))
    run_long()
    (PROCESSED_DIR / "model_elasticity.json").write_text(
        json.dumps(
            {
                "headline": out.elasticity.summary(),
                "robustness": _json_ready(out.elasticity_robustness),
                "window_months": WINDOW,
                "horizon_months": HORIZON,
                "panel_window": [str(panel.index[0]), str(panel.index[-1])],
                "panel_months": len(panel),
                "n_obs": out.elasticity.n_obs,
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
    # Twelve-month changes of single months: the data behind the headline regression.
    # implied_dlog12 is the elasticity times the change in the balance (what the decomposition attributes to
    # the four forces); fitted_dlog12 adds the regression intercept, a drift that the decomposition leaves in
    # the residual. residual_dlog12 is actual minus implied, the same definition as the decomposition.
    log_index = np.log(panel["index"])
    balance = np.log(panel["tonne_miles"] / (panel["fleet"] * panel["supply_factor"]))
    d12 = pd.DataFrame({"actual_dlog12": log_index.diff(12), "balance_dlog12": balance.diff(12)}).dropna()
    e = out.elasticity
    d12["implied_dlog12"] = e.elasticity * d12["balance_dlog12"]
    d12["fitted_dlog12"] = e.intercept + d12["implied_dlog12"]
    d12["residual_dlog12"] = d12["actual_dlog12"] - d12["implied_dlog12"]
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
