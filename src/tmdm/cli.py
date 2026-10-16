"""Command line entry point: ``tmdm <stage>``.

Stages run in order: ingest, validate, transform, model, export, report.
``tmdm all`` runs every stage. Each stage is idempotent and reads only the
outputs of earlier stages, so a partial rerun is always safe.
"""

from __future__ import annotations

import argparse
import importlib
import logging
import sys
from collections.abc import Callable
from typing import cast

from tmdm.config import ensure_dirs

STAGES: dict[str, str] = {
    "ingest": "Download raw data from public sources and write metadata records",
    "validate": "Run data quality checks on the raw data and write a validation report",
    "transform": "Build distances, route tonne-miles, fleet and freight series",
    "model": "Estimate the decomposition, episodes and robustness checks",
    "export": "Write site JSON, CSV downloads and figures",
    "report": "Render the research note PDF and written deliverables",
}


def _runner(stage: str) -> Callable[[], None]:
    module = "tmdm.export.report" if stage == "report" else f"tmdm.{stage}"
    fn = importlib.import_module(module).run
    if not callable(fn):  # pragma: no cover - defensive
        raise TypeError(f"{module}.run is not callable")
    return cast(Callable[[], None], fn)


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run the requested stage(s)."""
    parser = argparse.ArgumentParser(prog="tmdm", description="Tonne-Mile Demand Model pipeline")
    parser.add_argument("stage", choices=[*STAGES, "all"], help="pipeline stage to run")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    ensure_dirs()
    stages = list(STAGES) if args.stage == "all" else [args.stage]
    for stage in stages:
        logging.getLogger("tmdm").info("stage %s: %s", stage, STAGES[stage])
        _runner(stage)()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
