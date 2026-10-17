"""Long-history model: the decomposition on annual data since 1980.

The identity is the same as in the monthly model, applied to calendar-year
averages:

    log(I_t / I_s) = e [dlog tonnes + dlog haul] - e [dlog fleet + dlog supply factor]
                     + c_oil dlog Brent + c_ore dlog iron ore (twelve months to September)
                     + residual

The elasticity e is estimated on annual log changes (Newey-West errors over two
lags); the signal coefficients come from a regression without intercept of the
physical residual on the annual price changes; and an expanding-window test
refits every coefficient with data up to the previous year and predicts the
next. Episodes compare annual averages of two years.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
import statsmodels.api as sm

from tmdm.model.attribution import ExtendedAttribution, attribute
from tmdm.model.regression import estimate_elasticity
from tmdm.model.signals import fit_signals

HAC_LAGS = 2
MIN_TRAIN = 15  # annual changes before the first out-of-sample prediction
SIGNAL_COLUMNS = {"oil": "brent_usd_bbl", "iron_ore": "iron_ore_usd_dmtu_lead3"}
SIGNAL_LABELS = {"oil": "Oil price (Brent)", "iron_ore": "Iron ore price, twelve months to September"}


@dataclass(frozen=True)
class LongEpisode:
    """A multi-year episode compared on annual averages."""

    episode_id: str
    name: str
    start: int
    end: int
    description: str


LONG_EPISODES: tuple[LongEpisode, ...] = (
    LongEpisode("slump_1980s", "The 1980s slump", 1980, 1986, "From the 1980 average to the 1986 low."),
    LongEpisode("recovery_to_1995", "Recovery to 1995", 1986, 1995, "From the 1986 low to the 1995 high."),
    LongEpisode("asian_crisis", "Asian financial crisis", 1995, 1999, "The 1997 and 1998 crisis in East Asia."),
    LongEpisode("boom_2000s", "The 2000s boom", 2002, 2007, "From the 2002 low to the 2007 annual high."),
    LongEpisode("crash_2008", "The 2008 crash", 2007, 2009, "The financial crisis and the fall in trade."),
    LongEpisode("delivery_wave", "The delivery wave", 2010, 2016, "Record fleet deliveries after 2009."),
    LongEpisode("boom_2021", "Recovery and the 2021 boom", 2016, 2021, "From the 2016 low to the 2021 high."),
    LongEpisode("after_2021", "After 2021", 2021, 2025, "Normalisation after the pandemic boom."),
)


def _num(x: Any) -> float:
    return float(x)


def _logs(panel: pd.DataFrame) -> pd.DataFrame:
    p = panel.set_index("year").sort_index()
    return pd.DataFrame(
        {
            "y": np.log(p["index"]),
            "balance": np.log(p["tonne_miles"] / (p["fleet"] * p["supply_factor"])),
        },
        index=p.index,
    )


def elasticity(panel: pd.DataFrame) -> Any:
    """Annual log change of the index on the annual log change of the balance."""
    lg = _logs(panel).diff().dropna()
    return estimate_elasticity(lg["y"], lg["balance"], hac_lags=HAC_LAGS)


def _ols_slope(y: pd.Series, x: pd.Series) -> dict[str, float]:
    """Slope of y on x with an intercept and HAC errors, for sub-samples shorter than the main estimator allows."""
    df = pd.concat([y.rename("y"), x.rename("x")], axis=1).dropna()
    res = sm.OLS(df["y"], sm.add_constant(df[["x"]])).fit(cov_type="HAC", cov_kwds={"maxlags": HAC_LAGS})
    return {
        "elasticity": float(res.params["x"]),
        "std_error": float(res.bse["x"]),
        "r_squared": float(res.rsquared),
        "n_obs": int(res.nobs),
    }


def robustness(panel: pd.DataFrame) -> dict[str, Any]:
    """Elasticity on the proxy years, on the Baltic Dry Index years and on each half."""
    p = panel.set_index("year").sort_index()
    lg = _logs(panel).diff().dropna()
    proxy_both = p["proxy"] & p["proxy"].shift(1).fillna(True).astype(bool)
    out: dict[str, Any] = {}
    masks = {
        "proxy_years": proxy_both.reindex(lg.index).fillna(False).astype(bool),
        "bdi_years": (~p["proxy"] & ~p["proxy"].shift(1).fillna(True).astype(bool))
        .reindex(lg.index)
        .fillna(False)
        .astype(bool),
    }
    half = len(lg) // 2
    masks["first_half"] = pd.Series([i < half for i in range(len(lg))], index=lg.index)
    masks["second_half"] = ~masks["first_half"]
    for k, m in masks.items():
        if int(m.sum()) >= 10:
            out[k] = _ols_slope(lg.loc[m, "y"], lg.loc[m, "balance"]) | {
                "years": [int(lg.index[m][0]), int(lg.index[m][-1])]
            }
    return out


def windows(panel: pd.DataFrame, prices: pd.DataFrame, e: float) -> pd.DataFrame:
    """One row per annual change: index change, balance change, physical part and price changes."""
    lg = _logs(panel).diff()
    lp = prices[list(SIGNAL_COLUMNS.values())].astype(float).apply(np.log).diff()
    w = pd.DataFrame({"y": lg["y"], "d_balance": lg["balance"]})
    w["physical"] = e * w["d_balance"]
    for name, col in SIGNAL_COLUMNS.items():
        w[f"x_{name}"] = lp[col].reindex(w.index)
    return w.dropna()


class _Sig:
    """Minimal stand-in for tmdm.model.signals.Signal (name only is used by fit_signals)."""

    def __init__(self, name: str) -> None:
        self.name = name


SIGNALS: Sequence[Any] = tuple(_Sig(n) for n in SIGNAL_COLUMNS)


def out_of_sample(panel: pd.DataFrame, prices: pd.DataFrame, min_train: int = MIN_TRAIN) -> dict[str, Any]:
    """Expanding window: refit e and the signal coefficients on changes up to year t-1, predict year t."""
    lg = _logs(panel).diff().dropna()
    w_all = windows(panel, prices, 1.0)
    years = list(w_all.index)
    preds = []
    for i, t in enumerate(years):
        if i < min_train:
            continue
        train = w_all.loc[: t - 1]
        tl = lg.loc[: t - 1]
        res = sm.OLS(tl["y"], sm.add_constant(tl[["balance"]])).fit()
        e_t = float(res.params["balance"])
        tw = train.assign(physical=e_t * train["d_balance"])
        x = tw[[f"x_{n}" for n in SIGNAL_COLUMNS]]
        c = sm.OLS(tw["y"] - tw["physical"], x).fit().params
        row = w_all.loc[t]
        phys = e_t * float(row["d_balance"])
        ext = phys + sum(float(c[f"x_{n}"]) * float(row[f"x_{n}"]) for n in SIGNAL_COLUMNS)
        preds.append({"year": int(t), "y": float(row["y"]), "physical": phys, "extended": ext})
    if len(preds) < 5:
        return {"available": False}
    df = pd.DataFrame(preds)
    y = df["y"].to_numpy()

    def r2(m: str) -> float:
        return float(1.0 - np.sum((y - df[m].to_numpy()) ** 2) / np.sum(y**2))

    return {
        "available": True,
        "first_year": int(df["year"].iloc[0]),
        "last_year": int(df["year"].iloc[-1]),
        "n_years": len(df),
        "r2_vs_zero": {"physical": r2("physical"), "extended": r2("extended")},
        "unexplained": {
            m: float(np.mean(np.abs(y - df[m].to_numpy())) / np.mean(np.abs(y))) for m in ("physical", "extended")
        },
        "sign_agreement": {m: float(np.mean(np.sign(df[m]) == np.sign(y))) for m in ("physical", "extended")},
        "predictions": df.to_dict(orient="records"),
    }


def attribute_years(
    panel: pd.DataFrame, prices: pd.DataFrame, e: float, coefs: dict[str, float], y0: int, y1: int
) -> ExtendedAttribution:
    """Attribution of the change between the annual averages of two years."""
    p = panel.set_index("year")
    cols = ["index", "tonnes", "tonne_miles", "fleet", "supply_factor"]
    a = {c: _num(p.at[y0, c]) for c in cols}
    b = {c: _num(p.at[y1, c]) for c in cols}
    phys = attribute(
        index_0=a["index"],
        index_1=b["index"],
        volume_0=a["tonnes"],
        volume_1=b["tonnes"],
        tonne_miles_0=a["tonne_miles"],
        tonne_miles_1=b["tonne_miles"],
        fleet_0=a["fleet"],
        fleet_1=b["fleet"],
        supply_factor_0=a["supply_factor"],
        supply_factor_1=b["supply_factor"],
        elasticity=e,
    )
    sig = {
        n: coefs[n] * float(np.log(_num(prices.at[y1, col]) / _num(prices.at[y0, col])))
        for n, col in SIGNAL_COLUMNS.items()
    }
    return ExtendedAttribution(phys, sig)


def run_long_model(panel: pd.DataFrame, prices: pd.DataFrame) -> dict[str, Any]:
    """Fit the annual model and produce the annual and episode attributions."""
    panel = panel.sort_values("year").reset_index(drop=True)
    est = elasticity(panel)
    e = est.elasticity
    w = windows(panel, prices, e)
    fit = fit_signals(w, SIGNALS, hac_lags=HAC_LAGS)
    coefs = fit.coefficients
    years = [int(y) for y in panel["year"]]
    annual = []
    for y0, y1 in itertools.pairwise(years):
        a = attribute_years(panel, prices, e, coefs, y0, y1)
        annual.append({"year": y1, **a.as_dict()})
    episodes = []
    for ep in LONG_EPISODES:
        if ep.start in years and ep.end in years:
            a = attribute_years(panel, prices, e, coefs, ep.start, ep.end)
            p = panel.set_index("year")
            episodes.append(
                {
                    **ep.__dict__,
                    "index_start": _num(p.at[ep.start, "index"]),
                    "index_end": _num(p.at[ep.end, "index"]),
                    "proxy_start": bool(p.at[ep.start, "proxy"]),
                    "proxy_end": bool(p.at[ep.end, "proxy"]),
                    **a.as_dict(),
                    "explained_share": a.explained_share(),
                    "explained_share_physical": a.physical.explained_share(),
                }
            )
    return {
        "elasticity": est.summary(),
        "robustness": robustness(panel),
        "signals": {
            "definitions": [{"name": n, "column": c, "label": SIGNAL_LABELS[n]} for n, c in SIGNAL_COLUMNS.items()],
            "fit": fit.summary(),
            "out_of_sample": out_of_sample(panel, prices),
        },
        "annual": annual,
        "episodes": episodes,
    }
