import pandas as pd
import pytest

from tmdm.transform import canal_users, monthly_route_distances
from tmdm.transform.flows import complete_months, group_coverage
from tmdm.transform.supply import diversion_shares


def test_complete_months_fills_true_zeros_inside_group_range():
    flows = pd.DataFrame(
        {
            "month": pd.PeriodIndex(["2024-01", "2024-03", "2024-01"], freq="M"),
            "route_id": ["AUS-CHN-coal", "AUS-CHN-coal", "AUS-JPN-coal"],
            "origin": ["AUS", "AUS", "AUS"],
            "destination": ["CHN", "CHN", "JPN"],
            "commodity": ["coal", "coal", "coal"],
            "tonnes": [10.0, 12.0, 5.0],
            "source": ["s", "s", "s"],
            "group_id": ["AUS_X_coal", "AUS_X_coal", "AUS_X_coal"],
        }
    )
    out = complete_months(flows)
    # two routes x three months
    assert len(out) == 6
    feb = out[(out["route_id"] == "AUS-CHN-coal") & (out["month"] == pd.Period("2024-02", "M"))]
    assert feb["tonnes"].iloc[0] == 0.0
    cov = group_coverage(out)
    assert cov.loc[0, "n_months"] == 3


def test_canal_detection_and_monthly_distances():
    distances = pd.DataFrame(
        [
            # a grain route that uses Suez: avoiding Suez is much longer
            ("UKR-CHN-grains", "shortest", 8000.0, True),
            ("UKR-CHN-grains", "avoid_suez", 11000.0, False),
            ("UKR-CHN-grains", "avoid_panama", 8000.0, False),
            ("UKR-CHN-grains", "avoid_suez_panama", 11000.0, False),
            # an iron ore route via the Cape: no canal
            ("BRA-CHN-iron_ore", "shortest", 11000.0, False),
            ("BRA-CHN-iron_ore", "avoid_suez", 11000.0, False),
            ("BRA-CHN-iron_ore", "avoid_panama", 11000.0, True),
            ("BRA-CHN-iron_ore", "avoid_suez_panama", 11000.0, False),
        ],
        columns=["route_id", "variant", "distance_nm", "is_baseline"],
    )
    canal = canal_users(distances)
    c = canal.set_index("route_id")
    assert bool(c.loc["UKR-CHN-grains", "uses_suez"]) is True
    assert bool(c.loc["BRA-CHN-iron_ore", "uses_suez"]) is False
    assert bool(c.loc["BRA-CHN-iron_ore", "uses_panama"]) is False
    months = pd.period_range("2024-01", "2024-02", freq="M")
    div = pd.DataFrame(
        {
            "month": list(months) * 2,
            "chokepoint": ["red_sea", "red_sea", "panama", "panama"],
            "diversion_share": [0.0, 0.5, 0.0, 0.0],
        }
    )
    out = monthly_route_distances(canal, div, months).set_index(["route_id", "month"])
    assert out.loc[("UKR-CHN-grains", months[0]), "distance_nm"] == pytest.approx(8000.0)
    assert out.loc[("UKR-CHN-grains", months[1]), "distance_nm"] == pytest.approx(9500.0)
    assert out.loc[("BRA-CHN-iron_ore", months[1]), "distance_nm"] == pytest.approx(11000.0)


def test_diversion_shares_bounded_and_relative_to_baseline():
    months = pd.period_range("2021-01", "2024-03", freq="M")
    rows = []
    for m in months:
        n = 30.0 if m < pd.Period("2023-12", "M") else 12.0
        rows.append(
            {
                "month": m,
                "portname": "Bab el-Mandeb Strait",
                "n_dry_bulk": n * 30,
                "capacity_dry_bulk": 0,
                "days": 30,
                "n_dry_bulk_per_day": n,
            }
        )
        rows.append(
            {
                "month": m,
                "portname": "Panama Canal",
                "n_dry_bulk": 20 * 30,
                "capacity_dry_bulk": 0,
                "days": 30,
                "n_dry_bulk_per_day": 20.0 if m < pd.Period("2023-08", "M") else 25.0,
            }
        )
    ck = pd.DataFrame(rows)
    d = diversion_shares(ck)
    red = d[d["chokepoint"] == "red_sea"].set_index("month")["diversion_share"]
    assert red[pd.Period("2023-06", "M")] == pytest.approx(0.0)
    assert red[pd.Period("2024-02", "M")] == pytest.approx(0.6)
    pan = d[d["chokepoint"] == "panama"].set_index("month")["diversion_share"]
    # more transits than baseline is clipped to zero diversion
    assert pan[pd.Period("2024-01", "M")] == 0.0
    assert d["diversion_share"].between(0, 1).all()
