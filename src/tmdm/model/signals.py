"""Market-signal overlay: public commodity prices that track the demand cycle the customs data miss.

The physical identity explains only a small part of twelve-month moves in the
Baltic Dry Index: the customs-based balance moves a few percent while the index
moves by tens of percent. Out-of-sample tests (see the methodology page) show
that two public World Bank prices carry much of the missing variation:

* the Brent crude price over the same window (bunker cost and the global
  commodity and industrial cycle);
* the iron ore price (CFR China) over the window ending three months earlier
  (China steel demand and restocking; the lead keeps the freight embedded in a
  CFR price out of the same window).

They are market signals, not physical forces: they react to the same demand
shocks as freight. They are therefore added as separate, named components
after the four physical ones, with the elasticity of the physical identity
left unchanged:

    total = volume + distance + fleet + effective_supply   (e x dlog, as before)
            + sum_k c_k x dlog P_k (window shifted by lag_k)
            + residual

The coefficients c_k come from OLS without intercept of the physical residual
on the price changes over every rolling twelve-month window, with Newey-West
standard errors. The residual is whatever is left, so the identity holds
exactly. Out-of-sample metrics refit every coefficient, including e, using only
windows that ended at least twelve months before the month predicted.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
import statsmodels.api as sm


@dataclass(frozen=True)
class Signal:
    """A price series used as a market signal, with the lead of its window in months."""

    name: str
    column: str
    lag: int
    label: str


DEFAULT_SIGNALS: tuple[Signal, ...] = (
    Signal("oil", "brent_usd_bbl", 0, "Oil price (Brent)"),
    Signal("iron_ore", "iron_ore_usd_dmtu", 3, "Iron ore price, 3 months earlier"),
)

MIN_TRAIN = 60  # windows needed before the first out-of-sample prediction
GAP = 12  # training windows must end at least this many months before the month predicted


def window_log_change(series: pd.Series, start: pd.Period, end: pd.Period, window: int, lag: int = 0) -> float:
    """Log change of the ``window``-month trailing mean between two months, both shifted ``lag`` months earlier."""
    s0, e0 = start - lag, end - lag
    a = series.loc[s0 - (window - 1) : s0]
    b = series.loc[e0 - (window - 1) : e0]
    if len(a) != window or len(b) != window or a.isna().any() or b.isna().any():
        return float("nan")
    return float(np.log(b.mean() / a.mean()))


def build_windows(
    panel: pd.DataFrame,
    prices: pd.DataFrame,
    elasticity: float,
    signals: Sequence[Signal] = DEFAULT_SIGNALS,
    horizon: int = 12,
    window: int = 3,
) -> pd.DataFrame:
    """One row per rolling window: index change, balance change, physical part and each signal's price change."""
    p = panel.sort_index()
    rows = []
    for end in p.index:
        start = end - horizon
        if start - (window - 1) < p.index[0]:
            continue
        y = window_log_change(p["index"], start, end, window)
        # balance change with window means of each input, as in the attribution
        a = p.loc[start - (window - 1) : start, ["tonne_miles", "fleet", "supply_factor"]].mean()
        b = p.loc[end - (window - 1) : end, ["tonne_miles", "fleet", "supply_factor"]].mean()
        db = float(
            np.log(b["tonne_miles"] / a["tonne_miles"])
            - np.log(b["fleet"] / a["fleet"])
            - np.log(b["supply_factor"] / a["supply_factor"])
        )
        row: dict[str, Any] = {"month": end, "y": y, "d_balance": db, "physical": elasticity * db}
        for s in signals:
            row[f"x_{s.name}"] = window_log_change(prices[s.column], start, end, window, s.lag)
        rows.append(row)
    return pd.DataFrame(rows).set_index("month")


@dataclass(frozen=True)
class SignalFit:
    """Coefficients of the signals on the physical residual."""

    coefficients: dict[str, float]
    std_errors: dict[str, float]
    t_stats: dict[str, float]
    n_obs: int
    r_squared_physical: float
    r_squared_extended: float
    unexplained_physical: float
    unexplained_extended: float
    sign_agreement_physical: float
    sign_agreement_extended: float

    def summary(self) -> dict[str, Any]:
        """JSON-friendly summary."""
        return self.__dict__.copy()


def _r2(y: np.ndarray, fit: np.ndarray) -> float:
    return float(1.0 - np.sum((y - fit) ** 2) / np.sum((y - y.mean()) ** 2))


def fit_signals(windows: pd.DataFrame, signals: Sequence[Signal] = DEFAULT_SIGNALS, hac_lags: int = 12) -> SignalFit:
    """OLS without intercept of (index change - physical part) on the signal price changes, HAC errors."""
    cols = [f"x_{s.name}" for s in signals]
    df = windows.dropna(subset=["y", "physical", *cols])
    if len(df) < 24:
        raise ValueError("need at least 24 windows with every signal to fit the market signals")
    res = sm.OLS(df["y"] - df["physical"], df[cols]).fit(cov_type="HAC", cov_kwds={"maxlags": hac_lags})
    y = df["y"].to_numpy()
    phys = df["physical"].to_numpy()
    ext = phys + res.fittedvalues.to_numpy()
    names = [s.name for s in signals]
    return SignalFit(
        coefficients={n: float(res.params[c]) for n, c in zip(names, cols, strict=True)},
        std_errors={n: float(res.bse[c]) for n, c in zip(names, cols, strict=True)},
        t_stats={n: float(res.tvalues[c]) for n, c in zip(names, cols, strict=True)},
        n_obs=len(df),
        r_squared_physical=_r2(y, phys),
        r_squared_extended=_r2(y, ext),
        unexplained_physical=float(np.mean(np.abs(y - phys)) / np.mean(np.abs(y))),
        unexplained_extended=float(np.mean(np.abs(y - ext)) / np.mean(np.abs(y))),
        sign_agreement_physical=float(np.mean(np.sign(phys) == np.sign(y))),
        sign_agreement_extended=float(np.mean(np.sign(ext) == np.sign(y))),
    )


