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
    published = {"AUS": set(pd.period_range("2024-01", "2024-03", freq="M"))}
    out = complete_months(flows, published)
    # two routes x three months
    assert len(out) == 6
    feb = out[(out["route_id"] == "AUS-CHN-coal") & (out["month"] == pd.Period("2024-02", "M"))]
    assert feb["tonnes"].iloc[0] == 0.0
    assert not out["interpolated"].any()
    cov = group_coverage(out)
    assert cov.loc[0, "n_months"] == 3


def test_complete_months_interpolates_unpublished_months():
    flows = pd.DataFrame(
        {
            "month": pd.PeriodIndex(["2024-01", "2024-03"], freq="M"),
            "route_id": ["UKR-EGY-grains", "UKR-EGY-grains"],
            "origin": ["UKR", "UKR"],
            "destination": ["EGY", "EGY"],
            "commodity": ["grains", "grains"],
            "tonnes": [10.0, 30.0],
            "source": ["s", "s"],
            "group_id": ["UKR_X_grains", "UKR_X_grains"],
        }
    )
    published = {"UKR": {pd.Period("2024-01", "M"), pd.Period("2024-03", "M")}}
    out = complete_months(flows, published).set_index("month")
    assert out.loc[pd.Period("2024-02", "M"), "tonnes"] == pytest.approx(20.0)
    assert bool(out.loc[pd.Period("2024-02", "M"), "interpolated"]) is True
    assert out["interpolated"].sum() == 1


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


def _records(rows):
    cols = ["freqCode", "period", "reporterCode", "flowCode", "partnerCode", "cmdCode", "netWgt", "primaryValue"]
    return pd.DataFrame(rows, columns=cols)


def test_annual_unit_values_reject_implausible_weights():
    from tmdm.transform.flows import annual_unit_values

    recs = _records(
        [
            # 100 USD per tonne: 1,000,000 USD for 10,000 tonnes (10,000,000 kg)
            ("A", "2014", 842, "X", 76, "2701", 1.0e7, 1.0e6),
            # weight apparently in tonnes labelled as kilograms: 100,000 USD per "tonne"
            ("A", "2004", 842, "X", 76, "2701", 1.0e4, 1.0e6),
            # world aggregate rows are ignored
            ("A", "2014", 842, "X", 0, "2701", 1.0e8, 1.0e7),
        ]
    )
    uv = annual_unit_values(recs)
    assert len(uv) == 1
    assert uv.iloc[0]["year"] == 2014
    assert uv.iloc[0]["unit_value"] == pytest.approx(100.0)


def test_comtrade_flows_use_annual_unit_value_proxy_for_weightless_months():
    from tmdm.reference import ROUTE_GROUPS
    from tmdm.transform.flows import _comtrade_group_flows, annual_unit_values, missing_quantity_months

    g = next(r for r in ROUTE_GROUPS if r.group_id == "USA_X_coal")
    recs = _records(
        [
            ("A", "2014", 842, "X", 76, "2701", 2.0e10, 2.0e9),  # 100 USD per tonne
            ("M", "201401", 842, "X", 76, "2701", 0.0, 5.0e7),  # value only: proxy 500,000 t
            ("M", "201402", 842, "X", 76, "2701", 3.0e8, 3.0e7),  # weight reported: 300,000 t
            ("M", "201403", 842, "X", 528, "2701", None, 4.0e7),  # no unit value for this partner: missing
        ]
    )
    uv = annual_unit_values(recs)
    out = _comtrade_group_flows(recs, g, uv).set_index("month")
    jan, feb, mar = (pd.Period(m, "M") for m in ("2014-01", "2014-02", "2014-03"))
    assert out.loc[jan, "tonnes"] == pytest.approx(500_000.0)
    assert out.loc[jan, "proxy_tonnes"] == pytest.approx(500_000.0)
    assert out.loc[feb, "tonnes"] == pytest.approx(300_000.0)
    assert out.loc[feb, "proxy_tonnes"] == 0.0
    assert out.loc[mar, "tonnes"] == 0.0
    assert out.loc[mar, "value_missing_usd"] == pytest.approx(4.0e7)
    missing = missing_quantity_months(out.reset_index())
    assert missing == {"USA_X_coal": {mar}}


