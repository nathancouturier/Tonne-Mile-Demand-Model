import numpy as np
import pandas as pd
import pytest

from tmdm.model.long_history import out_of_sample, run_long_model
from tmdm.transform.long_history import (
    allocate_undisclosed,
    allocation_dominated,
    annual_flows,
    chain_link,
    classify_partner,
    fleet_annual,
    freight_annual,
    measured_series,
    monthly_mismatch,
    prices_annual,
)


def _sea(rows):
    """Rows (unit, cls, year, route_id, tonnes, tonne_miles); one commodity code per unit."""
    df = pd.DataFrame(rows, columns=["unit", "cls", "year", "route_id", "tonnes", "tonne_miles"])
    df["cmd"] = "2701"
    df["commodity"] = "coal"
    df["measured_tonnes"] = df["tonnes"]
    return df


def _all_measured(sea):
    return set(zip(sea["unit"], sea["cls"], sea["commodity"], sea["year"], strict=True))


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
    out = chain_link(sea, _all_measured(sea), 1998, 2003).set_index("year")
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


def test_chain_link_counts_a_missing_route_as_zero_but_needs_the_series_in_both_years():
    sea = _sea(
        [("A_X", "HS", 2000, "r1", 50, 50), ("A_X", "HS", 2000, "r2", 50, 50), ("A_X", "HS", 2001, "r1", 60, 60)]
    )
    out = chain_link(sea, _all_measured(sea), 2000, 2001).set_index("year")
    assert out.loc[2001, "tonnes"] / out.loc[2000, "tonnes"] == pytest.approx(0.6)
    with pytest.raises(ValueError):
        chain_link(sea, {("A_X", "HS", "2701", 2000)}, 2000, 2001)


def test_chain_link_uses_only_series_measured_in_both_years():
    # two codes of one unit: coal measured both years (+10%), maize estimated in 2001 (a spurious doubling)
    sea = _sea([("A_X", "HS", 2000, "c", 100, 100), ("A_X", "HS", 2001, "c", 110, 110)])
    maize = pd.DataFrame(
        {"unit": "A_X", "cls": "HS", "year": [2000, 2001], "route_id": "m", "tonnes": [50.0, 100.0],
         "tonne_miles": [50.0, 100.0], "cmd": "1005", "commodity": "grains", "measured_tonnes": [50.0, 0.0]}
    )  # fmt: skip
    sea = pd.concat([sea, maize], ignore_index=True)
    measured = measured_series(sea)
    assert ("A_X", "HS", "grains", 2001) not in measured and ("A_X", "HS", "grains", 2000) in measured
    out = chain_link(sea, measured, 2000, 2001).set_index("year")
    assert out.loc[2001, "tonnes"] / out.loc[2000, "tonnes"] == pytest.approx(1.10)
    assert out.loc[2001, "n_series"] == 1
    # the level of the last year still counts every series (estimated tonnes included)
    assert out.loc[2001, "tonnes"] == pytest.approx(210.0)
    # a series excluded by the monthly cross-check drops out as well
    assert ("A_X", "HS", "coal", 2001) not in measured_series(sea, {("A_X", "2701", 2001)})


def test_a_series_is_a_commodity_group_so_code_switches_do_not_move_the_chain():
    # Australia's coal: SITC 3222 in 1980, 3221 in 1981 (same coal, +10%); a code-level chain would see 0 to 110
    sea = pd.DataFrame(
        {"unit": "AUS_X", "cls": "S2", "year": [1980, 1980, 1981], "route_id": "AUS-JPN-coal",
         "cmd": ["3222", "3221", "3221"], "commodity": "coal", "tonnes": [100.0, 0.5, 110.55],
         "tonne_miles": [100.0, 0.5, 110.55]}
    )  # fmt: skip
    sea["measured_tonnes"] = sea["tonnes"]
    out = chain_link(sea, measured_series(sea), 1980, 1981).set_index("year")
    assert out.loc[1981, "tonnes_chain"] == pytest.approx(1.10)


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


