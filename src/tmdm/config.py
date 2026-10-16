"""Project paths and shared constants.

All file locations used by the pipeline are defined here so that every stage
reads and writes to the same, documented places.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(os.environ.get("TMDM_ROOT", Path(__file__).resolve().parents[2]))
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
REFERENCE_DIR = DATA_DIR / "reference"
SITE_DIR = ROOT / "site"
SITE_DATA_DIR = SITE_DIR / "data"
DOCS_DIR = ROOT / "docs"
FIGURES_DIR = DOCS_DIR / "figures"

# Monthly analysis window. The start is the first month for which monthly
# bilateral trade data is available for the main reporters in UN Comtrade.
MONTHLY_START = "2010-01"

# Fixed random seed for any stochastic step (bootstrap confidence intervals).
RANDOM_SEED = 20261016

USER_AGENT = "tonne-mile-demand-model/0.1 (+https://github.com/nathancouturier/Tonne-Mile-Demand-Model)"


def ensure_dirs() -> None:
    """Create every output directory used by the pipeline."""
    for d in (RAW_DIR, PROCESSED_DIR, REFERENCE_DIR, SITE_DATA_DIR, FIGURES_DIR):
        d.mkdir(parents=True, exist_ok=True)
