"""UNCTADstat maritime datasets (bulk download, 7z compressed CSV).

Datasets used:

* ``US.MerchantFleet``: merchant fleet by flag of registration and type of
  ship, as of 1 January, in thousand deadweight tons (1980 onward). The
  World total for bulk carriers is the fleet supply series.
* ``US.ShipBuilding`` and ``US.ShipScrapping``: ships built and ships
  scrapped by economy, gross tonnage, all ship types (2014 onward). Used as
  context for fleet renewal, not as bulk-specific flows.
* ``US.PortCalls`` (annual) and ``US.PortCalls_S`` (semiannual): port calls
  and performance statistics, including the median time in port by commercial
  market (ship type), from 2018. Dry bulk median port time is the congestion
  proxy. ``US.PortCallsArrivals`` gives the number of port calls.
* ``US.SeaborneTrade``: world seaborne trade by cargo type in thousand
  metric tons (2000 onward), used to express the covered routes as a share
  of world dry cargo.
"""

from __future__ import annotations

import io
import logging
import tempfile
from pathlib import Path

import pandas as pd
import py7zr

from tmdm.http import get
from tmdm.io import DatasetMetadata, raw_dir, utc_now_iso, write_metadata

log = logging.getLogger(__name__)

DATASET_ID = "unctadstat_maritime"
BULK = "https://unctadstat-api.unctad.org/bulkdownload/{ds}/{name}"
PORTAL = "https://unctadstat.unctad.org/datacentre/"
DATASETS = {
    "US.MerchantFleet": "US_MerchantFleet.csv",
    "US.ShipBuilding": "US_ShipBuilding.csv",
    "US.ShipScrapping": "US_ShipScrapping.csv",
    "US.PortCalls": "US_PortCalls.csv",
    "US.SeaborneTrade": "US_SeaborneTrade.csv",
    "US.PortCalls_S": "US_PortCalls_S.csv",
    "US.PortCallsArrivals": "US_PortCallsArrivals.csv",
}


def fetch() -> None:
    """Download every dataset archive, extract the CSV and write metadata."""
    d = raw_dir(DATASET_ID)
    coverage = {}
    for ds, csv_name in DATASETS.items():
        url = BULK.format(ds=ds, name=csv_name.removesuffix(".csv"))
        r = get(url, timeout=300)
        with tempfile.TemporaryDirectory() as tmp, py7zr.SevenZipFile(io.BytesIO(r.content), mode="r") as z:
            names = z.getnames()
            if csv_name not in names:
                raise RuntimeError(f"unctad {ds}: expected {csv_name} in archive, found {names}")
            z.extractall(path=tmp)
            content = (Path(tmp) / csv_name).read_bytes()
        (d / csv_name).write_bytes(content)
        head = pd.read_csv(io.BytesIO(content), dtype=str)
        period_col = "Year" if "Year" in head.columns else "Period"
        periods = head[period_col].dropna().astype(str)
        coverage[ds] = f"{periods.min()} to {periods.max()}"
        log.info("unctad %s: %d bytes, %s", ds, len(content), coverage[ds])
    meta = DatasetMetadata(
        dataset_id=DATASET_ID,
        title="UNCTADstat maritime transport statistics: merchant fleet, ships built and scrapped, port calls, "
        "seaborne trade",
        publisher="United Nations Conference on Trade and Development (UNCTAD), UNCTADstat Data Centre",
        source_url=PORTAL,
        licence="UNCTADstat data may be freely used with attribution to UNCTAD (UNCTADstat terms of use)",
        frequency="annual",
        coverage="; ".join(f"{k}: {v}" for k, v in coverage.items()),
        units="thousand deadweight tons (fleet, 1 January), gross tons (ships built and scrapped), "
        "days (median time in port), "
        "thousand metric tons (seaborne trade)",
        retrieved_at=utc_now_iso(),
        files=list(DATASETS.values()),
        limitations=[
            "Fleet figures are stocks as of 1 January, compiled from Clarksons Research data for ships of 100 GT "
            "and above.",
            "Ships built and scrapped are not split by ship type in the public dataset; they describe the whole fleet.",
            "Median time in port is an annual median across all port calls in the ship type and may hide "
            "within-year swings.",
            "Seaborne trade dry cargo includes containerised and minor bulk cargo, not only the major bulks.",
        ],
        extra={
            "bulk_download_urls": {ds: BULK.format(ds=ds, name=n.removesuffix(".csv")) for ds, n in DATASETS.items()}
        },
    )
    write_metadata(meta)


