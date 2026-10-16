"""Regression cross-checks for the decomposition.

Two complementary estimates are produced.

1. A levels regression of the log freight index on the log demand/supply
   balance (tonne-miles over effective fleet), which gives the single
   elasticity ``e`` used by the attribution identity, with Newey-West
   standard errors.
2. An unrestricted first-difference regression of the change in the log index
   on the separate changes in volume, haul, fleet and effective supply,
   including lags, which checks whether the data support treating the
   components with one common elasticity and reports fit and residual
   diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.stats.stattools import durbin_watson


@dataclass
class ElasticityEstimate:
    """Result of the levels regression log(index) on log(balance)."""

    elasticity: float
    std_error: float
    t_stat: float
    intercept: float
    r_squared: float
    n_obs: int
    durbin_watson: float
    residual_std: float
    fitted: pd.Series = field(repr=False)
    residuals: pd.Series = field(repr=False)

    def summary(self) -> dict[str, Any]:
        """JSON-friendly summary without the series."""
        return {
            "elasticity": self.elasticity,
            "std_error": self.std_error,
            "t_stat": self.t_stat,
            "intercept": self.intercept,
            "r_squared": self.r_squared,
            "n_obs": self.n_obs,
            "durbin_watson": self.durbin_watson,
            "residual_std": self.residual_std,
        }


def estimate_elasticity(
    log_index: pd.Series, log_balance: pd.Series, hac_lags: int = 12, seasonal: bool = False
) -> ElasticityEstimate:
    """OLS of log index on log balance with HAC (Newey-West) standard errors.

    With ``seasonal=True`` eleven month-of-year dummies are added so that the
    elasticity is estimated from within-season variation (freight rates and
    cargo volumes both have pronounced seasonal patterns). The series must
    then carry a monthly PeriodIndex.
    """
    df = pd.concat([log_index.rename("y"), log_balance.rename("x")], axis=1).dropna()
    if len(df) < 24:
        raise ValueError("need at least 24 overlapping observations")
    x = sm.add_constant(df[["x"]])
    if seasonal:
        months = pd.PeriodIndex(df.index, freq="M").month
        for m in range(2, 13):
            x[f"m{m}"] = (months == m).astype(float)
    res = sm.OLS(df["y"], x).fit(cov_type="HAC", cov_kwds={"maxlags": hac_lags})
    return ElasticityEstimate(
        elasticity=float(res.params["x"]),
        std_error=float(res.bse["x"]),
        t_stat=float(res.tvalues["x"]),
        intercept=float(res.params["const"]),
        r_squared=float(res.rsquared),
        n_obs=int(res.nobs),
        durbin_watson=float(durbin_watson(res.resid)),
        residual_std=float(np.std(res.resid, ddof=2)),
        fitted=pd.Series(res.fittedvalues, index=df.index),
        residuals=pd.Series(res.resid, index=df.index),
    )


def difference_regression(
    dlog: pd.DataFrame,
    target: str = "index",
    regressors: tuple[str, ...] = ("volume", "haul", "fleet", "supply_factor"),
    lags: int = 1,
    hac_lags: int = 6,
) -> dict[str, Any]:
    """Unrestricted regression of d log index on component changes and their lags.

    Args:
        dlog: Frame of log differences with the target and regressor columns.
        target: Name of the dependent column.
        regressors: Component columns.
        lags: Number of lags of each regressor to include (contemporaneous always included).
        hac_lags: Newey-West bandwidth.

    Returns:
        Dictionary with coefficients, standard errors, p-values, fit statistics
        and a joint test that the four contemporaneous coefficients are equal in
        absolute value with the sign pattern (+, +, -, -) implied by the
        single-elasticity identity.
    """
    cols: dict[str, pd.Series] = {}
    for r in regressors:
        cols[r] = dlog[r]
        for k in range(1, lags + 1):
            cols[f"{r}_lag{k}"] = dlog[r].shift(k)
    x = pd.DataFrame(cols)
    df = pd.concat([dlog[target].rename("y"), x], axis=1).dropna()
    if len(df) < 24:
        raise ValueError("need at least 24 observations")
    xx = sm.add_constant(df.drop(columns="y"))
    res = sm.OLS(df["y"], xx).fit(cov_type="HAC", cov_kwds={"maxlags": hac_lags})
    # Restriction: b_volume = b_haul = -b_fleet = -b_supply (common elasticity)
    names = list(xx.columns)
    rows = []
    sign = {"volume": 1, "haul": 1, "fleet": -1, "supply_factor": -1}
    base = regressors[0]
    for r in regressors[1:]:
        row = np.zeros(len(names))
        row[names.index(base)] = sign[base]
        row[names.index(r)] = -sign[r]
        rows.append(row)
    wald = res.wald_test(np.array(rows), scalar=True) if rows else None
    out: dict[str, Any] = {
        "n_obs": int(res.nobs),
        "r_squared": float(res.rsquared),
        "adj_r_squared": float(res.rsquared_adj),
        "durbin_watson": float(durbin_watson(res.resid)),
        "coefficients": {k: float(v) for k, v in res.params.items()},
        "std_errors": {k: float(v) for k, v in res.bse.items()},
        "p_values": {k: float(v) for k, v in res.pvalues.items()},
        "common_elasticity_wald_stat": float(wald.statistic) if wald is not None else None,
        "common_elasticity_wald_pvalue": float(wald.pvalue) if wald is not None else None,
        "residuals": pd.Series(res.resid, index=df.index),
    }
    return out
