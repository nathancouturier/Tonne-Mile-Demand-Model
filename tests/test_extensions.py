import numpy as np
import pandas as pd
import pytest

from tmdm.model.extensions import run_extensions, with_minor_bulks
from tmdm.model.signals import Signal, out_of_sample
from tmdm.transform.route_distances import minor_route_ports, portwatch_blend


def _synthetic(n: int = 200, seed: int = 5) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    pidx = pd.period_range("2008-01", periods=n, freq="M")
    prices = pd.DataFrame(
        {
            "brent_usd_bbl": 60 * np.exp(np.cumsum(rng.normal(0, 0.06, n))),
            "iron_ore_usd_dmtu": 90 * np.exp(np.cumsum(rng.normal(0, 0.05, n))),
            "cli_chn": 100 + np.cumsum(rng.normal(0, 0.3, n)),
        },
        index=pidx,
    )
    idx = pidx[24:]
    m = len(idx)
    tm = 1e12 * np.exp(np.cumsum(rng.normal(0.003, 0.02, m)))
    fleet = 8e8 * np.exp(np.cumsum(np.full(m, 0.003)))
    log_bal = np.log(tm / fleet)
    lo = np.log(prices["brent_usd_bbl"]).reindex(idx).to_numpy()
    lr = np.log(prices["iron_ore_usd_dmtu"]).shift(3).reindex(idx).to_numpy()
    log_bdi = 7.0 + 2.0 * (log_bal - log_bal[0]) + 0.5 * (lo - lo[0]) + 0.8 * (lr - lr[0]) + rng.normal(0, 0.02, m)
    panel = pd.DataFrame(
        {"index": np.exp(log_bdi), "tonnes": tm / 5000, "tonne_miles": tm, "fleet": fleet, "supply_factor": 1.0},
        index=idx,
    )
    return panel, prices


def test_with_minor_bulks_adds_over_common_months():
    panel, _ = _synthetic()
    minor = pd.DataFrame(
        {"month": panel.index[10:], "tonnes": 1e6, "tonne_miles": 5e9, "avg_haul_nm": 5000.0}
    ).reset_index(drop=True)
    out = with_minor_bulks(panel, minor)
    assert out.index[0] == panel.index[10]
    assert len(out) == len(panel) - 10
    assert out["tonne_miles"].iloc[0] == pytest.approx(panel["tonne_miles"].iloc[10] + 5e9)
    assert out["tonnes"].iloc[-1] == pytest.approx(panel["tonnes"].iloc[-1] + 1e6)
    assert (out["fleet"] == panel.loc[out.index, "fleet"]).all()


def test_run_extensions_scores_variants_against_headline():
    panel, prices = _synthetic()
    minor = pd.DataFrame(
        {"month": panel.index, "tonnes": panel["tonnes"].to_numpy() * 0.1, "tonne_miles": panel["tonne_miles"] * 0.09}
    ).reset_index(drop=True)
    res = run_extensions(panel, prices, minor)
    ids = [v["id"] for v in res["variants"]]
    assert ids[0] == "headline"
    assert "minor_bulks" in ids
    assert "cli_china_lead6" in ids and "cli_china_instead_of_ore_lead0" in ids
    assert not any(i.startswith("cli_g20") for i in ids)  # no G20 column in the prices
    head = res["variants"][0]
    assert head["oos_r2_gain"] is None and head["clears_margin"] is False
    rows = {v["id"]: v for v in res["variants"]}
    # a constant 9 percent share leaves twelve-month changes and the fit unchanged
    assert rows["minor_bulks"]["minor_tonne_miles_share"] == pytest.approx(0.09)
    assert rows["minor_bulks"]["oos_r2_extended"] == pytest.approx(head["oos_r2_extended"], abs=1e-9)
    for v in res["variants"][1:]:
        assert v["clears_margin"] == (v["oos_r2_gain"] > res["adoption_margin"])
    # the headline row reproduces the direct out-of-sample test
    from tmdm.model.decomposition import headline_elasticity

    sig = (Signal("oil", "brent_usd_bbl", 0, ""), Signal("iron_ore", "iron_ore_usd_dmtu", 3, ""))
    direct = out_of_sample(panel, prices, headline_elasticity, sig)
    assert head["oos_r2_extended"] == pytest.approx(direct["r2_vs_zero"]["extended"])


def _ports_db() -> pd.DataFrame:
    rows = [
        ("p1", "Gulf A", "USA", 29.9, -90.0, 500),
        ("p2", "East B", "USA", 36.8, -76.3, 300),
        ("p3", "West C", "USA", 46.1, -122.9, 400),
        ("p4", "West D", "USA", 47.6, -122.3, 100),
        ("p5", "Tiny", "USA", 40.0, -74.0, 0),
        ("p6", "Qingdao", "CHN", 36.1, 120.3, 900),
        ("p7", "Rotterdam", "NLD", 51.9, 4.1, 700),
        ("p8", "Newcastle", "AUS", -32.9, 151.8, 800),
    ]
    return pd.DataFrame(rows, columns=["portid", "portname", "ISO3", "lat", "lon", "vessel_count_dry_bulk"])


def test_portwatch_blend_weights_and_coasts():
    db = _ports_db()
    ports, weights = portwatch_blend(db, "USA")
    assert [p.port_id for p in ports] == ["PW_p1", "PW_p3", "PW_p2"]
    assert sum(w for _p, w in weights) == pytest.approx(1.0)
    assert dict(weights)["PW_p1"] == pytest.approx(500 / 1200)
    west, ww = portwatch_blend(db, "USA", "west")
    assert {p.port_id for p in west} == {"PW_p3", "PW_p4"}
    assert dict(ww)["PW_p3"] == pytest.approx(0.8)
    east, _ = portwatch_blend(db, "USA", "east")
    assert {p.port_id for p in east} == {"PW_p1", "PW_p2"}  # the port without dry bulk calls is ignored
    with pytest.raises(LookupError):
        portwatch_blend(db, "FRA")


def test_minor_route_ports_pick_the_coast_by_trade():
    db = _ports_db()
    routes = pd.DataFrame(
        {
            "route_id": ["USA-CHN-minor_bulks", "USA-NLD-minor_bulks", "AUS-USA-minor_bulks"],
            "origin": ["USA", "USA", "AUS"],
            "destination": ["CHN", "NLD", "USA"],
        }
    )
    ports, pairs = minor_route_ports(routes, db)
    load_pacific = {pid for pid, _w in pairs["USA-CHN-minor_bulks"][2]}
    load_atlantic = {pid for pid, _w in pairs["USA-NLD-minor_bulks"][2]}
    disc_from_aus = {pid for pid, _w in pairs["AUS-USA-minor_bulks"][3]}
    assert load_pacific == {"PW_p3", "PW_p4"}
    assert load_atlantic == {"PW_p1", "PW_p2"}
    assert disc_from_aus == {"PW_p3", "PW_p4"}
    assert set(ports) >= load_pacific | load_atlantic | {"PW_p6", "PW_p7", "PW_p8"}
