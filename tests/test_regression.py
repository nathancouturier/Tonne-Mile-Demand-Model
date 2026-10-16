import numpy as np
import pandas as pd

from tmdm.model.regression import difference_regression, estimate_elasticity


def _synthetic(n=200, elasticity=5.0, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.period_range("2010-01", periods=n, freq="M")
    volume = pd.Series(np.cumsum(rng.normal(0.002, 0.02, n)), index=idx)
    haul = pd.Series(np.cumsum(rng.normal(0.0005, 0.005, n)), index=idx)
    fleet = pd.Series(np.cumsum(rng.normal(0.003, 0.001, n)), index=idx)
    supply = pd.Series(np.cumsum(rng.normal(0.0, 0.003, n)), index=idx)
    balance = volume + haul - fleet - supply
    noise = pd.Series(rng.normal(0, 0.05, n), index=idx)
    log_index = 7.0 + elasticity * balance + noise
    return log_index, volume, haul, fleet, supply, balance


def test_levels_regression_recovers_elasticity():
    log_index, *_, balance = _synthetic()
    est = estimate_elasticity(log_index, balance)
    assert abs(est.elasticity - 5.0) < 0.5
    assert est.r_squared > 0.9
    assert est.n_obs == 200
    assert est.check_sums() if hasattr(est, "check_sums") else True


def test_difference_regression_signs_and_restriction():
    log_index, volume, haul, fleet, supply, _ = _synthetic(n=400, seed=3)
    dlog = pd.DataFrame(
        {
            "index": log_index.diff(),
            "volume": volume.diff(),
            "haul": haul.diff(),
            "fleet": fleet.diff(),
            "supply_factor": supply.diff(),
        }
    )
    out = difference_regression(dlog, lags=1)
    c = out["coefficients"]
    assert c["volume"] > 0 and c["haul"] > 0 and c["fleet"] < 0 and c["supply_factor"] < 0
    # the data were generated with a common elasticity, so the restriction should not be rejected
    assert out["common_elasticity_wald_pvalue"] > 0.01
    assert 0 < out["r_squared"] <= 1
