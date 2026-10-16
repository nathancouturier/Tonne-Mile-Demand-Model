import math

import pandas as pd
import pytest

from tmdm.transform.tonne_miles import aggregate_tonne_miles, haul_split


def _flows():
    return pd.DataFrame(
        {
            "month": ["2024-01", "2024-01", "2024-02", "2024-02"],
            "route_id": ["AU-CN", "BR-CN", "AU-CN", "BR-CN"],
            "tonnes": [60.0, 20.0, 50.0, 30.0],
        }
    )


def test_aggregate_totals_and_shares():
    distances = pd.DataFrame({"route_id": ["AU-CN", "BR-CN"], "distance_nm": [3600.0, 11000.0]})
    by_route, totals = aggregate_tonne_miles(_flows(), distances)
    jan = totals[totals["month"] == "2024-01"].iloc[0]
    assert jan["tonnes"] == 80.0
    assert jan["tonne_miles"] == 60 * 3600 + 20 * 11000
    assert jan["avg_haul_nm"] == pytest.approx((60 * 3600 + 20 * 11000) / 80)
    shares = by_route[by_route["month"] == "2024-01"]["share"]
    assert shares.sum() == pytest.approx(1.0)


def test_volume_plus_haul_equals_tonne_mile_change():
    distances = pd.DataFrame({"route_id": ["AU-CN", "BR-CN"], "distance_nm": [3600.0, 11000.0]})
    _, totals = aggregate_tonne_miles(_flows(), distances)
    t0, t1 = totals.iloc[0], totals.iloc[1]
    dlog_tm = math.log(t1["tonne_miles"] / t0["tonne_miles"])
    dlog_v = math.log(t1["tonnes"] / t0["tonnes"])
    dlog_h = math.log(t1["avg_haul_nm"] / t0["avg_haul_nm"])
    assert dlog_v + dlog_h == pytest.approx(dlog_tm, abs=1e-12)


def test_missing_distance_raises():
    distances = pd.DataFrame({"route_id": ["AU-CN"], "distance_nm": [3600.0]})
    with pytest.raises(ValueError):
        aggregate_tonne_miles(_flows(), distances)


def test_haul_split_sums_exactly_and_isolates_mix():
    idx = ["AU-CN", "BR-CN"]
    t0 = pd.Series([60.0, 20.0], index=idx)
    t1 = pd.Series([50.0, 30.0], index=idx)
    d = pd.Series([3600.0, 11000.0], index=idx)
    dlog, mix, reroute = haul_split(t0, t1, d, d)
    assert mix + reroute == pytest.approx(dlog, abs=1e-12)
    assert reroute == 0.0
    assert mix > 0  # shift towards the long Brazil route lengthens the haul


def test_haul_split_isolates_rerouting():
    idx = ["UA-CN"]
    t = pd.Series([10.0], index=idx)
    d0 = pd.Series([8000.0], index=idx)
    d1 = pd.Series([11000.0], index=idx)
    dlog, mix, reroute = haul_split(t, t, d0, d1)
    assert mix == 0.0
    assert reroute == pytest.approx(math.log(11000 / 8000))
    assert dlog == pytest.approx(reroute)