def test_undisclosed_destinations_are_allocated_pro_rata():
    flows = pd.DataFrame(
        {
            "unit": ["ZAF_X"] * 5,
            "cls": ["S2"] * 5,
            "year": [1985] * 5,
            "commodity": ["coal"] * 5,
            "is_world": [False, False, False, False, True],
            "route_class": ["sea", "sea", "land", "unspecified", "world"],
            "destination": ["JPN", "ITA", "ZWE", None, None],
            "tonnes": [30.0, 10.0, 10.0, 50.0, 100.0],
            "measured_tonnes": [30.0, 0.0, 10.0, 50.0, 100.0],
            "proxy_tonnes": [0.0, 10.0, 0.0, 0.0, 0.0],
            "cmd": ["2701"] * 5,
        }
    )
    sea = allocate_undisclosed(flows)
    # 50 undisclosed over 50 disclosed: every disclosed partner doubles; only sea rows are returned
    assert list(sea["destination"]) == ["JPN", "ITA"]
    assert sea["tonnes"].tolist() == pytest.approx([60.0, 20.0])
    assert sea["allocated_tonnes"].tolist() == pytest.approx([30.0, 10.0])
    # the undisclosed tonnes are measured: each sea row gains measured tonnes, its proxy part is unchanged
    assert sea["measured_tonnes"].tolist() == pytest.approx([60.0, 10.0])
    assert sea["proxy_tonnes"].tolist() == pytest.approx([0.0, 10.0])
    # undisclosed tonnes that are value estimates stay estimates once allocated
    est = flows.copy()
    est.loc[3, ["measured_tonnes", "proxy_tonnes"]] = [0.0, 50.0]
    sea = allocate_undisclosed(est)
    assert sea["measured_tonnes"].tolist() == pytest.approx([30.0, 0.0])
    assert sea["proxy_tonnes"].tolist() == pytest.approx([30.0, 20.0])


def test_a_code_with_mostly_undisclosed_destinations_leaves_its_series_unmeasured():
    # Australian wheat 1992: 7.3 Mt under "Special categories", 124 t disclosed (mostly to Switzerland)
    flows = pd.DataFrame(
        {"unit": "AUS_X", "cls": "HS", "commodity": "grains", "year": 1992, "is_world": False,
         "cmd": ["1001", "1001", "1001", "1003"], "route_class": ["unspecified", "no_port", "sea", "sea"],
         "tonnes": [7.3e6, 119.0, 5.0, 2.3e6]}
    )  # fmt: skip
    assert allocation_dominated(flows) == {("AUS_X", "HS", "grains", 1992)}
    sea = pd.DataFrame(
        {"unit": "AUS_X", "cls": "HS", "cmd": "1003", "commodity": "grains", "year": 1992,
         "tonnes": [2.3e6], "measured_tonnes": [2.3e6]}
    )  # fmt: skip
    assert measured_series(sea) == {("AUS_X", "HS", "grains", 1992)}
    assert measured_series(sea, dominated=allocation_dominated(flows)) == set()
    # a small undisclosed remainder does not
    flows.loc[0, "tonnes"] = 50.0
    assert allocation_dominated(flows) == set()


def test_a_shift_in_the_allocated_share_moves_the_haul_only_beyond_what_the_allocation_can_explain():
    # 1993: 100 t to Asia (4,000 nm), 20 of them allocated from undisclosed destinations;
    # 1994: all disclosed, 90 t to Asia and 20 t to Europe (9,000 nm)
    sea = pd.DataFrame(
        {"unit": "AUS_X", "cls": "HS", "cmd": "2601", "commodity": "iron_ore", "year": [1993, 1994, 1994],
         "route_id": ["asia", "asia", "europe"], "tonnes": [100.0, 90.0, 20.0],
         "tonne_miles": [400_000.0, 360_000.0, 180_000.0], "measured_tonnes": [100.0, 90.0, 20.0],
         "allocated_tonnes": [20.0, 0.0, 0.0]}
    )  # fmt: skip
    out = chain_link(sea, measured_series(sea), 1993, 1994).set_index("year")
    assert out.loc[1994, "tonnes_chain"] == pytest.approx(1.10)
    # haul change log(4909/4000) = 0.205; the 20% allocated could account for 0.2 x 5000/4000 = 0.25: all held
    assert out.loc[1994, "tm_chain"] == pytest.approx(1.10)
    assert out.loc[1994, "held"] == "AUS_X:HS:iron_ore"
    # with 3% allocated, most of the haul change is real and kept
    sea.loc[0, "allocated_tonnes"] = 3.0
    out = chain_link(sea, measured_series(sea), 1993, 1994).set_index("year")
    dh = np.log(540_000.0 / 110.0 / 4000.0)
    assert np.log(out.loc[1994, "tm_chain"] / 1.10) == pytest.approx(dh - 0.03 * 5000.0 / 4000.0)


