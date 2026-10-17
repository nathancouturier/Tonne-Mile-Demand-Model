"""Long history: an annual panel from 1980 for the multi-decade decomposition.

The monthly panel starts in December 2012 (monthly customs data begin in 2010,
the public Baltic Dry Index table in 2011). This module builds an annual panel
from 1980 with the same four forces:

Cargo volume and haul
    Annual UN Comtrade exports of iron ore, coal and grains by the covered
    exporters to every partner, plus Russian and Mozambican coal from Indian
    and Japanese import records. Exports to land neighbours (mode of transport
    is not separated) and to countries without a dry bulk port are left out
    and their share is reported. Each route's distance blends the named
    loading and discharge ports where the monthly model defines them, and
    otherwise the three ports of each country with the most dry bulk calls in
    the IMF PortWatch ports table.

Chain-linking
    Reporters enter the record in different years and the classification
    changes from SITC Revision 2 to HS around 1990. The change in tonnes and
    tonne-miles between two years is therefore measured on the reporters
    present in both years, within one classification (HS where both years
    have it), and the annual changes are chained. A reporter's arrival or a
    change of classification never shows up as a jump.

Freight index
    The Baltic Dry Index (annual mean of the monthly ASUB table) from 2011.
    Before 2011 no public BDI series was found; the Kilian index of dry cargo
    freight rates (Federal Reserve Bank of Dallas), which is deflated by US
    consumer prices and detrended, is converted back to nominal terms and
    calibrated on the months where both series exist:

        log BDI_t = a + b t + kilian_t / 100 + log CPI_t + error

    and the fitted value is the proxy before 2011. Proxy years are flagged.

Fleet and effective supply
    UNCTAD bulk carrier deadweight at 1 January, averaged over the two
    surrounding years for each calendar year; the effective supply factor is
    the annual mean of the monthly factor (one before PortWatch coverage).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
import statsmodels.api as sm

from tmdm.config import PROCESSED_DIR
from tmdm.ingest import comtrade, comtrade_long, kilian, oecd_cpi, portwatch, worldbank, wpi
from tmdm.io import raw_dir
from tmdm.reference import DISCHARGE, LOADING, LOADING_BY_DESTINATION, PACIFIC_DESTINATIONS, loading_blend
from tmdm.transform.distances import Port, sea_distance_nm
from tmdm.transform.route_distances import (
    PACIFIC_ORIGINS,
    TWO_COAST_COUNTRIES,
    baseline_variant,
    portwatch_blend,
    resolve_ports,
)

log = logging.getLogger(__name__)

FIRST_YEAR = comtrade_long.FIRST_YEAR
UNIT_VALUE_BOUNDS = (5.0, 3000.0)  # US dollars per tonne; weights implying values outside are not used
# commodity-years where at least this share of partner rows share one unit value have estimated weights
ESTIMATED_MODAL_SHARE = 0.5
ESTIMATED_MIN_ROWS = 3
MIN_VALID_VALUE_SHARE = 0.5  # share of a year's value that rows with usable weights must carry to set its unit value
MEASURED_SHARE = 0.9  # a series-year counts as measured if at least this share of its tonnes has measured weights
MONTHLY_TOLERANCE = 0.15  # largest gap between an annual weight and the sum of its twelve monthly weights
# a code-year with more undisclosed than disclosed tonnes (and at least this many) leaves its series unmeasured
MIN_DOMINATED_TONNES = 0.1e6
# when a series' allocated share moves by more than this between two years, its haul is held for that change
MAX_ALLOCATION_SHIFT = 0.02
# a partner weight is understated when its unit value is this many times the code-year's median ...
OUTLIER_RATIO = 2.5
OUTLIER_MIN_TONNES = 0.5e6  # ... and the tonnes its value implies at the median exceed it by this much
OUTLIER_MIN_SHARE = 0.05  # ... and by this share of the code-year's partner tonnes
# code-years whose median unit value is below this share of the benchmark price have no reliable median
BROKEN_MEDIAN = 0.3

COMMODITY_OF: dict[str, dict[str, str]] = {
    "HS": {
        "2601": "iron_ore",
        "2701": "coal",
        "2702": "coal",
        "1001": "grains",
        "1003": "grains",
        "1005": "grains",
        "1007": "grains",
        "1201": "grains",
    },
    "S2": {
        "281": "iron_ore",
        "3221": "coal",
        "3222": "coal",
        "3223": "coal",
        "041": "grains",
        "043": "grains",
        "044": "grains",
        "045": "grains",
        "2222": "grains",
    },
}
REPORTER_ISO = {code: iso.replace("_SACU", "") for iso, code in comtrade_long.EXPORTERS.items()}
IMPORTER_ISO = {code: iso for iso, code in comtrade_long.IMPORTERS.items()}
# Partners that no longer exist, mapped to a successor for ports; land-locked successors drop out with no port.
SUCCESSOR = {
    "SUN": "RUS",
    "DDR": "DEU",
    "YUG": "HRV",
    "CSK": "CZE",
    "SCG": "SRB",
    "ZA1": "ZAF",
    "ANT": "CUW",
    "YMD": "YEM",
}
# Comtrade records Taiwan as "Other Asia, nes" (partner code 490).
PARTNER_CODE_ISO = {490: "TWN"}
# Coastal partners without dry bulk calls in the PortWatch table: distances use a neighbouring coast's ports.
PORT_PROXY_COUNTRY = {"PRK": "KOR"}
# Partners whose destination is not disclosed ("Areas, nes", "Special categories", free zones, bunkers):
# their tonnes are allocated to the disclosed destinations of the same exporter, commodity and year.
UNDISCLOSED = "unspecified"
# Trading hubs recorded as destinations of cargo they do not consume, treated as undisclosed: the Cayman
# Islands and the Bahamas for every commodity, and iron ore to economies without ironmaking (Colombian coal
# to the Cayman Islands in 2008, 8.1 Mt; Australian iron ore to Singapore and Hong Kong in 2020, 37 Mt).
TRADING_HUBS: dict[str, frozenset[str] | None] = {
    "CYM": None,
    "BHS": None,
    "SGP": frozenset({"iron_ore"}),
    "HKG": frozenset({"iron_ore"}),
    "CUW": frozenset({"iron_ore"}),
}
# Land neighbours where bulk cargo can move by rail, road or river; pairs separated by roadless terrain
# (Colombia and Brazil, Peru or Panama; Brazil and Venezuela or the Guianas) trade bulk by sea and stay in.
LAND_NEIGHBOURS_LONG: dict[str, frozenset[str]] = {
    "USA": frozenset({"CAN", "MEX"}),
    "CAN": frozenset({"USA"}),
    "BRA": frozenset({"ARG", "BOL", "PRY", "URY"}),
    "ARG": frozenset({"BOL", "BRA", "CHL", "PRY", "URY"}),
    "COL": frozenset({"ECU", "VEN"}),
    "UKR": frozenset({"BLR", "HUN", "MDA", "POL", "ROU", "RUS", "SVK"}),
    "ZAF": frozenset({"BWA", "LSO", "MOZ", "NAM", "SWZ", "ZWE"}),
    "IDN": frozenset({"PNG", "TLS"}),
}
LOAD_KEY = {"iron_ore": "iron_ore", "coal": "coal", "grains": "grains"}
# importer-reported coal: loading key by origin (as in the monthly panel)
IMPORTER_LOAD_KEY = {("IND", "RUS"): "coal_pacific", ("JPN", "RUS"): "coal_pacific", ("IND", "MOZ"): "coal"}


# ---------------------------------------------------------------- partners and tonnes
def partner_iso() -> dict[int, str | None]:
    """Comtrade partner code to ISO3 (successor-mapped); None for groups and unspecified areas."""
    data = json.loads((raw_dir("un_comtrade") / "partnerAreas.json").read_text(encoding="utf-8"))
    out: dict[int, str | None] = {}
    for r in data["results"]:
        if not str(r.get("id")).isdigit():
            continue
        code = int(r["id"])
        iso = str(r.get("PartnerCodeIsoAlpha3") or "").strip()
        iso = PARTNER_CODE_ISO.get(code, SUCCESSOR.get(iso, iso))
        valid = len(iso) == 3 and iso.isalpha() and iso.isupper() and not r.get("isGroup")
        out[code] = iso if valid else None
    return out


def _weights(rec: pd.DataFrame) -> pd.DataFrame:
    """Tonnes from net weight (or quantity in kilograms), with unusable weights set to missing.

    A weight implying more than the upper unit-value bound is unusable (weights a thousand times too small,
    as in US records for 2000 and 2001). A weight implying less than the lower bound is kept when the
    reporter's partner weights add up to its World weight for that commodity and year: the value was then
    withheld for confidentiality, not the weight mis-stated (Australian wheat, 2000 to 2008). Weights that
    Comtrade flags as estimated are set to missing in :func:`annual_flows`.
    """
    df = rec.copy()
    nw = pd.to_numeric(df["netWgt"], errors="coerce")
    qty = pd.to_numeric(df["qty"], errors="coerce")
    unit = (
        pd.to_numeric(df["qtyUnitCode"], errors="coerce") if "qtyUnitCode" in df else pd.Series(np.nan, index=df.index)
    )
    kg = nw.where(nw > 0, qty.where((unit == 8) & (qty > 0)))
    df["value_usd"] = pd.to_numeric(df["primaryValue"], errors="coerce").fillna(0.0)
    df["tonnes_reported"] = kg / 1000.0
    uv = df["value_usd"] / df["tonnes_reported"]
    key = [
        df["reporterCode"],
        df["classification"].astype(str),
        df["cmdCode"].astype(str),
        df["period"].astype(str),
        df["flowCode"].astype(str),
    ]
    world = df["partnerCode"].astype(int) == 0
    part_sum = df["tonnes_reported"].where(~world).groupby(key).transform("sum")
    world_w = df["tonnes_reported"].where(world).groupby(key).transform("max")
    consistent = (world_w > 0) & ((part_sum / world_w - 1.0).abs() < 0.05)
    low = uv < UNIT_VALUE_BOUNDS[0]
    ok = df["tonnes_reported"].gt(0) & (uv <= UNIT_VALUE_BOUNDS[1]) & (~low | consistent)
    df.loc[~ok, "tonnes_reported"] = np.nan
    return df


def benchmark_prices(prices_m: pd.DataFrame) -> pd.DataFrame:
    """Annual World Bank benchmark prices per commodity group, to carry unit values across years.

    Iron ore (CFR China), Australian coal and, for grains, the geometric mean of US wheat, maize and soybeans.
    """
    a = prices_m.groupby(pd.PeriodIndex(prices_m.index).year).mean()
    out = pd.DataFrame(index=a.index)
    out["iron_ore"] = a["iron_ore_usd_dmtu"]
    out["coal"] = a["coal_australia_usd_t"]
    out["grains"] = np.exp(np.log(a[["wheat_us_hrw_usd_t", "maize_usd_t", "soybeans_usd_t"]]).mean(axis=1))
    out.index.name = "year"
    return out


def _estimated_weights(rec: pd.DataFrame) -> pd.Series:
    """Rows of commodity-years whose weights were estimated, not measured.

    Comtrade flags estimated weights (``isNetWgtEstimated``) when it has the flag. In addition, when at
    least half of a reporter's partner rows for a commodity and year share one unit value (to four
    significant figures), the weights were set from a few standard unit values: Canada's 2008 records put
    most coal at 70.39 and most wheat at 231.67 dollars a tonne while prices were near their peak. Every row
    of such a commodity-year, the World total included, is then treated as having no measured weight.
    """
    flagged = pd.Series(False, index=rec.index)
    if "isNetWgtEstimated" in rec.columns:
        flagged = rec["isNetWgtEstimated"].astype(str).str.lower().isin(["true", "1", "1.0"])
    part = rec[~rec["is_world"] & rec["tonnes_reported"].notna()]
    uv4 = (part["value_usd"] / part["tonnes_reported"]).map(lambda v: float(f"{v:.4g}"))
    key = [part["unit"], part["cls"], part["cmd"], part["year"]]
    n = uv4.groupby(key).transform("size")
    modal = uv4.groupby(key).transform(lambda x: x.value_counts().iloc[0])
    flagged_rows = part[(n >= ESTIMATED_MIN_ROWS) & (modal / n >= ESTIMATED_MODAL_SHARE)]
    groups = set(zip(flagged_rows["unit"], flagged_rows["cls"], flagged_rows["cmd"], flagged_rows["year"], strict=True))
    keys = list(zip(rec["unit"], rec["cls"], rec["cmd"], rec["year"], strict=True))
    modal_flag = pd.Series([k in groups for k in keys], index=rec.index, dtype=bool)
    return flagged | modal_flag


def _weighted_median(values: np.ndarray, weights: np.ndarray) -> float:
    order = np.argsort(values)
    cum = np.cumsum(weights[order])
    return float(values[order][np.searchsorted(cum, 0.5 * cum[-1])])


def _understated_weights(rec: pd.DataFrame, bench: pd.DataFrame | None) -> pd.Series:
    """Rows of code-years where a partner weight is materially understated.

    A partner row is understated when its unit value is more than OUTLIER_RATIO times the tonnes-weighted
    median of the same unit, classification, code and year, and its value at that median implies at least
    OUTLIER_MIN_TONNES and OUTLIER_MIN_SHARE of the code-year's partner tonnes more than it reports (US maize
    to Japan in 1990 and 1991: 4.1 and 3.7 Mt at about 410 dollars a tonne while every other partner paid
    110 to 125). The World total of such a code-year carries the same understatement and is flagged too.
    Code-years whose median is below BROKEN_MEDIAN times the benchmark price are not tested.
    """
    part = rec[~rec["is_world"] & rec["tonnes_reported"].notna() & (rec["value_usd"] > 0)]
    out = pd.Series(False, index=rec.index)
    groups = set()
    for (unit, cls, cmd, year), g in part.groupby(["unit", "cls", "cmd", "year"]):
        t = g["tonnes_reported"].to_numpy(dtype=float)
        v = g["value_usd"].to_numpy(dtype=float)
        med = _weighted_median(v / t, t)
        group = str(g["commodity"].iloc[0])
        if bench is not None and group in bench.columns and year in bench.index:
            if med < BROKEN_MEDIAN * float(bench[group].get(year, np.nan)):
                continue
        missing = v / med - t
        bad = (v / t > OUTLIER_RATIO * med) & (missing >= OUTLIER_MIN_TONNES) & (missing >= OUTLIER_MIN_SHARE * t.sum())
        if bad.any():
            out.loc[g.index[bad]] = True
            groups.add((unit, cls, cmd, year))
    keys = zip(rec["unit"], rec["cls"], rec["cmd"], rec["year"], strict=True)
    world = rec["is_world"] & pd.Series([k in groups for k in keys], index=rec.index, dtype=bool)
    return out | world


def _carry(log_uv: pd.Series, ref: pd.Series | None) -> pd.Series:
    """Fill missing years of a log unit value: the premium over the benchmark is interpolated, then added back."""
    if ref is not None and ref.notna().all():
        return (log_uv - ref).interpolate(limit_direction="both") + ref
    return log_uv.interpolate(limit_direction="both")


def annual_flows(
    records: pd.DataFrame, partners: dict[int, str | None], bench: pd.DataFrame | None = None
) -> pd.DataFrame:
    """Tidy annual flows per commodity code: unit, classification, year, origin, destination, tonnes.

    A unit is a reporter and flow (for example AUS exports, IND coal imports). ``measured_tonnes`` holds
    the weights as reported and usable; rows without one (missing, implausible, estimated or understated) get tonnes
    from value over the unit value of the same unit, classification and commodity code that year, and are
    flagged as proxies. A year whose usable rows carry less than half the value has no unit value of its
    own: it is carried from the neighbouring years in proportion to the World Bank benchmark price of the
    commodity group (``bench``), or interpolated if no benchmark is given; a code with no usable year at all
    takes the group's unit value. Proxies fill levels and coverage; the chained changes use measured
    series only (see :func:`chain_link`).
    """
    rec = records.copy()
    rec["cls"] = rec["classification"].astype(str)
    rec["cmd"] = rec["cmdCode"].astype(str)
    rec["commodity"] = [COMMODITY_OF.get(c, {}).get(k) for c, k in zip(rec["cls"], rec["cmd"], strict=True)]
    rec = rec.dropna(subset=["commodity"])
    rec["year"] = rec["period"].astype(str).str[:4].astype(int)
    rec["reporterCode"] = rec["reporterCode"].astype(int)
    rec["partnerCode"] = rec["partnerCode"].astype(int)
    rec = _weights(rec)
    is_x = rec["flowCode"].astype(str) == "X"
    rec["unit"] = np.where(
        is_x,
        rec["reporterCode"].map(REPORTER_ISO).astype(str) + "_X",
        rec["reporterCode"].map(IMPORTER_ISO).astype(str) + "_M",
    )
    rec["partner_iso"] = rec["partnerCode"].map(partners)
    rec["origin"] = np.where(is_x, rec["reporterCode"].map(REPORTER_ISO), rec["partner_iso"])
    rec["destination"] = np.where(is_x, rec["partner_iso"], rec["reporterCode"].map(IMPORTER_ISO))
    rec["is_world"] = rec["partnerCode"] == 0
    rec.loc[_estimated_weights(rec), "tonnes_reported"] = np.nan
    rec.loc[_understated_weights(rec, bench), "tonnes_reported"] = np.nan
    rec["measured_tonnes"] = rec["tonnes_reported"].fillna(0.0)
    # unit values per unit, classification, commodity code and year from rows with usable weights
    partners_only = rec[~rec["is_world"]]
    valid = partners_only[partners_only["tonnes_reported"].notna()]

    def unit_values(key: list[str]) -> pd.DataFrame:
        uv = valid.groupby(key)[["value_usd", "tonnes_reported"]].sum()
        uv["share"] = uv["value_usd"] / partners_only.groupby(key)["value_usd"].sum().reindex(uv.index)
        # a year's unit value is trusted only if rows with usable weights carry most of its value
        uv = uv[uv["share"] >= MIN_VALID_VALUE_SHARE]
        return (uv["value_usd"] / uv["tonnes_reported"]).rename("uv").reset_index()

    def fill(uv: pd.DataFrame, by: list[str]) -> pd.DataFrame:
        bounds = rec.groupby(by)["year"].agg(["min", "max"])
        span = {k: (int(v["min"]), int(v["max"])) for k, v in bounds.to_dict(orient="index").items()}
        out = []
        for k, g in uv.groupby(by):
            lo, hi = span[k]
            years = pd.RangeIndex(lo, hi + 1)
            group = str(g["commodity"].iloc[0])
            ref = np.log(bench[group]).reindex(years) if bench is not None and group in bench.columns else None
            s = _carry(np.log(g.set_index("year")["uv"]).reindex(years), ref)
            out.append(pd.DataFrame(dict(zip(by, k, strict=True)) | {"year": years, "uv": np.exp(s.values)}))
        return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=[*by, "year", "uv"])

    by_cmd = fill(unit_values(["unit", "cls", "cmd", "commodity", "year"]), ["unit", "cls", "cmd", "commodity"])
    by_group = fill(unit_values(["unit", "cls", "commodity", "year"]), ["unit", "cls", "commodity"])
    rec = rec.merge(by_cmd, on=["unit", "cls", "cmd", "commodity", "year"], how="left")
    rec = rec.merge(by_group.rename(columns={"uv": "uv_group"}), on=["unit", "cls", "commodity", "year"], how="left")
    rec["uv"] = rec["uv"].fillna(rec["uv_group"])
    proxy = rec["tonnes_reported"].isna() & (rec["value_usd"] > 0) & rec["uv"].notna()
    rec["proxy_tonnes"] = np.where(proxy, rec["value_usd"] / rec["uv"], 0.0)
    rec["tonnes"] = rec["tonnes_reported"].fillna(0.0) + rec["proxy_tonnes"]
    rec["no_weight"] = rec["tonnes_reported"].isna() & ~proxy & (rec["value_usd"] > 0)
    keep = ["unit", "cls", "cmd", "year", "origin", "destination", "commodity", "partnerCode", "is_world", "tonnes",
            "measured_tonnes", "proxy_tonnes", "value_usd", "no_weight"]  # fmt: skip
    return rec[keep]


def allocate_undisclosed(flows: pd.DataFrame) -> pd.DataFrame:
    """Sea-route rows with the undisclosed destinations allocated pro rata.

    For each unit, classification, year and commodity code, tonnes sent to undisclosed destinations are
    spread over the disclosed partners in proportion to their tonnes (sea, land and no-port alike), so
    that the sea routes receive their share. The measured and the estimated parts of the undisclosed
    tonnes are allocated separately, so allocated estimates stay estimates. A reporter that discloses no
    destination at all in a year (South Africa's customs union before 1992) has nothing to allocate to,
    and drops out of that year.
    """
    x = flows[~flows["is_world"]]
    key = ["unit", "cls", "year", "cmd"]
    parts = ["tonnes", "measured_tonnes", "proxy_tonnes"]
    disclosed = x[x["route_class"] != UNDISCLOSED].groupby(key)["tonnes"].sum().rename("disclosed")
    undisclosed = x[x["route_class"] == UNDISCLOSED].groupby(key)[parts].sum().add_prefix("u_")
    alloc = pd.concat([disclosed, undisclosed], axis=1).fillna(0.0)
    alloc = alloc[alloc["disclosed"] > 0].reset_index()
    sea = x[x["route_class"] == "sea"].merge(alloc, on=key, how="left")
    sea[["disclosed", *[f"u_{c}" for c in parts]]] = sea[["disclosed", *[f"u_{c}" for c in parts]]].fillna(0.0)
    share = (sea["tonnes"] / sea["disclosed"].where(sea["disclosed"] > 0)).fillna(0.0)
    sea["allocated_tonnes"] = share * sea["u_tonnes"]
    for c in parts:
        sea[c] = sea[c] + share * sea[f"u_{c}"]
    return sea.drop(columns=["disclosed", *[f"u_{c}" for c in parts]]).copy()


def allocation_dominated(flows: pd.DataFrame) -> set[tuple[str, str, str, int]]:
    """(unit, cls, commodity, year) containing a code-year whose destinations are mostly undisclosed.

    When a code's undisclosed tonnes exceed its disclosed tonnes (and are at least MIN_DOMINATED_TONNES),
    the allocation decides where most of it goes: Australian wheat in 1992 put 7.3 Mt under "Special
    categories" and disclosed 124 tonnes, mostly to Switzerland, so the pro rata allocation sent the
    wheat to a country without a port and out of the sea routes.
    """
    x = flows[~flows["is_world"]]
    key = ["unit", "cls", "commodity", "year", "cmd"]
    und = x["route_class"] == UNDISCLOSED
    g = pd.concat(
        [x[~und].groupby(key)["tonnes"].sum().rename("disclosed"), x[und].groupby(key)["tonnes"].sum().rename("u")],
        axis=1,
    ).fillna(0.0)
    g = g[(g["u"] > g["disclosed"]) & (g["u"] >= MIN_DOMINATED_TONNES)]
    return {(str(u), str(c), str(k), int(y)) for u, c, k, y, _cmd in g.index}


def monthly_mismatch(
    monthly: pd.DataFrame, annual: pd.DataFrame, tolerance: float = MONTHLY_TOLERANCE
) -> set[tuple[str, str, int]]:
    """(unit, cmd, year) whose annual HS World weight falls short of the sum of twelve monthly World weights.

    Both come from UN Comtrade for the same reporter and code. Where the annual weight is more than
    ``tolerance`` below the twelve monthly weights it is missing tonnes and is not treated as measured
    (US coal in 2025: 52.8 Mt annual against 67.0 Mt over the twelve months). The reverse gap is not used:
    monthly weights that add up to far less than a correct annual total are common (US wheat in 2016:
    2.2 Mt over the months against 24.0 Mt for the year).
    """
    m = monthly[(monthly["freqCode"].astype(str) == "M") & (monthly["partnerCode"].astype(int) == 0)].copy()
    if m.empty:
        return set()
    m["year"] = m["period"].astype(str).str[:4].astype(int)
    m["w"] = pd.to_numeric(m["netWgt"], errors="coerce")
    g = m.groupby(["reporterCode", "flowCode", "cmdCode", "year"]).agg(
        w=("w", "sum"), n=("period", "nunique"), na=("w", lambda s: s.isna().sum())
    )
    g = g[(g["n"] == 12) & (g["na"] == 0)].reset_index()
    a = annual[(annual["classification"] == "HS") & (annual["partnerCode"].astype(int) == 0)].copy()
    a["year"] = a["period"].astype(str).str[:4].astype(int)
    a["aw"] = pd.to_numeric(a["netWgt"], errors="coerce")
    j = a.merge(g, on=["reporterCode", "flowCode", "cmdCode", "year"], how="inner")
    j = j[(j["w"] > 0) & (j["aw"] > 0)]
    bad = j[j["aw"] < (1.0 - tolerance) * j["w"]]
    out = set()
    for r in bad.to_dict(orient="records"):
        iso = (
            REPORTER_ISO.get(int(r["reporterCode"]))
            if str(r["flowCode"]) == "X"
            else IMPORTER_ISO.get(int(r["reporterCode"]))
        )
        if iso:
            out.add((f"{iso}_{r['flowCode']}", str(r["cmdCode"]), int(r["year"])))
    return out


def measured_series(
    sea: pd.DataFrame,
    exclude: set[tuple[str, str, int]] | None = None,
    dominated: set[tuple[str, str, str, int]] | None = None,
) -> set[tuple[str, str, str, int]]:
    """(unit, cls, commodity, year) whose sea-route tonnes are mostly measured (at least MEASURED_SHARE).

    A series is one unit and commodity group (iron ore, coal, grains) in one classification, not one
    commodity code: reporters move the same coal between codes from year to year (Australia records it as
    SITC 3222 in 1980, 3221 from 1981 to 1987 and 3222 again from 1988). ``exclude`` lists
    (unit, cmd, year) whose HS weights failed the monthly cross-check; their tonnes count as not measured.
    ``dominated`` lists series-years with a code whose destinations are mostly undisclosed (see
    :func:`allocation_dominated`); they are not measured either.
    """
    m = sea[["unit", "cls", "cmd", "commodity", "year", "tonnes", "measured_tonnes"]].copy()
    if exclude:
        bad = [
            c == "HS" and (str(u), str(k), int(y)) in exclude
            for u, c, k, y in zip(m["unit"], m["cls"], m["cmd"], m["year"], strict=True)
        ]
        m.loc[bad, "measured_tonnes"] = 0.0
    g = m.groupby(["unit", "cls", "commodity", "year"])[["tonnes", "measured_tonnes"]].sum()
    g = g[(g["tonnes"] > 0) & (g["measured_tonnes"] >= MEASURED_SHARE * g["tonnes"])]
    out = {(str(u), str(c), str(k), int(y)) for u, c, k, y in g.index}
    return out - (dominated or set())


# ---------------------------------------------------------------- route set and distances
@dataclass(frozen=True)
class RouteSpec:
    """A route's loading and discharge blends."""

    route_id: str
    origin: str
    destination: str
    commodity: str
    load: tuple[tuple[str, float], ...]
    disc: tuple[tuple[str, float], ...]


def classify_partner(
    origin: str, destination: str | None, port_countries: set[str], commodity: str | None = None
) -> str:
    """Why a flow is kept or left out of the sea routes: sea, own, land, no_port or unspecified."""
    if destination is None or destination == "nan":
        return UNDISCLOSED
    if destination in TRADING_HUBS:
        hub_for = TRADING_HUBS[destination]
        if hub_for is None or commodity in hub_for:
            return UNDISCLOSED
    if destination == origin:
        return "own"
    if destination in LAND_NEIGHBOURS_LONG.get(origin, frozenset()):
        return "land"
    if destination not in port_countries and PORT_PROXY_COUNTRY.get(destination) not in port_countries:
        return "no_port"
    return "sea"


def route_specs(
    routes: pd.DataFrame, ports_db: pd.DataFrame, named: dict[str, Port]
) -> tuple[list[RouteSpec], dict[str, Port]]:
    """Loading and discharge blends for every sea route: named ports where defined, else PortWatch ports."""
    ports: dict[str, Port] = dict(named)
    specs = []
    for r in routes[["origin", "destination", "commodity", "unit"]].drop_duplicates().to_dict(orient="records"):
        o, d, c = str(r["origin"]), str(r["destination"]), str(r["commodity"])
        key = IMPORTER_LOAD_KEY.get((d, o), LOAD_KEY[c]) if str(r["unit"]).endswith("_M") else LOAD_KEY[c]
        if (o, key) in LOADING or (o, key) in LOADING_BY_DESTINATION:
            load = loading_blend(o, key, d)
        else:
            coast = ("west" if d in PACIFIC_DESTINATIONS else "east") if o in TWO_COAST_COUNTRIES else None
            lp, load = portwatch_blend(ports_db, o, coast)
            ports.update({p.port_id: p for p in lp})
        if (d, c) in DISCHARGE:
            disc = DISCHARGE[(d, c)]
        else:
            coast = ("west" if o in PACIFIC_ORIGINS else "east") if d in TWO_COAST_COUNTRIES else None
            dp, disc = portwatch_blend(ports_db, PORT_PROXY_COUNTRY.get(d, d), coast)
            ports.update({p.port_id: p for p in dp})
        specs.append(RouteSpec(f"{o}-{d}-{c}", o, d, c, tuple(load), tuple(disc)))
    return specs, ports


class DistanceCache:
    """Port-pair sea distances kept on disk, so a rebuild only routes new port pairs."""

    def __init__(self, path: Any) -> None:
        """Load the cached distances from ``path`` if it exists."""
        self.path = path
        self.table: dict[tuple[float, float, float, float, str], float] = {}
        if path.exists():
            df = pd.read_csv(path)
            for r in df.to_dict(orient="records"):
                key = (float(r["o_lon"]), float(r["o_lat"]), float(r["d_lon"]), float(r["d_lat"]), str(r["variant"]))
                self.table[key] = float(r["distance_nm"])
        self.new = 0

    @staticmethod
    def _key(o: Port, d: Port, variant: str) -> tuple[float, float, float, float, str]:
        return (round(o.lon, 4), round(o.lat, 4), round(d.lon, 4), round(d.lat, 4), variant)

    def get(self, o: Port, d: Port, variant: str) -> float:
        """Distance in nautical miles, routed and cached on first use (NaN if the variant is infeasible)."""
        k = self._key(o, d, variant)
        if k not in self.table:
            try:
                self.table[k] = sea_distance_nm(o, d, variant)[0]
            except Exception as exc:  # noqa: BLE001 - an infeasible variant is recorded as missing
                log.warning("no %s route %s-%s: %s", variant, o.port_id, d.port_id, exc)
                self.table[k] = float("nan")
            self.new += 1
        return self.table[k]

    def save(self) -> None:
        """Write the cache back to disk."""
        rows = [dict(zip(("o_lon", "o_lat", "d_lon", "d_lat", "variant"), k, strict=True)) | {"distance_nm": v}
                for k, v in sorted(self.table.items())]  # fmt: skip
        pd.DataFrame(rows).to_csv(self.path, index=False)


def route_distances(specs: list[RouteSpec], ports: dict[str, Port], cache: DistanceCache) -> pd.DataFrame:
    """Baseline distance per route (the variant used by the monthly model for the commodity and origin)."""
    rows = []
    for s in specs:
        variant = baseline_variant(s.route_id)
        total, weight = 0.0, 0.0
        for lp, lw in s.load:
            for dp, dw in s.disc:
                d = cache.get(ports[lp], ports[dp], variant)
                if np.isfinite(d):
                    total += lw * dw * d
                    weight += lw * dw
        rows.append(
            {
                "route_id": s.route_id,
                "origin": s.origin,
                "destination": s.destination,
                "commodity": s.commodity,
                "variant": variant,
                "distance_nm": total / weight if weight > 0 else float("nan"),
                "named_ports": all(not p.startswith("PW_") for p, _ in (*s.load, *s.disc)),
            }
        )
    return pd.DataFrame(rows)


def diversion_ratios() -> pd.DataFrame:
    """Annual mean of monthly distance over baseline per route, from the monthly model (Red Sea, Panama)."""
    path = PROCESSED_DIR / "route_distance_monthly.csv"
    if not path.exists():
        return pd.DataFrame(columns=["route_id", "year", "ratio"])
    m = pd.read_csv(path)
    m["year"] = m["month"].astype(str).str[:4].astype(int)
    m["ratio"] = m["distance_nm"] / m["baseline_nm"]
    return pd.DataFrame(m.groupby(["route_id", "year"], as_index=False)["ratio"].mean())


# ---------------------------------------------------------------- chain-linking
def chain_link(sea: pd.DataFrame, measured: set[tuple[str, str, str, int]], first: int, last: int) -> pd.DataFrame:
    """Chain-linked tonnes and tonne-miles, each year's change measured on series measured in both years.

    ``sea`` has unit, cls, commodity, year, tonnes and tonne_miles per route. A series is one unit and
    commodity group in one classification (see ``measured_series``). For each pair (t-1, t) and unit, HS
    is used if at least one of the unit's HS series is measured in both years, else SITC Revision 2 on the
    same rule, else the unit is left out of that pair; within the chosen classification only series
    measured (and positive) in both years count.
    Reporters that enter, leave or change classification therefore never move the chain, and estimated
    tonnes move it only within a measured series (at most 1 - MEASURED_SHARE of its tonnes). When a series'
    share of tonnes allocated from undisclosed destinations moves by more than MAX_ALLOCATION_SHIFT between
    the two years, its haul is held at the earlier year's value for that change: its tonnes count, but the
    allocation does not move the haul (Australian iron ore under "Special categories" from 1988 to 1993,
    mostly bound for Europe, would otherwise add a false 10 percent haul step in 1994). ``held`` lists those
    series. ``chain_share``
    is the share of year t's sea tonnes (each unit in the classification it uses, else HS where present)
    that the change to year t rests on. Levels are scaled so that the last year equals that year's covered
    total (HS, all series).
    """
    cols = ["tonnes", "tonne_miles", "allocated_tonnes"]
    agg = (
        sea.assign(allocated_tonnes=sea["allocated_tonnes"] if "allocated_tonnes" in sea else 0.0)
        .groupby(["unit", "cls", "commodity", "year"], as_index=False)[cols]
        .sum()
    )
    totals: dict[tuple[str, str, str, int], tuple[float, float]] = {}
    alloc_share: dict[tuple[str, str, str, int], float] = {}
    for r in agg.to_dict(orient="records"):
        k4 = (str(r["unit"]), str(r["cls"]), str(r["commodity"]), int(r["year"]))
        totals[k4] = (float(r["tonnes"]), float(r["tonne_miles"]))
        alloc_share[k4] = float(r["allocated_tonnes"]) / float(r["tonnes"]) if float(r["tonnes"]) > 0 else 0.0
    series: dict[tuple[str, str], set[str]] = {}
    for u, c, k, _y in totals:
        series.setdefault((u, c), set()).add(k)
    units = sorted({u for u, _c in series})
    rows = [{"year": first, "tonnes_chain": 1.0, "tm_chain": 1.0, "units": "", "n_units": 0, "n_series": 0,
             "chain_share": np.nan, "held": ""}]  # fmt: skip
    t_idx, tm_idx = 1.0, 1.0
    for y in range(first + 1, last + 1):
        num_t = den_t = num_tm = den_tm = total_t = 0.0
        used, held, n_series = [], [], 0
        for u in units:
            cover = "HS" if any((u, "HS", k, y) in totals for k in series.get((u, "HS"), set())) else "S2"
            for cls in ("HS", "S2"):
                both = [
                    k
                    for k in sorted(series.get((u, cls), set()))
                    if (u, cls, k, y - 1) in measured and (u, cls, k, y) in measured
                ]
                if both:
                    cover = cls
                    break
            else:
                total_t += sum(
                    totals[(u, cover, k, y)][0] for k in series.get((u, cover), set()) if (u, cover, k, y) in totals
                )
                continue
            total_t += sum(totals[(u, cls, k, y)][0] for k in series.get((u, cls), set()) if (u, cls, k, y) in totals)
            for k in both:
                a, b = totals[(u, cls, k, y - 1)], totals[(u, cls, k, y)]
                if abs(alloc_share[(u, cls, k, y)] - alloc_share[(u, cls, k, y - 1)]) > MAX_ALLOCATION_SHIFT:
                    b = (b[0], b[0] * a[1] / a[0])
                    held.append(f"{u}:{cls}:{k}")
                den_t, den_tm, num_t, num_tm = den_t + a[0], den_tm + a[1], num_t + b[0], num_tm + b[1]
            used.append(f"{u}:{cls}")
            n_series += len(both)
        if den_t <= 0 or num_t <= 0:
            raise ValueError(f"no series measured in both {y - 1} and {y}")
        t_idx *= num_t / den_t
        tm_idx *= num_tm / den_tm
        rows.append({"year": y, "tonnes_chain": t_idx, "tm_chain": tm_idx, "units": " ".join(used),
                     "n_units": len(used), "n_series": n_series,
                     "chain_share": num_t / total_t if total_t > 0 else np.nan, "held": " ".join(held)})  # fmt: skip
    out = pd.DataFrame(rows)
    last_t = sea[(sea["year"] == last) & (sea["cls"] == "HS")]
    out["tonnes"] = out["tonnes_chain"] / out["tonnes_chain"].iloc[-1] * float(last_t["tonnes"].sum())
    out["tonne_miles"] = out["tm_chain"] / out["tm_chain"].iloc[-1] * float(last_t["tonne_miles"].sum())
    out["avg_haul_nm"] = out["tonne_miles"] / out["tonnes"]
    return out


# ---------------------------------------------------------------- freight index
def freight_annual(bdi_monthly: pd.Series, kilian_m: pd.Series, cpi: pd.Series) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Annual freight index: BDI annual mean from its first full year, calibrated Kilian proxy before.

    Returns the annual table (year, index, proxy flag, both series where available) and the calibration
    summary (coefficients and fit of annual changes on the overlap).
    """
    x = (kilian_m / 100.0 + np.log(cpi)).dropna()
    overlap = pd.concat([np.log(bdi_monthly).rename("lb"), x.rename("x")], axis=1).dropna()
    if len(overlap) < 60:
        raise ValueError("need at least five years where the BDI and the Kilian index overlap")
    tau = np.array([(p.year - 2000) * 12 + p.month - 1 for p in overlap.index], dtype=float)
    res = sm.OLS(overlap["lb"] - overlap["x"], sm.add_constant(tau)).fit()
    a, b = float(res.params.iloc[0]), float(res.params.iloc[1])
    tau_all = np.array([(p.year - 2000) * 12 + p.month - 1 for p in x.index], dtype=float)
    proxy_m = pd.Series(np.exp(a + b * tau_all + x.to_numpy()), index=x.index)
    log_proxy = pd.Series(a + b * tau_all + x.to_numpy(), index=x.index).reindex(overlap.index)
    bdi_years = pd.PeriodIndex(bdi_monthly.index).year
    first_full = min(y for y in sorted(set(bdi_years)) if (bdi_years == y).sum() == 12)
    bdi_y = bdi_monthly.groupby(bdi_years).agg(["mean", "count"])
    prox_y = proxy_m.groupby(pd.PeriodIndex(proxy_m.index).year).agg(["mean", "count"])
    years = sorted(set(prox_y.index[prox_y["count"] == 12]) | set(bdi_y.index[bdi_y["count"] == 12]))
    rows = []
    for y in years:
        has_bdi = y >= first_full and y in bdi_y.index and bdi_y.loc[y, "count"] == 12
        rows.append(
            {
                "year": y,
                "index": float(bdi_y.loc[y, "mean"]) if has_bdi else float(prox_y.loc[y, "mean"]),
                "proxy": not has_bdi,
                "bdi": float(bdi_y.loc[y, "mean"]) if has_bdi else None,
                "kilian_proxy": float(prox_y.loc[y, "mean"])
                if y in prox_y.index and prox_y.loc[y, "count"] == 12
                else None,
            }
        )
    out = pd.DataFrame(rows)
    # calendar months between the first and last overlap month (a month missing from one input is a gap)
    full = pd.period_range(overlap.index[0], overlap.index[-1], freq="M")
    span = pd.period_range(pd.PeriodIndex(bdi_monthly.index).min(), pd.PeriodIndex(bdi_monthly.index).max(), freq="M")
    ov = out.dropna(subset=["bdi", "kilian_proxy"]).set_index("year")
    d = ov[["bdi", "kilian_proxy"]].astype(float).apply(np.log).diff().dropna()
    calib = {
        "intercept": a,
        "trend_per_month": b,
        "trend_per_year": 12 * b,
        "overlap_months": len(overlap),
        "overlap_window": [str(overlap.index[0]), str(overlap.index[-1])],
        "monthly_level_corr": float(np.corrcoef(overlap["lb"], log_proxy)[0, 1]),
        "monthly_change12_corr": _corr(overlap["lb"].reindex(full).diff(12), log_proxy.reindex(full).diff(12)),
        "annual_change_corr": float(np.corrcoef(d["bdi"], d["kilian_proxy"])[0, 1]) if len(d) > 2 else None,
        "annual_change_mean_abs_error": float((d["bdi"] - d["kilian_proxy"]).abs().mean()) if len(d) else None,
        "annual_change_max_abs_error": float((d["bdi"] - d["kilian_proxy"]).abs().max()) if len(d) else None,
        "annual_level_mean_abs_log_error": float(
            np.mean(np.abs(np.log(ov["bdi"].astype(float) / ov["kilian_proxy"].astype(float))))
        ),
        "first_bdi_year": int(first_full),
        "cpi_missing_months": [str(m) for m in span.difference(pd.PeriodIndex(cpi.dropna().index))],
        "kilian_missing_months": [str(m) for m in span.difference(pd.PeriodIndex(kilian_m.dropna().index))],
    }
    return out, calib


def _corr(a: pd.Series, b: pd.Series) -> float:
    """Correlation over the observations where both series exist."""
    df = pd.DataFrame({"a": a, "b": b}).dropna()
    return float(np.corrcoef(df["a"].to_numpy(dtype=float), df["b"].to_numpy(dtype=float))[0, 1])


# ---------------------------------------------------------------- fleet, supply, prices
def fleet_annual(fleet: pd.DataFrame) -> pd.Series:
    """Calendar-year average fleet (dwt): mean of 1 January of the year and of the next year."""
    f = fleet.set_index("year")["fleet_dwt_thousand"].astype(float) * 1000.0
    avg = (f + f.shift(-1)) / 2.0
    return avg.dropna().rename("fleet")


def supply_annual(sf_monthly: pd.DataFrame) -> pd.Series:
    """Annual mean of the monthly effective supply factor (one before PortWatch coverage)."""
    s = sf_monthly.copy()
    s["year"] = s["month"].astype(str).str[:4].astype(int)
    g = s.groupby("year")["supply_factor"].agg(["mean", "count"])
    return g.loc[g["count"] == 12, "mean"].rename("supply_factor")


def prices_annual(prices_m: pd.DataFrame) -> pd.DataFrame:
    """Annual Brent (calendar year) and iron ore (twelve months to September, the three-month lead)."""
    p = prices_m.copy()
    oil = p["brent_usd_bbl"].groupby(pd.PeriodIndex(p.index).year).agg(["mean", "count"])
    ore_shift = p["iron_ore_usd_dmtu"].copy()
    ore_shift.index = ore_shift.index + 3  # October t-1 to September t is labelled year t
    ore = ore_shift.groupby(pd.PeriodIndex(ore_shift.index).year).agg(["mean", "count"])
    out = pd.DataFrame(
        {
            "brent_usd_bbl": oil.loc[oil["count"] == 12, "mean"],
            "iron_ore_usd_dmtu_lead3": ore.loc[ore["count"] == 12, "mean"],
        }
    )
    out.index.name = "year"
    return out


# ---------------------------------------------------------------- driver
def run() -> dict[str, Any]:
    """Build the annual long-history panel and its supporting tables; returns a coverage summary."""
    records = comtrade_long.load_records()
    flows = annual_flows(records, partner_iso(), benchmark_prices(worldbank.load()))
    try:
        mismatch = monthly_mismatch(comtrade.load_records(), records)
    except FileNotFoundError:
        mismatch = set()
    ports_db = portwatch.load_ports()
    port_countries = set(ports_db.loc[ports_db["vessel_count_dry_bulk"] > 0, "ISO3"].astype(str))
    flows["route_class"] = [
        "world" if w else classify_partner(str(o), None if pd.isna(d) else str(d), port_countries, str(c))
        for o, d, w, c in zip(flows["origin"], flows["destination"], flows["is_world"], flows["commodity"], strict=True)
    ]
    sea = allocate_undisclosed(flows)
    sea["route_id"] = sea["origin"].astype(str) + "-" + sea["destination"].astype(str) + "-" + sea["commodity"]
    named = resolve_ports(wpi.load())
    specs, ports = route_specs(sea, ports_db, named)
    cache = DistanceCache(PROCESSED_DIR / "long_port_distances.csv")
    dist = route_distances(specs, ports, cache)
    cache.save()
    log.info("long history: %d routes, %d new port-pair distances", len(dist), cache.new)
    sea = sea.merge(dist[["route_id", "distance_nm"]], on="route_id", how="left")
    sea = sea[sea["distance_nm"].notna()]
    div = diversion_ratios()
    sea = sea.merge(div, on=["route_id", "year"], how="left")
    sea["ratio"] = sea["ratio"].fillna(1.0)
    sea["tonne_miles"] = sea["tonnes"] * sea["distance_nm"] * sea["ratio"]
    dominated = allocation_dominated(flows)
    measured = measured_series(sea, mismatch, dominated)
    sea["measured_series"] = [
        (u, c, k, y) in measured
        for u, c, k, y in zip(sea["unit"], sea["cls"], sea["commodity"], sea["year"], strict=True)
    ]
    freight_m = pd.read_csv(PROCESSED_DIR / "freight_monthly.csv")
    bdi_m = pd.Series(freight_m["bdi"].to_numpy(), index=pd.PeriodIndex(freight_m["month"], freq="M"))
    freight, calib = freight_annual(bdi_m, kilian.load(), oecd_cpi.load())
    fleet = fleet_annual(pd.read_csv(PROCESSED_DIR / "fleet_annual.csv"))
    sf = supply_annual(pd.read_csv(PROCESSED_DIR / "supply_factor_monthly.csv"))
    first = max(FIRST_YEAR, int(fleet.index.min()), int(freight["year"].min()))
    last = min(int(sea["year"].max()), int(fleet.index.max()), int(freight["year"].max()))
    chain = chain_link(sea, measured, first, last)
    panel = (
        chain.merge(freight[["year", "index", "proxy"]], on="year")
        .merge(fleet.reset_index(), on="year")
        .merge(sf.reset_index(), on="year", how="left")
    )
    panel["supply_factor"] = panel["supply_factor"].fillna(1.0)
    panel.to_csv(PROCESSED_DIR / "long_panel_annual.csv", index=False)
    freight.to_csv(PROCESSED_DIR / "long_freight_annual.csv", index=False)
    dist.to_csv(PROCESSED_DIR / "long_route_distances.csv", index=False)
    prices_annual(worldbank.load()).to_csv(PROCESSED_DIR / "long_prices_annual.csv")
    cov = coverage_table(flows, sea)
    cov.to_csv(PROCESSED_DIR / "long_coverage_annual.csv", index=False)
    sea_out = sea.groupby(["unit", "cls", "cmd", "year", "route_id", "commodity", "measured_series"], as_index=False)[
        ["tonnes", "measured_tonnes", "proxy_tonnes", "allocated_tonnes", "tonne_miles"]
    ].sum()
    sea_out.to_csv(PROCESSED_DIR / "long_route_flows_annual.csv", index=False)
    summary = {
        "window": [int(panel["year"].min()), int(panel["year"].max())],
        "years": len(panel),
        "routes": int(dist["route_id"].nunique()),
        "routes_named_ports": int(dist["named_ports"].sum()),
        "proxy_years": [int(y) for y in panel.loc[panel["proxy"], "year"]],
        "freight_calibration": calib,
        "units": sorted({k[0] for k in measured}),
        "monthly_mismatch": sorted(f"{u} {c} {y}" for u, c, y in mismatch),
        "allocation_dominated": sorted(f"{u} {c} {k} {y}" for u, c, k, y in dominated),
        "series_years": len(
            {(u, c, k, y) for u, c, k, y in zip(sea["unit"], sea["cls"], sea["commodity"], sea["year"], strict=True)}
        ),
        "series_years_measured": len(measured),
    }
    (PROCESSED_DIR / "long_coverage.json").write_text(json.dumps(summary, indent=2, default=str))
    log.info("long history panel: %s to %s", panel["year"].min(), panel["year"].max())
    return summary


def coverage_table(flows: pd.DataFrame, sea: pd.DataFrame) -> pd.DataFrame:
    """Per exporter unit, classification and year: World total, sea-route tonnes and the excluded shares.

    Undisclosed tonnes split, per commodity code as in :func:`allocate_undisclosed`, into the part
    allocated to sea routes (``sea_allocated``), the part allocated to land neighbours, countries without
    a port and the reporter itself (``undisclosed_to_other``, left out with them) and the part of codes
    with no disclosed destination (``undisclosed_dropped``).
    """
    key = ["unit", "cls", "year"]
    x = flows[flows["unit"].str.endswith("_X")]
    nw = x[~x["is_world"]]
    world = x[x["is_world"]].groupby(key)["tonnes"].sum().rename("world_tonnes")
    parts = nw.pivot_table(index=key, columns="route_class", values="tonnes", aggfunc="sum").fillna(0.0)
    by_cmd = nw.pivot_table(index=[*key, "cmd"], columns="route_class", values="tonnes", aggfunc="sum").fillna(0.0)
    for c in ("sea", "land", "no_port", "own", UNDISCLOSED):
        for t in (parts, by_cmd):
            if c not in t.columns:
                t[c] = 0.0
    disclosed = by_cmd[["sea", "land", "no_port", "own"]].sum(axis=1)
    undisclosed = by_cmd[UNDISCLOSED]
    split = (
        pd.DataFrame(
            {
                "undisclosed_to_other": (undisclosed * (disclosed - by_cmd["sea"]) / disclosed).where(
                    disclosed > 0, 0.0
                ),
                "undisclosed_dropped": undisclosed.where(disclosed <= 0, 0.0),
            }
        )
        .groupby(level=key)
        .sum()
    )
    proxy = nw.groupby(key)["proxy_tonnes"].sum().rename("proxy_tonnes")
    sx = sea[sea["unit"].str.endswith("_X")]
    meas = sx[sx["measured_series"]].groupby(key)["tonnes"].sum()
    alloc = sx.groupby(key)["allocated_tonnes"].sum()
    out = (
        parts.join(world, how="outer")
        .join(proxy, how="left")
        .join(alloc.rename("sea_allocated"), how="left")
        .join(split, how="left")
        .join(meas.rename("sea_in_measured_series"), how="left")
        .fillna(0.0)
        .reset_index()
    )
    out["sea_with_allocation"] = out["sea"] + out["sea_allocated"]
    out["sea_share_of_world"] = out["sea_with_allocation"] / out["world_tonnes"].where(out["world_tonnes"] > 0)
    return out
