"""Log-difference attribution of a freight index move.

The identity used throughout the project is

    log(BDI_1 / BDI_0) = e * [dlog V + dlog H - dlog F - dlog S] + residual

where V is cargo volume on the covered routes, H the average haul
(tonne-miles divided by tonnes), F the bulk carrier fleet in deadweight
tonnes, S an effective supply factor (share of fleet time at sea), and e the
estimated elasticity of the index to the demand/supply balance. The residual
is defined as whatever is left, so the four contributions and the residual
always sum exactly to the observed change. Contributions are reported both in
log points and as shares of the total move. :class:`ExtendedAttribution` adds
named market-signal contributions (see :mod:`tmdm.model.signals`) between the
physical components and the residual.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

COMPONENTS = ("volume", "distance", "fleet", "effective_supply")


@dataclass(frozen=True)
class Attribution:
    """Decomposition of one log change into named contributions and a residual."""

    total: float
    volume: float
    distance: float
    fleet: float
    effective_supply: float
    residual: float
    elasticity: float

    def as_dict(self) -> dict[str, float]:
        """Return a plain dictionary."""
        return asdict(self)

    def explained_share(self) -> float:
        """Share of the total move explained by the four components (can exceed 1 or be negative)."""
        if self.total == 0:
            return float("nan")
        return 1.0 - self.residual / self.total

    def check_sums(self, tol: float = 1e-9) -> bool:
        """Verify that the components and residual add back to the total."""
        s = self.volume + self.distance + self.fleet + self.effective_supply + self.residual
        return abs(s - self.total) < tol


def attribute(
    *,
    index_0: float,
    index_1: float,
    volume_0: float,
    volume_1: float,
    tonne_miles_0: float,
    tonne_miles_1: float,
    fleet_0: float,
    fleet_1: float,
    supply_factor_0: float,
    supply_factor_1: float,
    elasticity: float,
) -> Attribution:
    """Attribute the log change of a freight index between two points in time.

    All inputs must be strictly positive. Haul is derived as tonne-miles divided
    by volume so that the volume and distance effects add exactly to the
    tonne-mile effect.
    """
    vals = [
        index_0,
        index_1,
        volume_0,
        volume_1,
        tonne_miles_0,
        tonne_miles_1,
        fleet_0,
        fleet_1,
        supply_factor_0,
        supply_factor_1,
    ]
    if any(v <= 0 or not np.isfinite(v) for v in vals):
        raise ValueError("all inputs must be positive and finite")
    total = float(np.log(index_1 / index_0))
    dlog_v = float(np.log(volume_1 / volume_0))
    haul_0, haul_1 = tonne_miles_0 / volume_0, tonne_miles_1 / volume_1
    dlog_h = float(np.log(haul_1 / haul_0))
    dlog_f = float(np.log(fleet_1 / fleet_0))
    dlog_s = float(np.log(supply_factor_1 / supply_factor_0))
    volume = elasticity * dlog_v
    distance = elasticity * dlog_h
    fleet = -elasticity * dlog_f
    eff = -elasticity * dlog_s
    residual = total - (volume + distance + fleet + eff)
    return Attribution(total, volume, distance, fleet, eff, residual, elasticity)


@dataclass(frozen=True)
class ExtendedAttribution:
    """Physical attribution plus named market-signal contributions.

    ``residual`` is what is left after the physical components and the
    signals; ``residual_physical`` is the residual of the physical identity
    alone, kept so that both readings can be shown.
    """

    physical: Attribution
    signals: dict[str, float]

    @property
    def residual(self) -> float:
        """Residual after the physical components and the signals."""
        return self.physical.residual - sum(self.signals.values())

    def explained_share(self) -> float:
        """Share of the total move explained by the physical components and the signals."""
        if self.physical.total == 0:
            return float("nan")
        return 1.0 - self.residual / self.physical.total

    def as_dict(self) -> dict[str, float]:
        """Physical fields, one ``signal_<name>`` field per signal, the final and the physical residual."""
        d = self.physical.as_dict()
        d["residual_physical"] = self.physical.residual
        for name, v in self.signals.items():
            d[f"signal_{name}"] = v
        d["residual"] = self.residual
        return d

    def check_sums(self, tol: float = 1e-9) -> bool:
        """Physical components, signals and residual add back to the total."""
        p = self.physical
        s = p.volume + p.distance + p.fleet + p.effective_supply + sum(self.signals.values()) + self.residual
        return abs(s - p.total) < tol


def log_points_to_pct(x: float) -> float:
    """Convert a log change to a percentage change."""
    return float(np.expm1(x) * 100.0)