def test_complete_months_trims_leading_gaps_and_interpolates_interior_ones():
    flows = pd.DataFrame(
        {
            "month": pd.PeriodIndex(["2013-01", "2013-02", "2013-04", "2013-05"], freq="M"),
            "route_id": ["USA-NLD-coal"] * 4,
            "origin": ["USA"] * 4,
            "destination": ["NLD"] * 4,
            "commodity": ["coal"] * 4,
            "tonnes": [0.0, 10.0, 0.0, 30.0],
            "source": ["s"] * 4,
            "group_id": ["USA_X_coal"] * 4,
            "proxy_tonnes": [0.0, 0.0, 0.0, 0.0],
            "value_usd": [1.0, 1.0, 1.0, 1.0],
            "value_missing_usd": [1.0, 0.0, 1.0, 0.0],
        }
    )
    published = {"USA": set(pd.period_range("2013-01", "2013-05", freq="M"))}
    missing = {"USA_X_coal": {pd.Period("2013-01", "M"), pd.Period("2013-04", "M")}}
    out = complete_months(flows, published, missing).set_index("month")
    # the leading weightless month is trimmed, the interior one interpolated and flagged
    assert out.index.min() == pd.Period("2013-02", "M")
    assert out.loc[pd.Period("2013-03", "M"), "tonnes"] == 0.0
    assert out.loc[pd.Period("2013-04", "M"), "tonnes"] == pytest.approx(15.0)
    assert out.loc[pd.Period("2013-04", "M"), "fill"] == "interpolated_no_weight"
    assert bool(out.loc[pd.Period("2013-04", "M"), "interpolated"]) is True
    assert out.loc[pd.Period("2013-02", "M"), "fill"] == "reported"


def test_canal_use_is_judged_against_the_baseline_variant_and_red_sea_ports():
    distances = pd.DataFrame(
        [
            # Capesize route whose unrestricted shortest path uses Panama but whose baseline (avoiding
            # Panama) goes through Suez: the Suez alternative is the variant avoiding both canals
            ("CAN-CHN-iron_ore", "shortest", 11631.0, False, False),
            ("CAN-CHN-iron_ore", "avoid_suez", 11631.0, False, False),
            ("CAN-CHN-iron_ore", "avoid_panama", 12335.0, True, False),
            ("CAN-CHN-iron_ore", "avoid_suez_panama", 14820.0, False, False),
            # grain route into the Red Sea: Suez is used but no diversion is possible
            ("UKR-SAU-grains", "shortest", 1865.0, True, True),
            ("UKR-SAU-grains", "avoid_suez", 12058.0, False, True),
            ("UKR-SAU-grains", "avoid_panama", 1865.0, False, True),
            ("UKR-SAU-grains", "avoid_suez_panama", 12058.0, False, True),
        ],
        columns=["route_id", "variant", "distance_nm", "is_baseline", "red_sea_port"],
    )
    c = canal_users(distances).set_index("route_id")
    assert bool(c.loc["CAN-CHN-iron_ore", "uses_suez"]) is True
    assert c.loc["CAN-CHN-iron_ore", "suez_alt_nm"] == pytest.approx(14820.0)
    assert c.loc["CAN-CHN-iron_ore", "baseline_nm"] == pytest.approx(12335.0)
    assert bool(c.loc["CAN-CHN-iron_ore", "uses_panama"]) is False
    assert bool(c.loc["UKR-SAU-grains", "uses_suez"]) is False
    assert bool(c.loc["UKR-SAU-grains", "red_sea_port"]) is True


def test_diversion_shares_are_zero_before_the_disruption():
    months = pd.period_range("2023-06", "2024-02", freq="M")
    transits = [20.0, 20.0, 20.0, 20.0, 20.0, 10.0, 5.0, 4.0, 4.0]  # falls from November 2023
    choke = pd.DataFrame(
        {"month": list(months), "portname": ["Bab el-Mandeb Strait"] * len(months), "n_dry_bulk_per_day": transits}
    )
    out = diversion_shares(choke)
    red = out[out["chokepoint"] == "red_sea"].set_index("month")["diversion_share"]
    assert red.loc[pd.Period("2023-10", "M")] == 0.0
    assert red.loc[pd.Period("2023-11", "M")] == pytest.approx(0.5)
    assert red.loc[pd.Period("2024-02", "M")] == pytest.approx(0.8)
    assert out[out["chokepoint"] == "panama"].empty