def _read(name: str) -> pd.DataFrame:
    return pd.read_csv(
        raw_dir(DATASET_ID) / name, dtype={"Economy": str, "ShipType": str, "CommercialMarket": str, "CargoType": str}
    )


def world_bulk_fleet() -> pd.DataFrame:
    """World bulk carrier fleet by year: dwt (thousand), number of ships, average age."""
    df = _read("US_MerchantFleet.csv")
    w = df[(df["Economy Label"] == "World") & (df["ShipType Label"] == "Bulk carriers")].copy()
    out = pd.DataFrame(
        {
            "year": w["Year"].astype(int),
            "fleet_dwt_thousand": pd.to_numeric(w["Dead weight tons in thousands"], errors="coerce"),
            "number_of_ships": pd.to_numeric(w["Number of ships"], errors="coerce"),
            "gross_tonnage_thousand": pd.to_numeric(w["Gross Tonnage in thousands"], errors="coerce"),
            "average_age_years": pd.to_numeric(w["Average age of vessels (years)"], errors="coerce"),
        }
    ).sort_values("year")
    total = df[(df["Economy Label"] == "World") & (df["ShipType Label"] == "Total fleet")]
    tot = pd.DataFrame(
        {
            "year": total["Year"].astype(int),
            "total_fleet_dwt_thousand": pd.to_numeric(total["Dead weight tons in thousands"], errors="coerce"),
        }
    )
    return out.merge(tot, on="year", how="left").reset_index(drop=True)


def world_built_and_scrapped() -> pd.DataFrame:
    """World gross tonnage built and scrapped by year (all ship types)."""
    built = _read("US_ShipBuilding.csv")
    scrap = _read("US_ShipScrapping.csv")
    b = built[built["Economy Label"] == "World"][["Year", "Gross Tonnage"]].rename(
        columns={"Year": "year", "Gross Tonnage": "built_gt"}
    )
    s = scrap[scrap["Economy Label"] == "World"][["Year", "Gross Tonnage"]].rename(
        columns={"Year": "year", "Gross Tonnage": "scrapped_gt"}
    )
    out = b.merge(s, on="year", how="outer").sort_values("year")
    out["year"] = out["year"].astype(int)
    for c in ("built_gt", "scrapped_gt"):
        out[c] = pd.to_numeric(out[c], errors="coerce")
    return out.reset_index(drop=True)


def world_port_time(market_label_contains: str = "dry bulk") -> pd.DataFrame:
    """World median time in port (days) for a commercial market (ship type), by year."""
    df = _read("US_PortCalls.csv")
    mask = (df["Economy Label"] == "World") & df["CommercialMarket Label"].str.contains(
        market_label_contains, case=False, na=False
    )
    w = df[mask]
    cols = {"Year": "year", "CommercialMarket Label": "market", "Median time in port (days)": "median_port_days"}
    extra = [c for c in df.columns if c.startswith("Number of") and "Footnote" not in c and "Missing" not in c]
    for c in extra:
        cols[c] = c.lower().replace(" ", "_").replace("(", "").replace(")", "")
    out = w[list(cols)].rename(columns=cols).copy()
    out["year"] = out["year"].astype(int)
    out["median_port_days"] = pd.to_numeric(out["median_port_days"], errors="coerce")
    return out.sort_values("year").reset_index(drop=True)


def world_seaborne_trade() -> pd.DataFrame:
    """World seaborne trade loaded by cargo type, thousand metric tons, by year."""
    df = _read("US_SeaborneTrade.csv")
    w = df[(df["Economy Label"] == "World") & df["CargoType Label"].str.contains("loaded", case=False, na=False)]
    out = w.pivot_table(index="Year", columns="CargoType Label", values="Metric tons in thousands", aggfunc="first")
    out.index = out.index.astype(int)
    out.index.name = "year"
    out.columns = [c.lower().replace(" ", "_") for c in out.columns]
    return out.reset_index()