def contributions(
    prices: pd.DataFrame,
    start: pd.Period,
    end: pd.Period,
    coefficients: dict[str, float],
    signals: Sequence[Signal] = DEFAULT_SIGNALS,
    window: int = 3,
) -> dict[str, float]:
    """Contribution of each signal (log points of the index) to the change between two months."""
    return {
        s.name: coefficients[s.name] * window_log_change(prices[s.column], start, end, window, s.lag) for s in signals
    }


def out_of_sample(
    panel: pd.DataFrame,
    prices: pd.DataFrame,
    estimate_e: Any,
    signals: Sequence[Signal] = DEFAULT_SIGNALS,
    horizon: int = 12,
    window: int = 3,
    min_train: int = MIN_TRAIN,
    gap: int = GAP,
) -> dict[str, Any]:
    """Expanding-window out-of-sample comparison of the physical model and the model with signals.

    For each month t after the first ``min_train`` windows, e is re-estimated
    with ``estimate_e(panel_up_to)`` on data ending at t - gap, the signal
    coefficients on windows ending at or before t - gap, and both models
    predict the window ending at t. A persistence benchmark (the index's own
    twelve-month change on the window ending three months earlier, scaled by
    its own coefficient) is reported for context.
    """
    # a window ending at t uses data up to t only, and its physical part is e times the balance change,
    # so the windows are built once and rescaled by each refitted e
    w_full = build_windows(panel, prices, 1.0, signals, horizon, window)
    months = list(w_full.index)
    if len(months) <= min_train + gap:
        return {"available": False, "reason": "too few windows for an out-of-sample test"}
    preds = []
    y_lag3 = w_full["y"].shift(3)
    for i, t in enumerate(months):
        if i < min_train + gap - 1:  # the first month with min_train windows ending at or before t - gap
            continue
        cutoff = t - gap
        e_t = float(estimate_e(panel.loc[:cutoff]))
        w = w_full.loc[:t].copy()
        w["physical"] = e_t * w["d_balance"]
        train = w.loc[:cutoff]
        if len(train) < min_train:
            continue
        fit = fit_signals(train, signals)
        row = w.loc[t]
        phys = float(row["physical"])
        ext = phys + sum(fit.coefficients[s.name] * float(row[f"x_{s.name}"]) for s in signals)
        tr = pd.DataFrame({"y": w_full["y"], "x": y_lag3}).loc[:cutoff].dropna()
        b = float(np.sum(tr["x"] * tr["y"]) / np.sum(tr["x"] ** 2)) if len(tr) else 0.0
        pers = b * float(y_lag3.loc[t]) if pd.notna(y_lag3.loc[t]) else float("nan")
        preds.append({"month": t, "y": float(row["y"]), "physical": phys, "extended": ext, "persistence": pers})
    df = pd.DataFrame(preds).set_index("month")
    y = df["y"].to_numpy()

    def r2_vs(model: str, base: np.ndarray) -> float:
        return float(1.0 - np.sum((y - df[model].to_numpy()) ** 2) / np.sum((y - base) ** 2))

    zero = np.zeros_like(y)
    out: dict[str, Any] = {
        "available": True,
        "first_month": str(df.index[0]),
        "last_month": str(df.index[-1]),
        "n_months": len(df),
        "r2_vs_zero": {m: r2_vs(m, zero) for m in ("physical", "extended", "persistence")},
        "extended_r2_vs_physical": r2_vs("extended", df["physical"].to_numpy()),
        "extended_r2_vs_persistence": r2_vs("extended", df["persistence"].to_numpy()),
        "unexplained": {
            m: float(np.mean(np.abs(y - df[m].to_numpy())) / np.mean(np.abs(y))) for m in ("physical", "extended")
        },
        "sign_agreement": {
            m: float(np.mean(np.sign(df[m].to_numpy()) == np.sign(y))) for m in ("physical", "extended")
        },
    }
    years = pd.PeriodIndex(df.index).year
    sub = {}
    for label, mask in (
        ("2020", years == 2020),
        ("2021-22", (years >= 2021) & (years <= 2022)),
        ("2023 onward", years >= 2023),
        ("excluding 2021-22", (years < 2021) | (years > 2022)),
    ):
        if mask.sum() >= 6:
            yy, ph, ex = y[mask], df["physical"].to_numpy()[mask], df["extended"].to_numpy()[mask]
            sub[label] = float(1.0 - np.sum((yy - ex) ** 2) / np.sum((yy - ph) ** 2))
    out["extended_r2_vs_physical_by_period"] = sub
    out["predictions"] = df.reset_index().assign(month=lambda d: d["month"].astype(str)).to_dict(orient="records")
    return out
