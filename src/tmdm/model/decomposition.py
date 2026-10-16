"""Decomposition driver: elasticity, rolling attribution and episode attribution.

The driver works on a monthly panel with the following positive series:

    index          freight index (monthly average)
    tonnes         cargo tonnes on the covered routes
    tonne_miles    tonnes multiplied by route distance, summed over routes
    fleet          bulk carrier fleet, deadweight tonnes
    supply_factor  effective supply factor (share of fleet time at sea), 1 if absent

Endpoint values for an attribution are trailing averages over ``window``
months so that a single noisy month does not dominate an episode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from tmdm.model.attribution import Attribution, attribute
from tmdm.model.episodes import Episode, covered_episodes
from tmdm.model.regression import ElasticityEstimate, difference_regression, estimate_elasticity

SERIES = ("index", "tonnes", "tonne_miles", "fleet", "supply_factor")


@dataclass
class ModelOutput:
    """Everything the export stage needs from the model."""

    elasticity: ElasticityEstimate
    elasticity_robustness: dict[str, Any]
    difference_regression: dict[str, Any]
    rolling: pd.DataFrame
    episodes: list[dict[str, Any]]
    window: int
    horizon: int
    panel: pd.DataFrame = field(repr=False)


def _check_panel(panel: pd.DataFrame) -> pd.DataFrame:
    missing = set(SERIES) - set(panel.columns)
    if missing:
        raise ValueError(f"panel is missing {sorted(missing)}")
    p = panel.copy()
    if not isinstance(p.index, pd.PeriodIndex):
        p.index = pd.PeriodIndex(p.index, freq="M")
    p = p.sort_index()
    p = p.dropna(subset=list(SERIES))
    if (p[list(SERIES)] <= 0).any().any():
        raise ValueError("all panel series must be strictly positive")
    full = pd.period_range(p.index[0], p.index[-1], freq="M")
    if len(full) != len(p):
        raise ValueError("panel has gaps after dropping missing months")
    return p


def log_balance(panel: pd.DataFrame) -> pd.Series:
    """Log of the demand/supply balance: tonne-miles over effective fleet."""
    return pd.Series(np.log(panel["tonne_miles"] / (panel["fleet"] * panel["supply_factor"])), index=panel.index)


def fit_elasticity(panel: pd.DataFrame) -> tuple[ElasticityEstimate, dict[str, Any]]:
    """Headline elasticity from twelve-month log changes, plus robustness variants.

    Twelve-month changes remove seasonality and the slow trends that dominate
    the levels of both series, so the coefficient measures how a year-on-year
    change in the demand/supply balance has translated into a year-on-year
    change in the index. Levels regressions (with and without month dummies),
    quarterly changes and the two halves of the sample are reported as
    robustness checks.
    """
    p = _check_panel(panel)
    y = np.log(p["index"])
    x = log_balance(p)
    d12 = pd.concat([y.diff(12).rename("y"), x.diff(12).rename("x")], axis=1).dropna()
    if len(d12) < 24:
        raise ValueError("need at least 36 months of panel data for the twelve-month change regression")
    headline = estimate_elasticity(d12["y"], d12["x"])
    robustness: dict[str, Any] = {
        "levels_with_month_dummies": estimate_elasticity(y, x, seasonal=True).summary(),
        "levels_plain": estimate_elasticity(y, x).summary(),
    }
    # quarterly averages, four-quarter changes
    q = p[["index", "tonne_miles", "fleet", "supply_factor"]].copy()
    q.index = pd.PeriodIndex(q.index).asfreq("Q")
    qm = q.groupby(level=0).mean()
    if len(qm) >= 20:
        yq = np.log(qm["index"]).diff(4)
        xq = log_balance(qm).diff(4)
        est = estimate_elasticity(yq, xq, hac_lags=4)
        robustness["quarterly_four_quarter_changes"] = est.summary()
    # halves of the sample (twelve-month changes)
    half = len(d12) // 2
    for label, part in (("first_half", d12.iloc[:half]), ("second_half", d12.iloc[half:])):
        if len(part) >= 24:
            est = estimate_elasticity(part["y"], part["x"])
            robustness[label] = est.summary()
    return headline, robustness


def _window_mean(panel: pd.DataFrame, end: pd.Period, window: int) -> pd.Series:
    start = end - (window - 1)
    sub = panel.loc[start:end, list(SERIES)]
    if len(sub) != window:
        raise ValueError(f"window {start}..{end} not fully covered")
    return sub.mean()


def attribute_period(
    panel: pd.DataFrame, start: pd.Period, end: pd.Period, elasticity: float, window: int
) -> Attribution:
    """Attribute the index change between two months (window-averaged endpoints)."""
    a = _window_mean(panel, start, window)
    b = _window_mean(panel, end, window)
    return attribute(
        index_0=float(a["index"]),
        index_1=float(b["index"]),
        volume_0=float(a["tonnes"]),
        volume_1=float(b["tonnes"]),
        tonne_miles_0=float(a["tonne_miles"]),
        tonne_miles_1=float(b["tonne_miles"]),
        fleet_0=float(a["fleet"]),
        fleet_1=float(b["fleet"]),
        supply_factor_0=float(a["supply_factor"]),
        supply_factor_1=float(b["supply_factor"]),
        elasticity=elasticity,
    )


def rolling_attribution(panel: pd.DataFrame, elasticity: float, horizon: int = 12, window: int = 3) -> pd.DataFrame:
    """Attribute the change over ``horizon`` months for every month in the panel."""
    p = _check_panel(panel)
    rows = []
    for end in p.index:
        start = end - horizon
        if start - (window - 1) < p.index[0]:
            continue
        a = attribute_period(p, start, end, elasticity, window)
        d = a.as_dict()
        d.update({"month": end, "start": start, "end": end})
        rows.append(d)
    out = pd.DataFrame(rows)
    return out


def episode_attributions(panel: pd.DataFrame, elasticity: float, window: int) -> list[dict[str, Any]]:
    """Attribute every episode covered by the panel."""
    p = _check_panel(panel)
    eps: list[Episode] = covered_episodes(p.index[0], p.index[-1], window)
    out = []
    for ep in eps:
        s, e = pd.Period(ep.start, "M"), pd.Period(ep.end, "M")
        a = attribute_period(p, s, e, elasticity, window)
        level_0 = float(_window_mean(p, s, window)["index"])
        level_1 = float(_window_mean(p, e, window)["index"])
        out.append(
            {
                "episode_id": ep.episode_id,
                "name": ep.name,
                "start": ep.start,
                "end": ep.end,
                "description": ep.description,
                "index_start": level_0,
                "index_end": level_1,
                **a.as_dict(),
                "explained_share": a.explained_share(),
            }
        )
    return out


def run_model(panel: pd.DataFrame, window: int = 3, horizon: int = 12) -> ModelOutput:
    """Fit the elasticity and produce rolling and episode attributions."""
    p = _check_panel(panel)
    headline, robustness = fit_elasticity(p)
    e = headline.elasticity
    rolling = rolling_attribution(p, e, horizon=horizon, window=window)
    episodes = episode_attributions(p, e, window)
    logs = pd.DataFrame(
        {
            "index": np.log(p["index"]),
            "volume": np.log(p["tonnes"]),
            "haul": np.log(p["tonne_miles"] / p["tonnes"]),
            "fleet": np.log(p["fleet"]),
            "supply_factor": np.log(p["supply_factor"]),
        },
        index=p.index,
    )
    dlog = logs.diff()
    try:
        diff_reg = difference_regression(dlog, lags=1)
    except (ValueError, np.linalg.LinAlgError) as exc:
        diff_reg = {"error": str(exc)}
    return ModelOutput(headline, robustness, diff_reg, rolling, episodes, window, horizon, p)