def test_understated_partner_weight_becomes_an_estimate():
    # US maize 1990: Japan's weight implies 407 $/t while the other partners pay about 110 $/t
    rows = [
        ("S2", "1990", 842, "X", 392, "044", 4.05e9, 4.05e9, 8, 1.65e9),
        ("S2", "1990", 842, "X", 410, "044", 6.0e9, 6.0e9, 8, 0.66e9),
        ("S2", "1990", 842, "X", 156, "044", 5.0e9, 5.0e9, 8, 0.56e9),
        ("S2", "1990", 842, "X", 818, "044", 8.0e9, 8.0e9, 8, 0.90e9),
        ("S2", "1990", 842, "X", 0, "044", 23.05e9, 23.05e9, 8, 3.77e9),
    ]
    f = annual_flows(_rec(rows), {392: "JPN", 410: "KOR", 156: "CHN", 818: "EGY", 0: None}).set_index("destination")
    assert f.loc["JPN", "measured_tonnes"] == 0
    assert f.loc["JPN", "tonnes"] == pytest.approx(1.65e9 / ((0.66e9 + 0.56e9 + 0.90e9) / 19e6), rel=1e-6)
    assert f.loc["KOR", "measured_tonnes"] == pytest.approx(6e6)
    world = f[f["is_world"]].iloc[0]
    assert world["measured_tonnes"] == 0


def test_trading_hubs_are_undisclosed_for_the_commodities_they_do_not_consume():
    ports = {"SGP", "CHN", "CYM", "HKG"}
    assert classify_partner("AUS", "SGP", ports, "iron_ore") == "unspecified"
    assert classify_partner("AUS", "SGP", ports, "coal") == "sea"
    assert classify_partner("COL", "CYM", ports, "coal") == "unspecified"
    assert classify_partner("AUS", "CHN", ports, "iron_ore") == "sea"


def _rec(rows):
    cols = [
        "classification",
        "period",
        "reporterCode",
        "flowCode",
        "partnerCode",
        "cmdCode",
        "netWgt",
        "qty",
        "qtyUnitCode",
        "primaryValue",
    ]
    return pd.DataFrame(rows, columns=cols)


def test_estimated_weights_are_replaced_by_benchmark_carried_unit_values():
    rows = []
    # coal: 2007 and 2009 measured (different unit values per partner); 2008 estimated at one unit value
    for y, uvs in ((2007, (80.0, 90.0, 100.0)), (2009, (140.0, 150.0, 160.0))):
        for p, uv in zip((392, 410, 156), uvs, strict=True):
            rows.append(("HS", str(y), 124, "X", p, "2701", 1e9, 1e9, 8, uv * 1e6))
    for p in (392, 410, 156):
        rows.append(("HS", "2008", 124, "X", p, "2701", 3e9, 3e9, 8, 70.39 * 3e6))  # weights imply 70.39 $/t for all
    partners = {392: "JPN", 410: "KOR", 156: "CHN"}
    bench = pd.DataFrame({"coal": [60.0, 120.0, 72.0]}, index=pd.Index([2007, 2008, 2009], name="year"))
    f = annual_flows(_rec(rows), partners, bench)
    y8 = f[f["year"] == 2008]
    # premium over the benchmark: 2007 90/60 = 1.5, 2009 150/72; 2008 gets the geometric mean times 120
    premium = np.exp((np.log(90 / 60) + np.log(150 / 72)) / 2)
    assert y8["proxy_tonnes"].sum() == pytest.approx(3 * 70.39 * 3e6 / (premium * 120.0), rel=1e-6)
    assert (f[f["year"] != 2008]["proxy_tonnes"] == 0).all()


def test_distinct_unit_values_are_kept():
    rows = [
        ("HS", "2010", 36, "X", p, "2601", 1e9, 1e9, 8, uv * 1e6) for p, uv in ((156, 100.0), (392, 101.0), (410, 99.5))
    ]
    f = annual_flows(_rec(rows), {156: "CHN", 392: "JPN", 410: "KOR"})
    assert (f["proxy_tonnes"] == 0).all() and f["tonnes"].sum() == pytest.approx(3e6)


def test_chain_link_skips_a_unit_without_sea_tonnes_on_one_side():
    # B has no disclosed sea routes in 2000 (all destinations undisclosed), then 500 t in 2001: not growth
    sea = _sea(
        [("A_X", "HS", 2000, "a", 100, 100), ("A_X", "HS", 2001, "a", 110, 110), ("B_X", "HS", 2001, "b", 500, 500)]
    )
    out = chain_link(sea, _all_measured(sea), 2000, 2001).set_index("year")
    assert out.loc[2001, "tonnes"] / out.loc[2000, "tonnes"] == pytest.approx(1.10)
    assert out.loc[2001, "units"] == "A_X:HS"


