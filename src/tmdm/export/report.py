"""Research note (PDF) and written deliverables generated from the pipeline outputs.

The note is three to five pages: question and answer, method, data, results
with real figures, robustness and limitations, reproducibility. Figures are
drawn with matplotlib from the processed files; text is rendered with
ReportLab using Liberation Serif. The same numbers feed the README summary,
the CV description and the interview talking points.
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from reportlab import rl_config
from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Image, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from tmdm.config import DOCS_DIR, FIGURES_DIR, PROCESSED_DIR, ROOT, SITE_DIR
from tmdm.io import all_metadata
from tmdm.model.attribution import log_points_to_pct
from tmdm.reference import ECONOMY_NAMES

log = logging.getLogger(__name__)

PDF_NAME = "Tonne-Mile-Demand-Model-Research-Note.pdf"
FONT_DIR = Path("/usr/share/fonts/truetype/liberation")
COLORS = {
    "volume": "#2a78d6",
    "distance": "#eb6834",
    "fleet": "#1baf7a",
    "effective_supply": "#eda100",
    "residual": "#898781",
    "signal_oil": "#e87ba4",
    "signal_iron_ore": "#008300",
}
LABELS = {
    "volume": "Cargo volume",
    "distance": "Average haul",
    "fleet": "Fleet growth",
    "effective_supply": "Effective supply (port time)",
    "residual": "Residual",
    "signal_oil": "Oil price signal (Brent)",
    "signal_iron_ore": "Iron ore price signal (3 months earlier)",
}
PHYSICAL_KEYS = ("volume", "distance", "fleet", "effective_supply")


def _stack_keys(frame_or_cols: Any) -> tuple[str, ...]:
    """Physical components, the signal columns present, then the residual."""
    cols = list(frame_or_cols)
    return (*PHYSICAL_KEYS, *[c for c in ("signal_oil", "signal_iron_ore") if c in cols], "residual")


COMMODITY_LABEL = {"iron_ore": "Iron ore", "coal": "Coal", "grains": "Grains and soybeans"}


def register_fonts() -> None:
    """Register Liberation Serif with ReportLab (regular, bold, italic)."""
    pdfmetrics.registerFont(
        TTFont("LiberationSerif", "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf")
    )
    pdfmetrics.registerFont(TTFont("LiberationSerif-Bold", str(FONT_DIR / "LiberationSerif-Bold.ttf")))
    pdfmetrics.registerFont(TTFont("LiberationSerif-Italic", str(FONT_DIR / "LiberationSerif-Italic.ttf")))
    pdfmetrics.registerFont(TTFont("LiberationSerif-BoldItalic", str(FONT_DIR / "LiberationSerif-BoldItalic.ttf")))
    pdfmetrics.registerFontFamily(
        "LiberationSerif",
        normal="LiberationSerif",
        bold="LiberationSerif-Bold",
        italic="LiberationSerif-Italic",
        boldItalic="LiberationSerif-BoldItalic",
    )


def _styles() -> dict[str, ParagraphStyle]:
    base = ParagraphStyle(
        "base", fontName="LiberationSerif", fontSize=10.2, leading=13.4, alignment=TA_JUSTIFY, spaceAfter=5
    )
    return {
        "body": base,
        "title": ParagraphStyle(
            "title", parent=base, fontName="LiberationSerif-Bold", fontSize=20, leading=24, alignment=0, spaceAfter=4
        ),
        "subtitle": ParagraphStyle(
            "subtitle",
            parent=base,
            fontSize=11.5,
            leading=15,
            alignment=0,
            textColor=colors.HexColor("#4f4c45"),
            spaceAfter=10,
        ),
        "h2": ParagraphStyle(
            "h2",
            parent=base,
            fontName="LiberationSerif-Bold",
            fontSize=12.5,
            leading=15,
            alignment=0,
            spaceBefore=8,
            spaceAfter=4,
        ),
        "small": ParagraphStyle(
            "small", parent=base, fontSize=8.4, leading=10.5, alignment=0, textColor=colors.HexColor("#4f4c45")
        ),
        "caption": ParagraphStyle(
            "caption",
            parent=base,
            fontSize=8.6,
            leading=11,
            alignment=0,
            textColor=colors.HexColor("#4f4c45"),
            spaceAfter=8,
        ),
        "cell": ParagraphStyle("cell", parent=base, fontSize=8.4, leading=10.4, alignment=0, spaceAfter=0),
    }


def _month_label(m: str) -> str:
    y, mo = m.split("-")
    return ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][int(mo) - 1] + " " + y


def _lp(v: float) -> str:
    return f"{v:+.2f}"


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def load_results() -> dict[str, Any]:
    """Collect every processed output needed by the note and the written deliverables."""
    panel = pd.read_csv(PROCESSED_DIR / "panel_monthly.csv")
    roll = pd.read_csv(PROCESSED_DIR / "decomposition_rolling.csv")
    episodes = json.loads((PROCESSED_DIR / "decomposition_episodes.json").read_text())
    model = json.loads((PROCESSED_DIR / "model_elasticity.json").read_text())
    diff_reg = json.loads((PROCESSED_DIR / "model_difference_regression.json").read_text())
    sig_path = PROCESSED_DIR / "model_signals.json"
    signals = json.loads(sig_path.read_text()) if sig_path.exists() else {}
    validation = json.loads((PROCESSED_DIR / "validation_report.json").read_text())
    by_route = pd.read_csv(PROCESSED_DIR / "tonne_miles_by_route.csv")
    fleet = pd.read_csv(PROCESSED_DIR / "fleet_annual.csv")
    coverage = json.loads((PROCESSED_DIR / "coverage.json").read_text())
    last12 = by_route[by_route["month"] > str(pd.Period(by_route["month"].max(), "M") - 12)]
    route_tm = last12.groupby("route_id")["tonne_miles"].sum().sort_values(ascending=False)
    shares = route_tm / route_tm.sum()
    return {
        "panel": panel,
        "roll": roll,
        "episodes": episodes,
        "model": model,
        "diff_reg": diff_reg,
        "signals": signals,
        "validation": validation,
        "by_route": by_route,
        "fleet": fleet,
        "coverage": coverage,
        "route_shares": shares,
        "route_tonnes": last12.groupby("route_id")["tonnes"].sum(),
        "sources": all_metadata(),
    }


def _route_name(rid: str) -> str:
    o, d, c = rid.split("-", 2)
    return f"{ECONOMY_NAMES.get(o, o)} to {ECONOMY_NAMES.get(d, d)}, {COMMODITY_LABEL[c].lower()}"


def make_figures(res: dict[str, Any]) -> dict[str, Path]:
    """Draw the figures used in the note and return their paths."""
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.serif": ["Liberation Serif", "DejaVu Serif"],
            "font.size": 9,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.edgecolor": "#c5bfb1",
            "axes.labelcolor": "#4f4c45",
            "xtick.color": "#4f4c45",
            "ytick.color": "#4f4c45",
            "axes.grid": True,
            "grid.color": "#e8e4da",
            "grid.linewidth": 0.6,
        }
    )
    out: dict[str, Path] = {}
    roll = res["roll"]
    # Figure 1: twelve-month change in the index and the part attributed to the four forces (rolling decomposition)
    fig, ax = plt.subplots(figsize=(7.0, 2.9))
    fx = pd.PeriodIndex(roll["month"], freq="M").to_timestamp()
    actual_pct = (np.exp(roll["total"]) - 1) * 100
    implied_pct = (np.exp(roll["total"] - roll["residual"]) - 1) * 100
    ax.axhline(0, color="#c5bfb1", lw=0.8)
    ax.plot(fx, actual_pct, color="#1f3a5f", lw=1.6, label="Baltic Dry Index, 12-month change")
    has_sig = "residual_physical" in roll.columns
    ax.plot(
        fx,
        implied_pct,
        color="#9a9488",
        lw=1.3,
        ls="--",
        label="Attributed to forces and market signals" if has_sig else "Attributed to the four forces",
    )
    if has_sig:
        physical_pct = (np.exp(roll["total"] - roll["residual_physical"]) - 1) * 100
        ax.plot(fx, physical_pct, color="#2a78d6", lw=1.0, label="Physical forces only")
    ax.fill_between(fx, implied_pct, actual_pct, color="#898781", alpha=0.12, linewidth=0)
    ax.set_ylabel("Percent change over 12 months")
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:+,.0f}"))
    ax.legend(frameon=False, loc="upper left", fontsize=8)
    fig.tight_layout()
    out["fitted"] = FIGURES_DIR / "fig1_change12_actual_vs_implied.png"
    fig.savefig(out["fitted"], dpi=200)
    plt.close(fig)
    # Figure 2: rolling attribution
    fig, ax = plt.subplots(figsize=(7.0, 3.0))
    rx = pd.PeriodIndex(roll["month"], freq="M").to_timestamp()
    pos = np.zeros(len(roll))
    neg = np.zeros(len(roll))
    width = 26
    for k in _stack_keys(roll.columns):
        v = roll[k].to_numpy()
        bottom = np.where(v >= 0, pos, neg)
        ax.bar(rx, v, width=width, bottom=bottom, color=COLORS[k], label=LABELS[k], linewidth=0)
        pos = pos + np.where(v >= 0, v, 0)
        neg = neg + np.where(v < 0, v, 0)
    ax.plot(rx, roll["total"], color="#1b1a17", lw=1.3, label="Total change in log index")
    ax.axhline(0, color="#c5bfb1", lw=0.8)
    ax.set_ylabel("Log points, 12-month change")
    ax.legend(frameon=False, fontsize=7.5, ncol=3, loc="upper left")
    ax.xaxis.set_major_locator(mdates.YearLocator(2))  # type: ignore[no-untyped-call]
    fig.tight_layout()
    out["rolling"] = FIGURES_DIR / "fig2_rolling_attribution.png"
    fig.savefig(out["rolling"], dpi=200)
    plt.close(fig)
    # Figure 3: route shares
    shares = res["route_shares"].head(12)
    fig, ax = plt.subplots(figsize=(7.0, 3.1))
    names = [_route_name(r) for r in shares.index]
    cols = [
        COLORS["volume"] if r.endswith("iron_ore") else COLORS["distance"] if r.endswith("coal") else COLORS["fleet"]
        for r in shares.index
    ]
    ax.barh(range(len(shares))[::-1], shares.to_numpy() * 100, color=cols, height=0.7)
    ax.set_yticks(range(len(shares))[::-1], names, fontsize=7.8)
    ax.set_xlabel("Share of covered tonne-miles, last twelve months (percent)")
    for i, v in enumerate(shares.to_numpy()[::-1] * 100):
        ax.text(v + 0.3, i, f"{v:.1f}", va="center", fontsize=7.5, color="#4f4c45")
    ax.grid(axis="y", visible=False)
    fig.tight_layout()
    out["routes"] = FIGURES_DIR / "fig3_route_shares.png"
    fig.savefig(out["routes"], dpi=200)
    plt.close(fig)
    # Figure 4: episode waterfall table as bars
    eps = res["episodes"]
    fig, ax = plt.subplots(figsize=(7.0, 2.6 + 0.25 * len(eps)))
    ypos = np.arange(len(eps))[::-1]
    left_pos = np.zeros(len(eps))
    left_neg = np.zeros(len(eps))
    for k in _stack_keys(eps[0].keys() if eps else []):
        v = np.array([e[k] for e in eps])
        left = np.where(v >= 0, left_pos, left_neg)
        ax.barh(ypos, v, left=left, color=COLORS[k], height=0.62, label=LABELS[k], linewidth=0)
        left_pos = left_pos + np.where(v >= 0, v, 0)
        left_neg = left_neg + np.where(v < 0, v, 0)
    ax.scatter([e["total"] for e in eps], ypos, color="#1b1a17", zorder=5, s=18, label="Total change")
    ax.set_yticks(
        ypos, [f"{e['name']} ({_month_label(e['start'])} to {_month_label(e['end'])})" for e in eps], fontsize=7.8
    )
    ax.axvline(0, color="#c5bfb1", lw=0.8)
    ax.set_xlabel("Log points")
    ax.grid(axis="y", visible=False)
    ax.legend(frameon=False, fontsize=7.2, ncol=3, loc="lower right")
    fig.tight_layout()
    out["episodes"] = FIGURES_DIR / "fig4_episodes.png"
    fig.savefig(out["episodes"], dpi=200)
    plt.close(fig)
    return out


def _numbers(res: dict[str, Any]) -> dict[str, Any]:
    panel, roll, model = res["panel"], res["roll"], res["model"]
    h = model["headline"]
    last = roll.iloc[-1]
    first_m, last_m = str(panel["month"].iloc[0]), str(panel["month"].iloc[-1])
    mean_abs_resid = float(roll["residual"].abs().mean())
    mean_abs_total = float(roll["total"].abs().mean())
    sign_agree = float((np.sign(roll["total"] - roll["residual"]) == np.sign(roll["total"])).mean())
    resid_phys = roll["residual_physical"] if "residual_physical" in roll.columns else roll["residual"]
    mean_abs_resid_phys = float(resid_phys.abs().mean())
    sign_agree_phys = float((np.sign(roll["total"] - resid_phys) == np.sign(roll["total"])).mean())
    sig = res.get("signals", {})
    oos = sig.get("out_of_sample", {}) if sig else {}
    tm_growth = float(panel["tonne_miles"].iloc[-12:].mean() / panel["tonne_miles"].iloc[:12].mean() - 1)
    t_growth = float(panel["tonnes"].iloc[-12:].mean() / panel["tonnes"].iloc[:12].mean() - 1)
    fleet_growth = float(panel["fleet"].iloc[-12:].mean() / panel["fleet"].iloc[:12].mean() - 1)
    comps = {
        k: float(last[k])
        for k in (
            "volume",
            "distance",
            "fleet",
            "effective_supply",
            "residual",
            "total",
            "signal_oil",
            "signal_iron_ore",
        )
        if k in last.index
    }
    biggest = max((k for k in ("volume", "distance", "fleet", "effective_supply")), key=lambda k: abs(comps[k]))
    top3 = float(res["route_shares"].head(3).sum())
    return {
        "first_m": first_m,
        "last_m": last_m,
        "n_months": len(panel),
        "elasticity": h["elasticity"],
        "se": h["std_error"],
        "r2": h["r_squared"],
        "dw": h["durbin_watson"],
        "intercept": h["intercept"],
        "n_obs_reg": h["n_obs"],
        "mean_abs_resid": mean_abs_resid,
        "mean_abs_total": mean_abs_total,
        "share_resid": mean_abs_resid / mean_abs_total if mean_abs_total else float("nan"),
        "sign_agree": sign_agree,
        "mean_abs_resid_phys": mean_abs_resid_phys,
        "share_resid_phys": mean_abs_resid_phys / mean_abs_total if mean_abs_total else float("nan"),
        "sign_agree_phys": sign_agree_phys,
        "has_signals": bool(sig),
        "sig_coef": sig.get("fit", {}).get("coefficients", {}) if sig else {},
        "sig_t": sig.get("fit", {}).get("t_stats", {}) if sig else {},
        "oos": oos,
        "tm_growth": tm_growth,
        "t_growth": t_growth,
        "haul_growth": (1 + tm_growth) / (1 + t_growth) - 1,
        "fleet_growth": fleet_growth,
        "last": comps,
        "last_start": str(last["start"]),
        "last_end": str(last["end"]),
        "last_pct": log_points_to_pct(comps["total"]),
        "biggest": biggest,
        "n_routes": int(res["by_route"]["route_id"].nunique()),
        "top3_share": top3,
        "bdi_last": float(panel["index"].iloc[-1]),
        "fleet_last_mdwt": float(panel["fleet"].iloc[-1]) / 1e6,
        "episodes": res["episodes"],
        "validation": res["validation"]["summary"],
        "diff_reg": res["diff_reg"],
        "robustness": model["robustness"],
        "signals": res.get("signals", {}),
        "explained_typical": 1 - mean_abs_resid / mean_abs_total if mean_abs_total else float("nan"),
        "explained_typical_physical": (
            1 - float(roll["residual_physical"].abs().mean()) / mean_abs_total
            if "residual_physical" in roll.columns and mean_abs_total
            else float("nan")
        ),
    }


def build_pdf(res: dict[str, Any], figs: dict[str, Path], out_path: Path) -> None:
    """Assemble the research note."""
    register_fonts()
    # every canvas starts in Liberation Serif, so no non-embedded base font is written to the file
    rl_config.canvas_basefontname = "LiberationSerif"
    st = _styles()
    n = _numbers(res)
    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title="Tonne-Mile Demand Model: a decomposition of Baltic Dry Index moves",
        author="Tonne-Mile Demand Model pipeline",
    )
    width = A4[0] - 36 * mm
    story: list[Any] = []
    story.append(Paragraph("Tonne-Mile Demand Model", st["title"]))
    story.append(
        Paragraph(
            f"How much of the move in the Baltic Dry Index is cargo volume, haul distance, fleet growth and port congestion? A decomposition built only from public data, {_month_label(n['first_m'])} to {_month_label(n['last_m'])}.",
            st["subtitle"],
        )
    )
    story.append(Paragraph("Summary", st["h2"]))
    story.append(
        Paragraph(
            f"Dry bulk freight rates are set by the balance between demand for tonne-miles and the effective supply of ships. "
            f"This note measures both sides from public sources and attributes changes in the Baltic Dry Index to four forces: cargo volume on {n['n_routes']} major bulk routes, "
            f"the average distance that cargo sails, the size of the bulk carrier fleet, and the share of fleet time absorbed in port. "
            f"Over {n['n_months']} months a one percent rise in tonne-miles relative to effective fleet has been associated with a {n['elasticity']:.1f} percent rise in the index "
            f"(standard error {n['se']:.1f}, estimated on twelve-month changes); the relationship explains {n['r2']:.0%} of the variance of twelve-month changes in the log index. "
            f"Over rolling twelve-month windows the four physical forces leave about {n['share_resid_phys']:.0%} of a typical move unexplained (mean residual {n['mean_abs_resid_phys']:.2f} log points against a typical move of {n['mean_abs_total']:.2f}). "
            + (
                f"Two public market-price signals, the Brent crude price and the iron ore price three months earlier, carry much of the missing commodity cycle: with them the unexplained share falls to {n['share_resid']:.0%} in sample"
                + (
                    f" and {n['oos']['unexplained']['extended']:.0%} out of sample (against {n['oos']['unexplained']['physical']:.0%} for the physical forces alone)"
                    if n["oos"].get("available")
                    else ""
                )
                + ". They are reported as separate market-signal bars, not as physical forces. "
                if n["has_signals"]
                else ""
            )
            + f"In the latest window ({_month_label(n['last_start'])} to {_month_label(n['last_end'])}) the index moved {n['last_pct']:+.0f} percent; the largest physical contribution was {LABELS[n['biggest']].lower()} ({_lp(n['last'][n['biggest']])} log points) and the residual was {_lp(n['last']['residual'])}.",
            st["body"],
        )
    )
    story.append(Paragraph("The question and the identity", st["h2"]))
    story.append(
        Paragraph(
            "Tonne-mile demand is the sum over routes of tonnes shipped times sailing distance; average haul is tonne-miles divided by tonnes, so the change in tonne-miles is exactly the change in volume plus the change in haul. "
            "Fleet supply is bulk carrier deadweight in service. Effective supply multiplies the fleet by the share of time at sea, derived from median port time per call and calls per ship. "
            "For any two months the identity log(BDI1/BDI0) = e[dlog V + dlog H] minus e[dlog F + dlog S] + residual holds by construction, where e is a single elasticity estimated by regressing twelve-month changes in the log index on twelve-month changes in the log of tonne-miles over effective fleet. "
            "Endpoints are three-month averages. The residual is reported as the fifth component; it is never spread across the others.",
            st["body"],
        )
    )
    story.append(Image(str(figs["fitted"]), width=width, height=width * 2.9 / 7.0))
    story.append(
        Paragraph(
            f"Figure 1. Twelve-month percent change in the Baltic Dry Index and the part attributed to cargo volume, haul, fleet and port time at the estimated elasticity ({n['elasticity']:.2f}); endpoints are three-month averages. Shaded area is the residual. Sources: Statistics and Research Aland (index, originator Baltic Exchange); UN Comtrade, Brazil Comex Stat, UNCTADstat and this project's distance matrix (inputs).",
            st["caption"],
        )
    )
    story.append(Paragraph("Data", st["h2"]))
    rows = [
        [
            Paragraph("<b>Series</b>", st["cell"]),
            Paragraph("<b>Source</b>", st["cell"]),
            Paragraph("<b>Frequency and coverage</b>", st["cell"]),
            Paragraph("<b>Retrieved</b>", st["cell"]),
        ]
    ]
    for m in res["sources"]:
        rows.append(
            [
                Paragraph(m.title, st["cell"]),
                Paragraph(m.publisher, st["cell"]),
                Paragraph(f"{m.frequency}; {m.coverage}", st["cell"]),
                Paragraph(m.retrieved_at[:10], st["cell"]),
            ]
        )
    t = Table(rows, colWidths=[width * 0.34, width * 0.26, width * 0.28, width * 0.12], repeatRows=1)
    t.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "LiberationSerif"),
                ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#c5bfb1")),
                ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#e8e4da")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    story.append(t)
    story.append(Spacer(1, 4))
    story.append(
        Paragraph(
            f"Routes are measured from the customs data of the exporter wherever it publishes timely monthly data (Australia, Brazil, Indonesia, South Africa, United States, Canada, Ukraine, Argentina, Colombia) and from the importer for Russian and Mozambican origins (India, Japan). "
            f"Chinese import records lag by more than a year and are used only for cross-checks. Distances are volume-weighted blends between representative terminals on a maritime routing network, with iron ore and most coal routed around the Panama Canal (Capesize tonnage; coal from the Americas, largely on Panamax ships, takes the canal), Suez routes lengthened towards the Cape in proportion to the fall in dry bulk transits through Bab el-Mandeb observed by IMF PortWatch since November 2023, and Panama routes lengthened the same way during the 2023 to September 2024 draught restrictions. "
            f"The three largest routes carry {n['top3_share']:.0%} of covered tonne-miles. Validation: {n['validation']['pass']} checks passed, {_plural(n['validation']['warn'], 'warning')}, {_plural(n['validation']['fail'], 'failure')} (full report on the site).",
            st["body"],
        )
    )
    story.append(PageBreak())
    story.append(Paragraph("Results", st["h2"]))
    story.append(
        Paragraph(
            f"Between the first and last twelve months of the panel, tonnes on the covered routes grew {n['t_growth']:+.0%} and tonne-miles {n['tm_growth']:+.0%}, implying an average haul change of {n['haul_growth']:+.1%}; the bulk carrier fleet grew {n['fleet_growth']:+.0%} to {n['fleet_last_mdwt']:,.0f} million dwt. "
            f"Figure 2 attributes every rolling twelve-month change of the log index. Fleet growth is a steady negative contribution; volume and haul alternate as the main positive drivers; the port-time factor matters in 2021 and 2022; the residual carries the spikes.",
            st["body"],
        )
    )
    story.append(Image(str(figs["rolling"]), width=width, height=width * 3.0 / 7.0))
    story.append(
        Paragraph(
            "Figure 2. Twelve-month change in the log Baltic Dry Index (line) and its attribution (bars), log points. Endpoints are three-month averages.",
            st["caption"],
        )
    )
    story.append(Image(str(figs["episodes"]), width=width, height=width * (2.6 + 0.25 * len(n["episodes"])) / 7.0))
    story.append(
        Paragraph(
            "Figure 3. Named episodes covered by the panel: contributions in log points (bars) and the total change (dot). An episode is listed only when both endpoints fall inside the data.",
            st["caption"],
        )
    )
    ep_rows = [
        [
            Paragraph(f"<b>{h}</b>", st["cell"])
            for h in (
                "Episode",
                "Window",
                "Total",
                "Vol.",
                "Haul",
                "Fleet",
                "Port",
                "Oil",
                "Ore",
                "Resid.",
                "Explained",
            )
        ]
    ]
    for e in n["episodes"]:
        ex = e.get("explained_share")
        exp = e.get("explained_share_physical")
        small = abs(e["total"]) < 0.1
        ep_rows.append(
            [
                Paragraph(e["name"], st["cell"]),
                Paragraph(f"{_month_label(e['start'])} to {_month_label(e['end'])}", st["cell"]),
                _lp(e["total"]),
                _lp(e["volume"]),
                _lp(e["distance"]),
                _lp(e["fleet"]),
                _lp(e["effective_supply"]),
                _lp(e.get("signal_oil", 0.0)),
                _lp(e.get("signal_iron_ore", 0.0)),
                _lp(e["residual"]),
                "n/m"
                if small or ex is None or not np.isfinite(ex)
                else (f"{ex:.0%} ({exp:.0%})" if exp is not None and np.isfinite(exp) else f"{ex:.0%}"),
            ]
        )
    t2 = Table(
        ep_rows,
        colWidths=[width * f for f in (0.16, 0.13, 0.07, 0.07, 0.065, 0.065, 0.065, 0.065, 0.065, 0.075, 0.12)],
        repeatRows=1,
    )
    t2.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), "LiberationSerif"),
                ("FONTSIZE", (0, 0), (-1, -1), 7.8),
                ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#c5bfb1")),
                ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#e8e4da")),
                ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ]
        )
    )
    story.append(t2)
    story.append(
        Paragraph(
            "Table 1. Episode decomposition in log points. Vol. is cargo volume and Port is port time; Oil and Ore are the market-signal bars (Brent, and iron ore three months earlier). Explained is one minus residual over total, with the physical-forces-only share in brackets; it can exceed 100 percent when the residual opposes the move and is shown as n/m for moves under 0.10 log points.",
            st["caption"],
        )
    )
    story.append(Image(str(figs["routes"]), width=width, height=width * 3.1 / 7.0))
    story.append(
        Paragraph(
            "Figure 4. The twelve largest routes by tonne-miles in the last twelve months of the panel; blue iron ore, orange coal, green grains and soybeans.",
            st["caption"],
        )
    )
    if n["has_signals"]:
        story.append(Paragraph("Market signals", st["h2"]))
        o = n["oos"]
        oos_text = (
            f" Out of sample, refitting every coefficient each month with data that ended at least twelve months earlier ({o['n_months']} months from {_month_label(o['first_month'])} to {_month_label(o['last_month'])}), "
            f"R squared against a zero forecast rises from {o['r2_vs_zero']['physical']:.2f} to {o['r2_vs_zero']['extended']:.2f} and the unexplained share falls from {o['unexplained']['physical']:.0%} to {o['unexplained']['extended']:.0%}; "
            f"a benchmark with no fundamentals, the index's own twelve-month change three months earlier, reaches {o['r2_vs_zero']['persistence']:.2f}."
            if o.get("available")
            else ""
        )
        story.append(
            Paragraph(
                "The physical balance moves a few percent a year while the index moves by tens of percent. Two public World Bank prices carry much of the missing variation and are added after the four physical forces as separately named bars, with the elasticity of the identity unchanged: "
                f"the Brent crude price over the same window (coefficient {n['sig_coef'].get('oil', float('nan')):.2f}, t {n['sig_t'].get('oil', float('nan')):.1f}) and the iron ore price, CFR China, over the window ending three months earlier ({n['sig_coef'].get('iron_ore', float('nan')):.2f}, t {n['sig_t'].get('iron_ore', float('nan')):.1f}); the lead keeps the freight contained in a delivered price out of the same window. "
                f"The coefficients come from a regression without intercept of the physical residual on the two price changes over every rolling window. In sample the unexplained share of a typical move falls from {n['share_resid_phys']:.0%} to {n['share_resid']:.0%}.{oos_text} "
                "These are market signals, not physical forces: oil and iron ore prices react to the same global and Chinese demand shocks as freight, and other industrial commodity prices carry similar information, so the bars measure a commodity cycle that the customs-based balance misses rather than a causal channel. "
                "The two series were chosen after testing several candidate prices and leads on the same sample, which flatters the out-of-sample gain.",
                st["body"],
            )
        )
    story.append(Paragraph("Robustness", st["h2"]))
    rb = n["robustness"]
    parts = [
        f"twelve-month changes (headline, {n['n_obs_reg']} observations): {n['elasticity']:.2f} (se {n['se']:.2f}, R squared {n['r2']:.2f}, Durbin-Watson {n['dw']:.2f}, intercept {n['intercept']:+.2f} log points a year, which the decomposition leaves in the residual)"
    ]
    names = {
        "levels_with_month_dummies": "monthly levels with month dummies",
        "levels_plain": "monthly levels",
        "quarterly_four_quarter_changes": "quarterly averages, four-quarter changes",
        "first_half": "first half of the sample",
        "second_half": "second half of the sample",
    }
    for k, v in rb.items():
        parts.append(
            f"{names.get(k, k)}: {v['elasticity']:.2f} (se {v['std_error']:.2f}, R squared {v['r_squared']:.2f})"
        )
    dr = n["diff_reg"]
    dr_text = ""
    if dr.get("coefficients"):
        co, se = dr["coefficients"], dr["std_errors"]
        dr_text = (
            f" An unrestricted regression of monthly changes in the log index on the four input changes and their lags ({dr['n_obs']} months, R squared {dr['r_squared']:.2f}) gives contemporaneous coefficients of "
            f"{co['volume']:+.2f} (volume), {co['haul']:+.2f} (haul), {co['fleet']:+.2f} (fleet) and {co['supply_factor']:+.2f} (port time), with standard errors {se['volume']:.2f}, {se['haul']:.2f}, {se['fleet']:.2f} and {se['supply_factor']:.2f}; "
            f"a Wald test of one common elasticity with the signs of the identity has p = {dr['common_elasticity_wald_pvalue']:.2f}."
        )
    story.append(
        Paragraph(
            "The elasticity is re-estimated on alternative samples and transformations: "
            + "; ".join(parts)
            + "."
            + dr_text
            + " The levels regressions are reported for completeness only: both series trend and are strongly autocorrelated, so their coefficients are not reliable (low Durbin-Watson statistics); freight rates also move with information the inputs do not carry.",
            st["body"],
        )
    )
    story.append(Paragraph("Limitations", st["h2"]))
    story.append(
        Paragraph(
            "Sailing speed, waiting outside ports and ballast ratios are not observable from public data and remain in the residual with bunker prices, sentiment and contract timing. Some customs records carry values without weights (United States monthly records before 2016); tonnes for those months are estimated from the reporter's annual unit values and flagged, and the Russia to Egypt grain route is left out because Egyptian records lack weights in most years. "
            "The route set covers the major bulks between the main exporters and importers; minor bulks and regional trades that drive Supramax and Handysize rates are excluded. "
            "Representative ports approximate real terminals; the error is a few percent of a voyage and constant over time, so it affects levels rather than changes. "
            "Customs months record declarations, not sailings. Episodes shorter than twelve months compare endpoints in different seasons, and cargo volumes are seasonal, so their volume effect includes seasonal movement. The index composition changed in 2018. The freight index is republished by the Aland statistics office less often than the demand data, so the balanced panel ends earlier than the volume series. "
            "Deliveries and demolitions by ship type are not in the public UNCTAD tables; fleet growth is shown net.",
            st["body"],
        )
    )
    story.append(Paragraph("Reproducibility", st["h2"]))
    story.append(
        Paragraph(
            "The pipeline (Python 3.11 or later) runs in six stages: ingest, validate, transform, model, export and report; one command rebuilds everything, unit tests check the distance routing, the tonne-mile aggregation and the adding-up of the decomposition, and a scheduled GitHub Actions workflow refreshes the data and republishes the site. "
            "Every raw file is stored with a metadata record (source URL, retrieval timestamp, frequency, coverage, licence and known limitations). Code, data and this note: github.com/nathancouturier/Tonne-Mile-Demand-Model.",
            st["body"],
        )
    )
    doc.build(story)


def write_deliverables(res: dict[str, Any]) -> None:
    """CV description, interview talking points and a results summary used by the README."""
    n = _numbers(res)
    cv = (
        f"Tonne-Mile Demand Model (Python, D3, GitHub Actions): built an automated, fully documented pipeline that decomposes moves in the Baltic Dry Index into cargo volume, haul distance, fleet growth and port-time effects using only public data (UN Comtrade, Brazil Comex Stat, IMF PortWatch AIS data, UNCTADstat, NGA World Port Index), with sea distances computed on a maritime routing network for {n['n_routes']} major bulk routes.\n"
        f"Estimated a freight rate elasticity of {n['elasticity']:.1f} to the tonne-mile over effective fleet balance (twelve-month changes, {n['n_months']} months of data); the physical balance alone leaves about {n['share_resid_phys']:.0%} of a typical twelve-month move unexplained"
        + (
            f", and two public price signals (Brent crude, iron ore) cut this to {n['share_resid']:.0%} in sample"
            + (f" and {n['oos']['unexplained']['extended']:.0%} out of sample" if n["oos"].get("available") else "")
            if n["has_signals"]
            else ""
        )
        + ".\n"
        "Published the results as an interactive research-note website with weekly data refresh, validation and cross-source reconciliation checks, and a generated PDF note.\n"
    )
    (DOCS_DIR / "cv-description.txt").write_text(cv, encoding="utf-8")
    talking = (
        f"1. Question: how much of a move in the Baltic Dry Index is cargo volume, how much is average haul, how much is fleet growth and how much is port congestion, and what is left over?\n"
        f"2. Method: a log-difference attribution identity, log(BDI1/BDI0) = e[dlog volume + dlog haul] minus e[dlog fleet + dlog at-sea share] + residual, with one elasticity e estimated on twelve-month changes (Newey-West errors) and checked on levels with month dummies, quarterly four-quarter changes, sample halves and an unrestricted first-difference regression; tonne-miles built route by route from customs data and a maritime routing network ({n['n_routes']} routes).\n"
        f"3. Finding: e is about {n['elasticity']:.1f} (twelve-month changes, R squared {n['r2']:.2f}, {n['n_months']} months of data); the physical forces leave about {n['share_resid_phys']:.0%} of a typical twelve-month move unexplained and fleet growth is a steady drag"
        + (
            f"; adding Brent and the lagged iron ore price as market signals cuts the unexplained share to {n['share_resid']:.0%} in sample"
            + (f" and {n['oos']['unexplained']['extended']:.0%} out of sample" if n["oos"].get("available") else "")
            + ", a commodity-cycle effect the customs volumes miss"
            if n["has_signals"]
            else ""
        )
        + f"; in the latest window the largest physical driver was {LABELS[n['biggest']].lower()}.\n"
        f"4. Limitation: speed and anchorage waiting are not observable from free data and sit in the residual; the price signals are market correlates, not causes, and were chosen on the same sample; the public monthly index ends earlier than the demand data; representative ports and customs timing add measurement error.\n"
        f"5. Next: replace the price signals with physical cycle measures (China steel output, monthly ship deliveries, berth waiting times), add a Capesize versus Panamax split if sub-index history becomes available, and estimate a time-varying elasticity to test whether the market's sensitivity to utilisation has changed since 2021.\n"
    )
    (DOCS_DIR / "interview-talking-points.txt").write_text(talking, encoding="utf-8")
    (DOCS_DIR / "results-summary.json").write_text(
        json.dumps(
            {k: v for k, v in n.items() if k not in ("episodes", "diff_reg", "robustness")}, indent=2, default=str
        ),
        encoding="utf-8",
    )


def write_readme_results(res: dict[str, Any]) -> None:
    """Replace the results block of the README with figures from the current build."""
    n = _numbers(res)
    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    start_tag, end_tag = "<!-- results:start -->", "<!-- results:end -->"
    if start_tag not in text or end_tag not in text:
        return
    eps = n["episodes"]
    lines = [
        "## Results",
        "",
        f"Panel: {_month_label(n['first_m'])} to {_month_label(n['last_m'])}, {n['n_months']} months, {n['n_routes']} routes. "
        f"Built from data retrieved on {max(m.retrieved_at for m in res['sources'])[:10]}.",
        "",
        f"* Elasticity of the index to tonne-miles over effective fleet: {n['elasticity']:.2f} (Newey-West standard error {n['se']:.2f}); "
        f"estimated on twelve-month changes, it explains {n['r2']:.0%} of the variance of twelve-month changes in the log index (Durbin-Watson {n['dw']:.2f}).",
        f"* Over rolling twelve-month windows the four physical forces leave about {n['share_resid_phys']:.0%} of a typical move unexplained "
        f"(mean residual {n['mean_abs_resid_phys']:.2f} log points against a typical move of {n['mean_abs_total']:.2f}) and have the sign of the move {n['sign_agree_phys']:.0%} of the time.",
        *(
            [
                f"* Adding two public market-price signals (Brent crude over the same window, coefficient {n['sig_coef'].get('oil', float('nan')):.2f}; iron ore three months earlier, {n['sig_coef'].get('iron_ore', float('nan')):.2f}) "
                f"cuts the unexplained share to {n['share_resid']:.0%} in sample"
                + (
                    f" and from {n['oos']['unexplained']['physical']:.0%} to {n['oos']['unexplained']['extended']:.0%} out of sample "
                    f"({n['oos']['n_months']} months, every coefficient refitted on data ending twelve months earlier)"
                    if n["oos"].get("available")
                    else ""
                )
                + ". They are market correlates of the commodity cycle, shown as separate bars, not physical forces."
            ]
            if n["has_signals"]
            else []
        ),
        f"* Between the first and last twelve months of the panel, tonnes on the covered routes grew {n['t_growth']:+.0%}, tonne-miles {n['tm_growth']:+.0%} "
        f"(average haul {n['haul_growth']:+.1%}) and the bulk carrier fleet {n['fleet_growth']:+.0%} to {n['fleet_last_mdwt']:,.0f} million dwt.",
        f"* Latest window ({_month_label(n['last_start'])} to {_month_label(n['last_end'])}): the index moved {n['last_pct']:+.0f} percent; "
        f"contributions in log points were volume {_lp(n['last']['volume'])}, haul {_lp(n['last']['distance'])}, fleet {_lp(n['last']['fleet'])}, "
        f"port time {_lp(n['last']['effective_supply'])}"
        + (
            f", oil signal {_lp(n['last'].get('signal_oil', 0.0))}, iron ore signal {_lp(n['last'].get('signal_iron_ore', 0.0))}"
            if n["has_signals"]
            else ""
        )
        + f", residual {_lp(n['last']['residual'])} (total {_lp(n['last']['total'])}).",
        f"* The three largest routes carry {n['top3_share']:.0%} of covered tonne-miles. Validation: {n['validation']['pass']} checks passed, "
        f"{_plural(n['validation']['warn'], 'warning')}, {_plural(n['validation']['fail'], 'failure')}.",
        "",
        "| Episode | Window | Total | Volume | Haul | Fleet | Port time | Oil signal | Iron ore signal | Residual |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for e in eps:
        lines.append(
            f"| {e['name']} | {_month_label(e['start'])} to {_month_label(e['end'])} | {_lp(e['total'])} | {_lp(e['volume'])} | "
            f"{_lp(e['distance'])} | {_lp(e['fleet'])} | {_lp(e['effective_supply'])} | {_lp(e.get('signal_oil', 0.0))} | "
            f"{_lp(e.get('signal_iron_ore', 0.0))} | {_lp(e['residual'])} |"
        )
    lines += [
        "",
        "Values are log points (0.10 is roughly a ten percent move). Endpoints are three-month averages. "
        "The oil and iron ore columns are market signals, not physical forces.",
    ]
    block = "\n".join(lines)
    before = text.split(start_tag)[0]
    after = text.split(end_tag)[1]
    readme.write_text(f"{before}{start_tag}\n{block}\n{end_tag}{after}", encoding="utf-8")


def run() -> None:
    """Build figures, the PDF note and the written deliverables; copy the note to the site."""
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    res = load_results()
    figs = make_figures(res)
    out = DOCS_DIR / PDF_NAME
    build_pdf(res, figs, out)
    write_deliverables(res)
    write_readme_results(res)
    dl = SITE_DIR / "downloads"
    dl.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(out, dl / PDF_NAME)
    log.info("report: wrote %s (%d bytes)", out.relative_to(ROOT), out.stat().st_size)
