"""World Port Index (NGA Publication 150): port coordinates.

The World Port Index is published by the US National Geospatial-Intelligence
Agency and lists the location and characteristics of several thousand ports.
It is a US government work in the public domain and is the coordinate source
for the loading and discharge ports used in the distance matrix.
"""

from __future__ import annotations

import logging

import pandas as pd

from tmdm.http import get
from tmdm.io import DatasetMetadata, raw_dir, utc_now_iso, write_metadata

log = logging.getLogger(__name__)

DATASET_ID = "nga_world_port_index"
URL = "https://msi.nga.mil/api/publications/download?key=16920959/SFH00000/UpdatedPub150.csv&type=download"
PAGE = "https://msi.nga.mil/Publications/WPI"
FILE = "UpdatedPub150.csv"
COLUMNS = {
    "World Port Index Number": "wpi_number",
    "Main Port Name": "port_name",
    "Alternate Port Name": "alt_name",
    "UN/LOCODE": "locode",
    "Country Code": "country",
    "Latitude": "lat",
    "Longitude": "lon",
    "Harbor Size": "harbor_size",
    "Harbor Type": "harbor_type",
    "Maximum Vessel Draft (m)": "max_draft_m",
}


def fetch() -> None:
    """Download the full WPI CSV and write metadata."""
    d = raw_dir(DATASET_ID)
    r = get(URL, timeout=300)
    (d / FILE).write_bytes(r.content)
    n = r.content.count(b"\n")
    meta = DatasetMetadata(
        dataset_id=DATASET_ID,
        title="World Port Index (Pub. 150), port locations and characteristics",
        publisher="US National Geospatial-Intelligence Agency (NGA), Maritime Safety Information",
        source_url=PAGE,
        licence="US government work, public domain",
        frequency="updated continuously; snapshot at retrieval",
        coverage=f"about {n} ports worldwide",
        units="decimal degrees (WGS84)",
        retrieved_at=utc_now_iso(),
        files=[FILE],
        limitations=[
            "A port coordinate is a single reference point; large export terminals can lie several miles from it."
        ],
        extra={"download_url": URL},
    )
    write_metadata(meta)
    log.info("wpi: saved %d bytes", len(r.content))


def load() -> pd.DataFrame:
    """Load the selected WPI columns."""
    df = pd.read_csv(raw_dir(DATASET_ID) / FILE, encoding="utf-8-sig", usecols=list(COLUMNS), low_memory=False)
    df = df.rename(columns=COLUMNS)
    df["port_name"] = df["port_name"].astype(str).str.strip()
    df["country"] = df["country"].astype(str).str.strip()
    return df
