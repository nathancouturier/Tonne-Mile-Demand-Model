import numpy as np
import pandas as pd
import pytest

from tmdm.model.long_history import out_of_sample, run_long_model
from tmdm.transform.long_history import (
    annual_flows,
    chain_link,
    classify_partner,
    fleet_annual,
    freight_annual,
    prices_annual,
)


def _sea(rows):
    return pd.DataFrame(rows, columns=["unit", "cls", "year", "route_id", "tonnes", "tonne_miles"])


def test_chain_link_ignores_reporter_entry_and_classification_change():
    # unit A grows 10% a year throughout; unit B (ten times larger) enters in 2001 and is flat.
    # A switches from S2 to HS in 2001, with an HS level 5% above S2 (a definitional break).
    rows = []
    for y, a in zip(range(1998, 2004), [100, 110, 121, 133.1, 146.41, 161.051], strict=True):
        if y <= 2001:
            rows.append(("A_X", "S2", y, "A-X-coal", a, a * 1000))
        if y >= 2001:
            rows.append(("A_X", "HS", y, "A-X-coal", a * 1.05, a * 1.05 * 1000))
        if y >= 2001:
            rows.append(("B_X", "HS", y, "B-X-coal", 1000, 1000 * 5000))
    sea = _sea(rows)
    avail = set(zip(sea["unit"], sea["cls"], sea["year"], strict=True))
    out = chain_link(sea, avail, 1998, 2003).set_index("year")
    g = out["tonnes"] / out["tonnes"].shift(1)
    # 1998 to 2001: only A (S2), +10% a year; the HS break in 2001 is not a jump
    assert g.loc[1999] == pytest.approx(1.10)
    assert g.loc[2001] == pytest.approx(1.10)
    # 2002 on: A and B both in HS: (1.05*146.41 + 1000) / (1.05*133.1 + 1000)
    assert g.loc[2002] == pytest.approx((1.05 * 146.41 + 1000) / (1.05 * 133.1 + 1000))
    # level of the last year equals the covered HS total
    assert out.loc[2003, "tonnes"] == pytest.approx(1.05 * 161.051 + 1000)
    assert "B_X" not in out.loc[2001, "units"] and "B_X:HS" in out.loc[2002, "units"]
    assert out.loc[2001, "units"] == "A_X:S2"


def test_chain_link_counts_a_missing_route_as_zero_but_needs_the_unit_in_both_years():
    sea = _sea(
        [("A_X", "HS", 2000, "r1", 50, 50), ("A_X", "HS", 2000, "r2", 50, 50), ("A_X", "HS", 2001, "r1", 60, 60)]
    )
    avail = {("A_X", "HS", 2000), ("A_X", "HS", 2001)}
    out = chain_link(sea, avail, 2000, 2001).set_index("year")
    assert out.loc[2001, "tonnes"] / out.loc[2000, "tonnes"] == pytest.approx(0.6)
    with pytest.raises(ValueError):
        chain_link(sea, {("A_X", "HS", 2000)}, 2000, 2001)


def test_freight_proxy_recovers_the_calibration():
    months = pd.period_range("1990-01", "2020-12", freq="M")
    rng = np.random.default_rng(0)
    kil = pd.Series(rng.normal(0, 40, len(months)), index=months)
    cpi = pd.Series(np.exp(np.linspace(np.log(130), np.log(260), len(months))), index=months)
    tau = np.array([(p.year - 2000) * 12 + p.month - 1 for p in months], dtype=float)
    true = np.exp(1.5 - 0.002 * tau + kil.to_numpy() / 100 + np.log(cpi.to_numpy()))
    bdi = pd.Series(true, index=months)[months >= pd.Period("2011-01", "M")]
    out, cal = freight_annual(bdi, kil, cpi)
    assert cal["intercept"] == pytest.approx(1.5, abs=1e-9)
    assert cal["trend_per_month"] == pytest.approx(-0.002, abs=1e-12)
    o = out.set_index("year")
    assert bool(o.loc[2005, "proxy"]) and not bool(o.loc[2011, "proxy"])
    expected_2005 = pd.Series(true, index=months)[months.year == 2005].mean()
    assert o.loc[2005, "index"] == pytest.approx(expected_2005, rel=1e-9)
    assert o.loc[2015, "index"] == pytest.approx(bdi[bdi.index.year == 2015].mean())
    assert cal["annual_change_corr"] == pytest.approx(1.0)


def test_annual_flows_estimates_weightless_records_from_unit_values():
    rec = pd.DataFrame(
        {
            "classification": ["HS"] * 4,
            "period": ["2000", "2000", "2000", "2001"],
            "reporterCode": [36, 36, 36, 36],
            "flowCode": ["X"] * 4,
            "partnerCode": [156, 392, 0, 156],
            "cmdCode": ["2601"] * 4,
            # Japan's weight is a thousand times too small (unit value 25,000 $/t); 2001 has no weight at all
            "netWgt": [10e9, 10e6, 20e9, None],
            "qty": [None, None, None, None],
            "qtyUnitCode": [8, 8, 8, 8],
            "primaryValue": [250e6, 250e6, 500e6, 300e6],
        }
    )
    partners = {156: "CHN", 392: "JPN", 0: None}
    f = annual_flows(rec, partners)
    jp = f[(f["destination"] == "JPN")].iloc[0]
    assert jp["proxy_tonnes"] == pytest.approx(10e6)  # 250 M$ at the 25 $/t unit value of China's record
    cn01 = f[(f["destination"] == "CHN") & (f["year"] == 2001)].iloc[0]
    assert cn01["tonnes"] == pytest.approx(12e6)  # 2001 unit value carried from 2000
    assert set(f["unit"]) == {"AUS_X"} and f["is_world"].sum() == 1


