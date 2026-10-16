"""Extensions tested against the headline model: more cargo data and more market signals.

Each variant is scored with the protocol of the headline model, so the
numbers are comparable:

* in sample, the unexplained share of a typical twelve-month move (mean
  absolute residual over mean absolute move), with the physical forces alone
  and with the market signals;
* out of sample, the expanding-window test of :func:`tmdm.model.signals.out_of_sample`
  (every coefficient refit with a twelve-month gap).

Variants:

* ``minor_bulks``: tonne-miles of the minor bulks (sugar, soybean meal,
  cement, manganese ore, bauxite, petroleum coke, potash, wood chips and
  pellets, ferrous scrap) added to the panel, headline signals unchanged;
* ``cli_<area>_lead<k>``: an OECD composite leading indicator, over the window
  ending ``k`` months earlier, added as a third signal;
* ``cli_china_instead_of_ore_lead<k>``: the China leading indicator in place
  of the iron ore price.

The headline specification is fixed in the code. A variant is flagged as
clearing the adoption margin if it improves the out-of-sample R squared by
more than :data:`ADOPTION_MARGIN`, a margin meant to cover the choice among
several leads; a flagged variant is a candidate for review, not switched in
automatically.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

import pandas as pd

from tmdm.model.decomposition import SERIES, fit_elasticity, headline_elasticity
from tmdm.model.signals import DEFAULT_SIGNALS, Signal, build_windows, fit_signals, out_of_sample

log = logging.getLogger(__name__)

ADOPTION_MARGIN = 0.05
CLI_LEADS = (0, 3, 6)
CLI_AREAS = {"china": ("cli_chn", "China"), "g20": ("cli_g20", "G20")}


def evaluate(
    panel: pd.DataFrame,
    prices: pd.DataFrame,
    signals: Sequence[Signal],
    horizon: int = 12,
    window: int = 3,
) -> dict[str, Any]:
    """Score one variant: elasticity, signal fit and out-of-sample comparison."""
    p = panel[list(SERIES)]
    head, _rob = fit_elasticity(p)
    w = build_windows(p, prices, head.elasticity, signals, horizon, window)
    fit = fit_signals(w, signals)
    oos = out_of_sample(p, prices, headline_elasticity, signals, horizon, window)
    return {
        "panel_window": [str(p.index[0]), str(p.index[-1])],
        "elasticity": head.elasticity,
        "elasticity_se": head.std_error,
        "r_squared_balance": head.r_squared,
        "signals": [s.__dict__ for s in signals],
        "coefficients": fit.coefficients,
        "t_stats": fit.t_stats,
        "n_obs": fit.n_obs,
        "unexplained_physical": fit.unexplained_physical,
        "unexplained_extended": fit.unexplained_extended,
        "oos_available": bool(oos.get("available")),
        "oos_months": oos.get("n_months"),
        "oos_r2_physical": oos["r2_vs_zero"]["physical"] if oos.get("available") else None,
        "oos_r2_extended": oos["r2_vs_zero"]["extended"] if oos.get("available") else None,
        "oos_unexplained_physical": oos["unexplained"]["physical"] if oos.get("available") else None,
        "oos_unexplained_extended": oos["unexplained"]["extended"] if oos.get("available") else None,
    }


def with_minor_bulks(panel: pd.DataFrame, minor: pd.DataFrame) -> pd.DataFrame:
    """Panel with minor-bulk tonnes and tonne-miles added, over the months both cover."""
    m = minor.set_index("month")[["tonnes", "tonne_miles"]]
    common = panel.index.intersection(m.index)
    out = panel.loc[common].copy()
    out["tonnes"] = out["tonnes"] + m.loc[common, "tonnes"]
    out["tonne_miles"] = out["tonne_miles"] + m.loc[common, "tonne_miles"]
    return out


def variants(prices: pd.DataFrame, minor: pd.DataFrame | None) -> list[dict[str, Any]]:
    """The list of variants to score (label, data, signals); headline first."""
    oil, ore = DEFAULT_SIGNALS
    out: list[dict[str, Any]] = [
        {"id": "headline", "label": "Headline: major bulks, oil and iron ore", "minor": False, "signals": (oil, ore)}
    ]
    if minor is not None and not minor.empty:
        out.append(
            {"id": "minor_bulks", "label": "Minor bulks added to tonne-miles", "minor": True, "signals": (oil, ore)}
        )
    for key, (col, name) in CLI_AREAS.items():
        if col not in prices.columns:
            continue
        for k in CLI_LEADS:
            sig = Signal(f"cli_{key}", col, k, f"{name} leading indicator, {k} months earlier")
            out.append(
                {
                    "id": f"cli_{key}_lead{k}",
                    "label": f"{name} leading indicator added, {k} months earlier",
                    "minor": False,
                    "signals": (oil, ore, sig),
                }
            )
    if "cli_chn" in prices.columns:
        for k in CLI_LEADS:
            sig = Signal("cli_china", "cli_chn", k, f"China leading indicator, {k} months earlier")
            out.append(
                {
                    "id": f"cli_china_instead_of_ore_lead{k}",
                    "label": f"China leading indicator instead of iron ore, {k} months earlier",
                    "minor": False,
                    "signals": (oil, sig),
                }
            )
    return out


def run_extensions(
    panel: pd.DataFrame, prices: pd.DataFrame, minor: pd.DataFrame | None, horizon: int = 12, window: int = 3
) -> dict[str, Any]:
    """Score every variant against the headline and flag those that clear the adoption margin."""
    rows = []
    base: dict[str, Any] | None = None
    for v in variants(prices, minor):
        p = with_minor_bulks(panel, minor) if v["minor"] and minor is not None else panel
        try:
            res = evaluate(p, prices, v["signals"], horizon, window)
        except ValueError as exc:
            log.warning("extension %s not scored: %s", v["id"], exc)
            continue
        res.update({"id": v["id"], "label": v["label"]})
        if v["minor"] and minor is not None:
            m = minor.set_index("month").reindex(p.index)
            share = m["tonne_miles"] / panel.loc[p.index, "tonne_miles"]
            res["minor_tonne_miles_share"] = float(share.mean())
            res["minor_tonne_miles_share_range"] = [float(share.min()), float(share.max())]
            res["minor_tonnes_share"] = float((m["tonnes"] / panel.loc[p.index, "tonnes"]).mean())
        if base is None:
            base = res
        if res is not base and base["oos_r2_extended"] is not None and res["oos_r2_extended"] is not None:
            res["oos_r2_gain"] = res["oos_r2_extended"] - base["oos_r2_extended"]
        else:
            res["oos_r2_gain"] = None
        res["clears_margin"] = (
            v["id"] != "headline" and res["oos_r2_gain"] is not None and res["oos_r2_gain"] > ADOPTION_MARGIN
        )
        rows.append(res)
        log.info(
            "extension %s: unexplained %.3f in sample, out-of-sample R2 %s",
            v["id"],
            res["unexplained_extended"],
            None if res["oos_r2_extended"] is None else round(res["oos_r2_extended"], 3),
        )
    return {"adoption_margin": ADOPTION_MARGIN, "variants": rows}
