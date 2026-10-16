import numpy as np
import pandas as pd
import pytest

from tmdm.model.attribution import Attribution, ExtendedAttribution
from tmdm.model.decomposition import run_model
from tmdm.model.signals import Signal, build_windows, contributions, fit_signals, window_log_change


def _prices(n: int = 200, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.period_range("2008-01", periods=n, freq="M")
    oil = 60 * np.exp(np.cumsum(rng.normal(0, 0.06, n)))
    ore = 90 * np.exp(np.cumsum(rng.normal(0, 0.05, n)))
    return pd.DataFrame({"brent_usd_bbl": oil, "iron_ore_usd_dmtu": ore}, index=idx)


def test_window_log_change_with_lag():
    idx = pd.period_range("2020-01", periods=12, freq="M")
    s = pd.Series(np.arange(1.0, 13.0), index=idx)
    # windows ending in 2020-08 and 2020-04, shifted one month earlier: means of Jun-Aug and Jan-Mar values
    v = window_log_change(s, pd.Period("2020-04", "M"), pd.Period("2020-08", "M"), 3, lag=1)
    assert v == pytest.approx(np.log(np.mean([5, 6, 7]) / np.mean([1, 2, 3])))
    assert np.isnan(window_log_change(s, pd.Period("2020-02", "M"), pd.Period("2020-08", "M"), 3, lag=1))


def test_extended_attribution_adds_up():
    phys = Attribution(
        total=0.5, volume=0.1, distance=0.02, fleet=-0.05, effective_supply=0.0, residual=0.43, elasticity=2.0
    )
    ext = ExtendedAttribution(phys, {"oil": 0.2, "iron_ore": 0.1})
    assert ext.residual == pytest.approx(0.13)
    assert ext.check_sums()
    d = ext.as_dict()
    assert d["residual_physical"] == pytest.approx(0.43)
    assert d["signal_oil"] == pytest.approx(0.2)
    assert ext.explained_share() == pytest.approx(1 - 0.13 / 0.5)


def test_signal_fit_recovers_coefficients_and_attribution_adds_up():
    prices = _prices()
    rng = np.random.default_rng(11)
    idx = prices.index[24:]
    n = len(idx)
    tm = 1e12 * np.exp(np.cumsum(rng.normal(0.003, 0.02, n)))
    fleet = 8e8 * np.exp(np.cumsum(np.full(n, 0.003)))
    sf = np.ones(n)
    log_bal = np.log(tm / (fleet * sf))
    e, c_oil, c_ore = 2.0, 0.5, 0.8
    lo = np.log(prices["brent_usd_bbl"]).reindex(idx).to_numpy()
    lr = np.log(prices["iron_ore_usd_dmtu"]).shift(3).reindex(idx).to_numpy()
    log_bdi = 7.0 + e * (log_bal - log_bal[0]) + c_oil * (lo - lo[0]) + c_ore * (lr - lr[0]) + rng.normal(0, 0.01, n)
    panel = pd.DataFrame(
        {"index": np.exp(log_bdi), "tonnes": tm / 5000, "tonne_miles": tm, "fleet": fleet, "supply_factor": sf},
        index=idx,
    )
    signals = (Signal("oil", "brent_usd_bbl", 0, "Oil"), Signal("iron_ore", "iron_ore_usd_dmtu", 3, "Ore"))
    w = build_windows(panel, prices, e, signals)
    fit = fit_signals(w, signals)
    assert fit.coefficients["oil"] == pytest.approx(c_oil, abs=0.1)
    assert fit.coefficients["iron_ore"] == pytest.approx(c_ore, abs=0.1)
    assert fit.unexplained_extended < fit.unexplained_physical
    c = contributions(prices, idx[30], idx[60], fit.coefficients, signals)
    assert set(c) == {"oil", "iron_ore"}
    out = run_model(panel, prices=prices, signals=signals)
    roll = out.rolling
    s = roll[["volume", "distance", "fleet", "effective_supply", "signal_oil", "signal_iron_ore", "residual"]].sum(
        axis=1
    )
    assert np.allclose(s, roll["total"], atol=1e-9)
    assert np.allclose(roll["residual_physical"] - roll["signal_oil"] - roll["signal_iron_ore"], roll["residual"])
    assert out.signals["out_of_sample"]["available"] is True
    assert out.signals["fit"]["n_obs"] == len(w.dropna())
