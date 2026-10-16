import math

import pandas as pd
import pytest

from tmdm.transform.fleet import annual_to_monthly_loglinear


def test_interpolation_hits_january_values_and_grows_smoothly():
    annual = pd.Series({2020: 1000.0, 2021: 1100.0, 2022: 1210.0})
    m = annual_to_monthly_loglinear(annual, "2022-12")
    jan21 = m[m["month"] == pd.Period("2021-01", "M")].iloc[0]
    assert jan21["value"] == pytest.approx(1100.0)
    assert not jan21["extrapolated"]
    # constant monthly log growth within the year
    vals = m[(m["month"] >= pd.Period("2020-01", "M")) & (m["month"] <= pd.Period("2021-01", "M"))]["value"].to_numpy()
    ratios = vals[1:] / vals[:-1]
    assert ratios.std() < 1e-9
    assert ratios[0] == pytest.approx(math.exp(math.log(1.1) / 12))


def test_extrapolation_is_flagged_and_uses_last_growth():
    annual = pd.Series({2020: 1000.0, 2021: 1100.0})
    m = annual_to_monthly_loglinear(annual, "2022-01")
    jan22 = m[m["month"] == pd.Period("2022-01", "M")].iloc[0]
    assert jan22["extrapolated"]
    assert jan22["value"] == pytest.approx(1210.0)
    assert m["extrapolated"].sum() == 12


def test_requires_two_points():
    with pytest.raises(ValueError):
        annual_to_monthly_loglinear(pd.Series({2020: 1.0}), "2020-12")
