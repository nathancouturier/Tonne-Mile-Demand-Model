import numpy as np
import pandas as pd
import pytest

from tmdm.model.decomposition import attribute_period, episode_attributions, rolling_attribution, run_model
from tmdm.model.episodes import covered_episodes


def _panel(n=180, seed=7):
    rng = np.random.default_rng(seed)
    idx = pd.period_range("2010-01", periods=n, freq="M")
    tonnes = 100 * np.exp(np.cumsum(rng.normal(0.003, 0.02, n)))
    haul = 5000 * np.exp(np.cumsum(rng.normal(0.0005, 0.004, n)))
    fleet = 500 * np.exp(np.cumsum(rng.normal(0.003, 0.0005, n)))
    supply = np.exp(np.cumsum(rng.normal(0.0, 0.002, n)))
    tm = tonnes * haul
    index = 1500 * np.exp(
        4.0 * (np.log(tm / (fleet * supply)) - np.log(tm[0] / (fleet[0] * supply[0]))) + rng.normal(0, 0.1, n)
    )
    return pd.DataFrame(
        {"index": index, "tonnes": tonnes, "tonne_miles": tm, "fleet": fleet, "supply_factor": supply},
        index=idx,
    )


def test_rolling_attribution_sums_every_month():
    p = _panel()
    roll = rolling_attribution(p, elasticity=4.0, horizon=12, window=3)
    assert len(roll) == len(p) - 12 - 2
    s = roll[["volume", "distance", "fleet", "effective_supply", "residual"]].sum(axis=1)
    assert np.allclose(s, roll["total"], atol=1e-10)


def test_episode_attribution_matches_direct_call():
    p = _panel()
    eps = episode_attributions(p, elasticity=4.0, window=3)
    ids = {e["episode_id"] for e in eps}
    # the panel covers 2010-01 to 2024-12, so the 2008 episode must be excluded
    assert "spike_2008" not in ids
    assert "slide_2014_2016" in ids and "recent" in ids
    e = next(x for x in eps if x["episode_id"] == "slide_2014_2016")
    a = attribute_period(p, pd.Period("2013-12", "M"), pd.Period("2016-02", "M"), 4.0, 3)
    assert e["total"] == pytest.approx(a.total)
    assert e["volume"] + e["distance"] + e["fleet"] + e["effective_supply"] + e["residual"] == pytest.approx(e["total"])


def test_covered_episodes_respects_window():
    eps = covered_episodes(pd.Period("2010-01", "M"), pd.Period("2024-12", "M"), window=3)
    assert all(pd.Period(e.start, "M") - 2 >= pd.Period("2010-01", "M") for e in eps)
    assert any(e.episode_id == "recent" and e.end == "2024-12" and e.start == "2023-12" for e in eps)


def test_run_model_recovers_elasticity_and_outputs():
    p = _panel(n=240)
    out = run_model(p)
    assert abs(out.elasticity.elasticity - 4.0) < 1.0
    assert not out.rolling.empty
    assert out.episodes
    assert "r_squared" in out.difference_regression
    assert "quarterly_four_quarter_changes" in out.elasticity_robustness
    assert "levels_with_month_dummies" in out.elasticity_robustness
