"""The incident-open rule (README §5.8).

A single red reading alerts the household, but an *incident* — which starts
the incident workflow, wakes the agent and asks an engineer for approval — is
opened only when the evidence is strong enough to justify a ward-wide action:

* at least ``MIN_RED`` usable red readings in the current window whose homes
  lie within ``CLUSTER_RADIUS_M`` of one another, and
* the locator's probability that there is a breach at all is at least
  ``MIN_P_BREACH``.

This module is dependency-free on purpose: the live service, the AWS locate
Lambda and the agent all import it, so it must not import any of them.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field

MIN_RED = 3
CLUSTER_RADIUS_M = 1500.0
MIN_P_BREACH = 0.25


def pct(p: float | None, digits: int = 0) -> str:
    """0.734 -> '73%' (``None`` -> 'n/a')."""
    if p is None:
        return "n/a"
    return f"{100.0 * float(p):.{digits}f}%"


@dataclass
class IncidentDecision:
    open: bool
    reason: str
    red_households: list[str] = field(default_factory=list)
    cluster: list[str] = field(default_factory=list)
    p_breach: float | None = None

    def to_dict(self) -> dict:
        return {"open": self.open, "reason": self.reason, "red_households": self.red_households,
                "cluster": self.cluster, "p_breach": self.p_breach,
                "rule": {"min_red": MIN_RED, "radius_m": CLUSTER_RADIUS_M, "min_p_breach": MIN_P_BREACH}}


def red_cluster(red: list[str], positions: dict[str, tuple[float, float]],
                radius_m: float = CLUSTER_RADIUS_M) -> list[str]:
    """The largest set of red homes all within ``radius_m`` of one anchor home
    (each red home is tried as the anchor; ties keep the first found)."""
    homes = sorted({h for h in red if h in positions})
    best: list[str] = []
    for a in homes:
        ax, ay = positions[a]
        near = [h for h in homes if math.hypot(positions[h][0] - ax, positions[h][1] - ay) <= radius_m]
        if len(near) > len(best):
            best = near
    return best


def decide(red_households: list[str], positions: dict[str, tuple[float, float]], p_breach: float | None,
           min_red: int = MIN_RED, radius_m: float = CLUSTER_RADIUS_M,
           min_p: float = MIN_P_BREACH) -> IncidentDecision:
    """Apply the incident-open rule to the current window's usable red homes."""
    red = sorted(set(red_households))
    cluster = red_cluster(red, positions, radius_m)
    if len(cluster) < min_red:
        return IncidentDecision(False, f"{len(cluster)} red home(s) within {radius_m / 1000:g} km; need {min_red}",
                                red, cluster, p_breach)
    if p_breach is None or p_breach < min_p:
        return IncidentDecision(False, f"breach probability {pct(p_breach)} is below {pct(min_p)}",
                                red, cluster, p_breach)
    return IncidentDecision(True, f"{len(cluster)} red homes within {radius_m / 1000:g} km and breach "
                                  f"probability {pct(p_breach)}", red, cluster, p_breach)


def incident_id(cycle: int, epoch_iso: str) -> str:
    """Stable per supply cycle, so repeated re-scoring never opens duplicates."""
    h = hashlib.sha1(f"{epoch_iso}|{cycle}".encode()).hexdigest()[:6]
    return f"INC-{cycle:03d}-{h}"
