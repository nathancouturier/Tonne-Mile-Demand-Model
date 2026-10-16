"""Reusable data quality checks.

Each check returns a :class:`CheckResult` with a status (pass, warn or fail),
a short message and the numbers it looked at. Results are collected into a
validation report that is published on the site, so that the limitations of
the data are visible rather than hidden in logs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class CheckResult:
    """Outcome of one validation check."""

    dataset: str
    check: str
    status: str  # pass | warn | fail
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary."""
        d = asdict(self)
        d["details"] = {k: (v.item() if hasattr(v, "item") else v) for k, v in self.details.items()}
        return d


def check_missing(
    dataset: str, df: pd.DataFrame, columns: list[str], warn_above: float = 0.0, fail_above: float = 0.2
) -> CheckResult:
    """Report the share of missing values in the given columns."""
    shares = {c: float(df[c].isna().mean()) if len(df) else 0.0 for c in columns}
    worst = max(shares.values()) if shares else 0.0
    status = "fail" if worst > fail_above else "warn" if worst > warn_above else "pass"
    return CheckResult(
        dataset,
        "missing_values",
        status,
        f"highest missing share {worst:.1%}",
        {"missing_share": shares, "rows": len(df)},
    )


def check_monthly_continuity(dataset: str, months: pd.Series) -> CheckResult:
    """Check that a monthly series has no gaps between its first and last month."""
    p = pd.PeriodIndex([str(m)[:7] for m in months.tolist()], freq="M").unique().sort_values()
    if len(p) == 0:
        return CheckResult(dataset, "monthly_continuity", "fail", "no months", {})
    expected = pd.period_range(p[0], p[-1], freq="M")
    gaps = sorted(str(m) for m in expected.difference(p))
    status = "pass" if not gaps else "warn" if len(gaps) <= 3 else "fail"
    return CheckResult(
        dataset,
        "monthly_continuity",
        status,
        f"{len(gaps)} missing months between {p[0]} and {p[-1]}",
        {"first": str(p[0]), "last": str(p[-1]), "gaps": gaps[:24]},
    )


def check_outliers(dataset: str, series: pd.Series, label: str, z: float = 4.0) -> CheckResult:
    """Flag values whose robust z-score (median absolute deviation) exceeds ``z``.

    The check is applied to log values for strictly positive series so that
    multiplicative spikes are judged on a proportional scale.
    """
    s = series.dropna().astype(float)
    if len(s) < 12:
        return CheckResult(dataset, f"outliers:{label}", "warn", "too few observations", {"n": len(s)})
    x = np.log(s[s > 0]) if (s > 0).all() else s
    med = float(np.median(x))
    mad = float(np.median(np.abs(x - med))) * 1.4826
    if mad == 0:
        return CheckResult(dataset, f"outliers:{label}", "pass", "no dispersion", {"n": len(s)})
    rz = (x - med) / mad
    flagged = s[np.abs(rz) > z]
    status = "pass" if flagged.empty else "warn"
    return CheckResult(
        dataset,
        f"outliers:{label}",
        status,
        f"{len(flagged)} robust outliers beyond {z:g} MAD",
        {"n": len(s), "flagged": {str(k): float(v) for k, v in flagged.head(12).items()}},
    )


def check_range(dataset: str, series: pd.Series, label: str, low: float, high: float) -> CheckResult:
    """Check that every value lies inside a plausible unit range (unit consistency)."""
    s = series.dropna().astype(float)
    bad = s[(s < low) | (s > high)]
    status = "pass" if bad.empty else "fail"
    return CheckResult(
        dataset,
        f"range:{label}",
        status,
        f"{len(bad)} values outside [{low:g}, {high:g}]",
        {
            "min": float(s.min()) if len(s) else None,
            "max": float(s.max()) if len(s) else None,
            "n_bad": len(bad),
        },
    )


def check_reconciliation(
    dataset: str,
    a: pd.Series,
    b: pd.Series,
    label_a: str,
    label_b: str,
    warn_gap: float = 0.10,
    fail_gap: float = 0.30,
) -> CheckResult:
    """Compare two series measuring the same quantity and report the gap.

    The gap is the median absolute relative difference over overlapping periods;
    the sign of the mean relative difference tells which source is higher.
    """
    j = pd.concat([a.rename("a"), b.rename("b")], axis=1).dropna()
    j = j[(j["a"] > 0) & (j["b"] > 0)]
    if j.empty:
        return CheckResult(dataset, f"reconcile:{label_a}_vs_{label_b}", "warn", "no overlap", {})
    rel = (j["a"] - j["b"]) / j["b"]
    med_abs = float(rel.abs().median())
    status = "fail" if med_abs > fail_gap else "warn" if med_abs > warn_gap else "pass"
    corr = float(np.corrcoef(np.log(j["a"]), np.log(j["b"]))[0, 1]) if len(j) > 2 else float("nan")
    return CheckResult(
        dataset,
        f"reconcile:{label_a}_vs_{label_b}",
        status,
        f"median absolute gap {med_abs:.1%}, mean gap {float(rel.mean()):+.1%} "
        f"({label_a} relative to {label_b}), {len(j)} overlapping periods",
        {
            "median_abs_rel_gap": med_abs,
            "mean_rel_gap": float(rel.mean()),
            "log_correlation": corr,
            "n_overlap": len(j),
            "total_a": float(j["a"].sum()),
            "total_b": float(j["b"].sum()),
        },
    )