def test_classify_partner():
    ports = {"CHN", "JPN", "MEX"}
    assert classify_partner("USA", "MEX", ports) == "land"
    assert classify_partner("USA", "CHN", ports) == "sea"
    assert classify_partner("USA", "USA", ports) == "own"
    assert classify_partner("USA", "AUT", ports) == "no_port"
    assert classify_partner("USA", None, ports) == "unspecified"


def test_fleet_and_prices_annual():
    f = fleet_annual(pd.DataFrame({"year": [2000, 2001, 2002], "fleet_dwt_thousand": [100.0, 110.0, 130.0]}))
    assert f.loc[2000] == pytest.approx(105_000.0) and f.loc[2001] == pytest.approx(120_000.0) and 2002 not in f.index
    months = pd.period_range("1999-01", "2001-12", freq="M")
    p = pd.DataFrame(
        {
            "brent_usd_bbl": np.arange(len(months), dtype=float) + 10,
            "iron_ore_usd_dmtu": np.arange(len(months), dtype=float) + 1,
        },
        index=months,
    )
    a = prices_annual(p)
    assert a.loc[2000, "brent_usd_bbl"] == pytest.approx(np.mean(np.arange(12, 24) + 10))
    # iron ore: October of the previous year to September
    assert a.loc[2000, "iron_ore_usd_dmtu_lead3"] == pytest.approx(np.mean(np.arange(9, 21) + 1))


def _synthetic_long(n=45, seed=2):
    rng = np.random.default_rng(seed)
    years = np.arange(1980, 1980 + n)
    tm = 1e12 * np.exp(np.cumsum(rng.normal(0.04, 0.05, n)))
    tonnes = tm / 5000
    fleet = 2e8 * np.exp(np.cumsum(rng.normal(0.03, 0.03, n)))
    oil = 30 * np.exp(np.cumsum(rng.normal(0, 0.2, n)))
    ore = 25 * np.exp(np.cumsum(rng.normal(0, 0.15, n)))
    bal = np.log(tm / fleet)
    idx = np.exp(
        7 + 2.0 * (bal - bal[0]) + 0.4 * np.log(oil / oil[0]) + 0.7 * np.log(ore / ore[0]) + rng.normal(0, 0.05, n)
    )
    panel = pd.DataFrame(
        {
            "year": years,
            "index": idx,
            "tonnes": tonnes,
            "tonne_miles": tm,
            "fleet": fleet,
            "supply_factor": 1.0,
            "proxy": years < 2011,
        }
    )
    prices = pd.DataFrame({"brent_usd_bbl": oil, "iron_ore_usd_dmtu_lead3": ore}, index=pd.Index(years, name="year"))
    return panel, prices


def test_long_model_recovers_and_adds_up():
    panel, prices = _synthetic_long()
    out = run_long_model(panel, prices)
    assert out["elasticity"]["elasticity"] == pytest.approx(2.0, abs=0.3)
    # the signals are fitted on the residual of the physical identity, so they absorb part of the noise in e
    assert out["signals"]["fit"]["coefficients"]["oil"] == pytest.approx(0.4, abs=0.25)
    assert out["signals"]["fit"]["coefficients"]["iron_ore"] == pytest.approx(0.7, abs=0.25)
    assert out["signals"]["fit"]["unexplained_extended"] < out["signals"]["fit"]["unexplained_physical"]
    for a in out["annual"]:
        s = (
            a["volume"]
            + a["distance"]
            + a["fleet"]
            + a["effective_supply"]
            + a["signal_oil"]
            + a["signal_iron_ore"]
            + a["residual"]
        )
        assert s == pytest.approx(a["total"], abs=1e-12)
    ids = [e["episode_id"] for e in out["episodes"]]
    assert "crash_2008" in ids and "after_2021" not in ids  # 2025 is outside the synthetic window
    assert {"proxy_years", "bdi_years", "first_half", "second_half"} <= set(out["robustness"])


def test_long_out_of_sample_uses_only_past_years():
    panel, prices = _synthetic_long()
    o1 = out_of_sample(panel, prices)
    # changing the last year must not alter earlier predictions
    p2 = panel.copy()
    p2.loc[p2.index[-1], "index"] *= 3
    o2 = out_of_sample(p2, prices)
    assert o1["available"] and o1["n_years"] == len(panel) - 1 - 15
    assert [p["extended"] for p in o1["predictions"][:-1]] == pytest.approx(
        [p["extended"] for p in o2["predictions"][:-1]]
    )
