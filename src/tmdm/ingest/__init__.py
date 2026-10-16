"""Ingest stage: download raw data from every public source and write metadata.

Each source is independent. A failure in one source is logged and the others
still run, so that a transient outage never blocks the whole refresh; the
stage then exits with an error so the workflow run is marked as failed while
the data that did arrive is still committed.

Environment variables ``TMDM_INGEST_ONLY`` and ``TMDM_INGEST_SKIP`` take
comma-separated source names to restrict a run (used to split the long UN
Comtrade download from the fast sources in the GitHub Actions workflow).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable

from tmdm.config import MONTHLY_START

log = logging.getLogger(__name__)


def _ingest_comtrade() -> None:
    from tmdm.ingest import comtrade
    from tmdm.reference import comtrade_call_groups

    groups = [comtrade.CallGroup(*g) for g in comtrade_call_groups()]
    comtrade.fetch(groups, MONTHLY_START, refresh_months=int(os.environ.get("TMDM_COMTRADE_REFRESH_MONTHS", "15")))


def _ingest_portwatch() -> None:
    from tmdm.ingest import portwatch

    portwatch.fetch()


def _ingest_comexstat() -> None:
    from tmdm.ingest import comexstat

    comexstat.fetch()


def _ingest_unctad() -> None:
    from tmdm.ingest import unctad

    unctad.fetch()


def _ingest_wpi() -> None:
    from tmdm.ingest import wpi

    wpi.fetch()


def _ingest_oecd_cli() -> None:
    from tmdm.ingest import oecd_cli

    oecd_cli.fetch()


def _ingest_worldbank() -> None:
    from tmdm.ingest import worldbank

    worldbank.fetch()


def _ingest_asub() -> None:
    from tmdm.ingest import asub_bdi

    asub_bdi.fetch()


SOURCES: dict[str, Callable[[], None]] = {
    "asub_bdi": _ingest_asub,
    "unctad": _ingest_unctad,
    "wpi": _ingest_wpi,
    "worldbank": _ingest_worldbank,
    "oecd_cli": _ingest_oecd_cli,
    "comexstat": _ingest_comexstat,
    "portwatch": _ingest_portwatch,
    "comtrade": _ingest_comtrade,
}


def selected_sources() -> list[str]:
    """Resolve the source list from the environment."""
    only = [s for s in os.environ.get("TMDM_INGEST_ONLY", "").split(",") if s]
    skip = {s for s in os.environ.get("TMDM_INGEST_SKIP", "").split(",") if s}
    names = only or list(SOURCES)
    unknown = [n for n in names if n not in SOURCES] + [n for n in skip if n not in SOURCES]
    if unknown:
        raise ValueError(f"unknown ingest sources {unknown}; known: {list(SOURCES)}")
    return [n for n in names if n not in skip]


def run() -> None:
    """Run the selected sources and raise at the end if any failed."""
    failures: dict[str, str] = {}
    for name in selected_sources():
        log.info("ingest %s: start", name)
        try:
            SOURCES[name]()
            log.info("ingest %s: done", name)
        except Exception as exc:  # keep the other sources running
            log.exception("ingest %s: failed", name)
            failures[name] = f"{type(exc).__name__}: {exc}"
    if failures:
        raise RuntimeError(f"ingest failures: {failures}")
