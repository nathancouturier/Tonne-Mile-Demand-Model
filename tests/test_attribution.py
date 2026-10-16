import math

import pytest

from tmdm.model.attribution import attribute, log_points_to_pct


def test_components_sum_to_total_exactly():
    a = attribute(
        index_0=1000,
        index_1=2500,
        volume_0=100.0,
        volume_1=108.0,
        tonne_miles_0=500.0,
        tonne_miles_1=560.0,
        fleet_0=900.0,
        fleet_1=930.0,
        supply_factor_0=0.80,
        supply_factor_1=0.78,
        elasticity=6.0,
    )
    assert a.check_sums(1e-12)
    assert math.isclose(a.total, math.log(2.5))
    assert a.volume == pytest.approx(6.0 * math.log(1.08))
    # haul = tm / volume: 5.0 -> 5.185..., distance effect is e * dlog(haul)
    assert a.distance == pytest.approx(6.0 * math.log((560 / 108) / (500 / 100)))
    assert a.fleet == pytest.approx(-6.0 * math.log(930 / 900))
    assert a.effective_supply == pytest.approx(-6.0 * math.log(0.78 / 0.80))


def test_volume_and_distance_add_to_tonne_mile_effect():
    a = attribute(
        index_0=1.0,
        index_1=1.0,
        volume_0=100.0,
        volume_1=90.0,
        tonne_miles_0=1000.0,
        tonne_miles_1=1200.0,
        fleet_0=1.0,
        fleet_1=1.0,
        supply_factor_0=1.0,
        supply_factor_1=1.0,
        elasticity=1.0,
    )
    assert a.volume + a.distance == pytest.approx(math.log(1.2))
    assert a.residual == pytest.approx(-math.log(1.2))


def test_zero_change_gives_zero_components():
    a = attribute(
        index_0=5.0,
        index_1=5.0,
        volume_0=2.0,
        volume_1=2.0,
        tonne_miles_0=7.0,
        tonne_miles_1=7.0,
        fleet_0=3.0,
        fleet_1=3.0,
        supply_factor_0=0.9,
        supply_factor_1=0.9,
        elasticity=4.0,
    )
    assert a.total == 0 and a.volume == 0 and a.distance == 0 and a.fleet == 0 and a.residual == 0


def test_rejects_non_positive_inputs():
    with pytest.raises(ValueError):
        attribute(
            index_0=0.0,
            index_1=1.0,
            volume_0=1.0,
            volume_1=1.0,
            tonne_miles_0=1.0,
            tonne_miles_1=1.0,
            fleet_0=1.0,
            fleet_1=1.0,
            supply_factor_0=1.0,
            supply_factor_1=1.0,
            elasticity=1.0,
        )


def test_log_points_to_pct():
    assert log_points_to_pct(math.log(2.0)) == pytest.approx(100.0)