def test_confidential_values_keep_their_weights_but_thousandfold_errors_do_not():
    # wheat: partner values suppressed (unit value under 5 $/t) but weights add up to the World weight
    rows = [
        ("HS", "2002", 36, "X", 360, "1001", 1.17e9, 1.17e9, 8, 0.45e6),
        ("HS", "2002", 36, "X", 392, "1001", 1.16e9, 1.16e9, 8, 0.52e6),
        ("HS", "2002", 36, "X", 156, "1001", 2.0e9, 2.0e9, 8, 300e6),
        ("HS", "2002", 36, "X", 0, "1001", 4.33e9, 4.33e9, 8, 301e6),
        # maize: weights a thousand times too small (unit value far above 3,000 $/t)
        ("HS", "2002", 842, "X", 392, "1005", 1.0e6, 1.0e6, 8, 100e6),
        ("HS", "2002", 842, "X", 0, "1005", 1.0e6, 1.0e6, 8, 100e6),
    ]
    f = annual_flows(_rec(rows), {360: "IDN", 392: "JPN", 156: "CHN", 0: None})
    idn = f[(f["destination"] == "IDN")].iloc[0]
    assert idn["measured_tonnes"] == pytest.approx(1.17e6) and idn["proxy_tonnes"] == 0
    jp_maize = f[(f["cmd"] == "1005") & (f["destination"] == "JPN")].iloc[0]
    assert jp_maize["measured_tonnes"] == 0


def test_comtrade_estimated_flag_and_per_code_unit_values():
    # 2005: soybeans and maize both weightless (flagged estimated); 2004 and 2006 measured at their own prices
    rows = []
    for y, soy_uv, maize_uv in ((2004, 250.0, 100.0), (2006, 260.0, 110.0)):
        rows += [("HS", str(y), 842, "X", 156, "1201", 20e9, 20e9, 8, soy_uv * 20e6, False),
                 ("HS", str(y), 842, "X", 392, "1005", 40e9, 40e9, 8, maize_uv * 40e6, False)]  # fmt: skip
    rows += [("HS", "2005", 842, "X", 156, "1201", 99e9, 99e9, 8, 255.0 * 20e6, True),
             ("HS", "2005", 842, "X", 392, "1005", 99e9, 99e9, 8, 105.0 * 40e6, True)]  # fmt: skip
    rec = pd.DataFrame(rows, columns=["classification", "period", "reporterCode", "flowCode", "partnerCode", "cmdCode",
                                      "netWgt", "qty", "qtyUnitCode", "primaryValue", "isNetWgtEstimated"])  # fmt: skip
    f = annual_flows(rec, {156: "CHN", 392: "JPN"}).set_index(["cmd", "year"])
    # the estimated 99 Mt weights are discarded; each code keeps its own unit value (not a pooled grain price)
    assert f.loc[("1201", 2005), "measured_tonnes"] == 0
    assert f.loc[("1201", 2005), "tonnes"] == pytest.approx(255.0 * 20e6 / np.sqrt(250.0 * 260.0), rel=1e-6)
    assert f.loc[("1005", 2005), "tonnes"] == pytest.approx(105.0 * 40e6 / np.sqrt(100.0 * 110.0), rel=1e-6)


def test_monthly_cross_check_flags_annual_weights_that_disagree():
    months = [f"2025{m:02d}" for m in range(1, 13)]
    monthly = pd.DataFrame({"freqCode": "M", "partnerCode": 0, "period": months, "reporterCode": 842, "flowCode": "X",
                            "cmdCode": "2701", "netWgt": [67e9 / 12] * 12})  # fmt: skip
    annual = pd.DataFrame({"classification": ["HS", "HS"], "partnerCode": [0, 0], "period": ["2025", "2025"],
                           "reporterCode": [842, 842], "flowCode": ["X", "X"], "cmdCode": ["2701", "1005"],
                           "netWgt": [52.8e9, 60e9]})  # fmt: skip
    assert monthly_mismatch(monthly, annual) == {("USA_X", "2701", 2025)}
    annual.loc[0, "netWgt"] = 66e9
    assert monthly_mismatch(monthly, annual) == set()
    # monthly weights far below a correct annual total do not flag the annual weight
    annual.loc[0, "netWgt"] = 240e9
    assert monthly_mismatch(monthly, annual) == set()


def test_long_out_of_sample_does_not_train_on_the_predicted_year():
    panel, prices = _synthetic_long()
    o1 = out_of_sample(panel, prices)
    k = 2010
    p2 = panel.copy()
    p2.loc[p2["year"] == k, "index"] *= 3
    o2 = out_of_sample(p2, prices)
    e1 = {p["year"]: p["extended"] for p in o1["predictions"] if p["year"] <= k}
    e2 = {p["year"]: p["extended"] for p in o2["predictions"] if p["year"] <= k}
    assert k in e1 and e1 == pytest.approx(e2)
