import pytest

from tmdm.transform.distances import Port, haversine_nm, sea_distance_nm

# Reference sea distances in nautical miles are taken from widely published
# port-to-port tables (rounded); the routing network is accepted if it is
# within 7 percent of the reference.
TUBARAO = Port("BRTUB", "Tubarao", "Brazil", -20.28, -40.24)
PORT_HEDLAND = Port("AUPHE", "Port Hedland", "Australia", -20.31, 118.58)
QINGDAO = Port("CNTAO", "Qingdao", "China", 36.05, 120.33)
ROTTERDAM = Port("NLRTM", "Rotterdam", "Netherlands", 51.95, 4.05)
RICHARDS_BAY = Port("ZARCB", "Richards Bay", "South Africa", -28.80, 32.08)
NEW_ORLEANS = Port("USMSY", "New Orleans", "United States", 29.95, -90.07)
NOVOROSSIYSK = Port("RUNVS", "Novorossiysk", "Russia", 44.72, 37.78)


def test_haversine_equator_degree():
    # one degree of longitude at the equator is 60 nautical miles
    assert haversine_nm(0, 0, 0, 1) == pytest.approx(60.0, rel=0.002)


@pytest.mark.parametrize(
    ("o", "d", "reference_nm"),
    [
        (TUBARAO, QINGDAO, 11_000),  # Brazil to China via the Cape of Good Hope
        (PORT_HEDLAND, QINGDAO, 3_600),  # Western Australia to North China
        (TUBARAO, ROTTERDAM, 5_200),  # Brazil to Northwest Europe
        (RICHARDS_BAY, ROTTERDAM, 7_000),  # South Africa to Northwest Europe
    ],
)
def test_reference_distances(o, d, reference_nm):
    dist, coords = sea_distance_nm(o, d)
    assert dist == pytest.approx(reference_nm, rel=0.07)
    assert len(coords) > 2


def test_distance_never_below_great_circle():
    dist, _ = sea_distance_nm(NEW_ORLEANS, QINGDAO)
    assert dist >= haversine_nm(NEW_ORLEANS.lat, NEW_ORLEANS.lon, QINGDAO.lat, QINGDAO.lon)


def test_suez_restriction_lengthens_black_sea_to_china():
    shortest, _ = sea_distance_nm(NOVOROSSIYSK, QINGDAO, "shortest")
    no_suez, _ = sea_distance_nm(NOVOROSSIYSK, QINGDAO, "avoid_suez")
    assert no_suez > shortest * 1.25


def test_panama_restriction_lengthens_us_gulf_to_china():
    shortest, _ = sea_distance_nm(NEW_ORLEANS, QINGDAO, "shortest")
    no_panama, _ = sea_distance_nm(NEW_ORLEANS, QINGDAO, "avoid_panama")
    assert no_panama > shortest * 1.15
