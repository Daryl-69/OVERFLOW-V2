"""Headline numbers (README §6): across many simulated outbreaks, how often
does Upstream raise an alert, and how close does its dig spot get to the
real breach — with the utility's pipe map and with none?

Every run draws a fresh ward layout, households, hydraulics and breach.
Breaches are drawn over all pipes (weighted by risk), including ones with no
participating home downstream, which no method could see. Localisation is
scored on *detected* outbreaks: a red alert was raised and at least one home
downstream of the real breach reported an amber or red reading. Alerts
without that are counted as false alarms.

Two robustness numbers (IMPLEMENTATION §6):

* ``reports_to_p50`` — how many outbreak photos, in arrival order, the
  locator needs before it is at least 50% sure there is a breach;
* ``false_reports`` — the dig-spot error when 10% extra *fake* red reports
  are injected from homes that are not downstream of the breach (pranks or
  misreads), compared with the clean error on the same runs.
"""

from __future__ import annotations

import copy
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace

import numpy as np

from .locate.levels import apply_levels
from .reports import Report
from .scenario import ScenarioConfig, run_scenario

FALSE_REPORT_FRAC = 0.10


def reports_to_p50(sc, reports: list[Report], cap: int = 80) -> int | None:
    """Fewest arrival-ordered reports giving p_breach >= 0.5 (exact up to 20,
    then in steps of 5 to keep the evaluation affordable)."""
    ordered = sorted(reports, key=lambda r: r.t_obs)
    ks = list(range(1, min(len(ordered), 20) + 1)) + list(range(25, min(len(ordered), cap) + 1, 5))
    for k in ks:
        post = sc.gis.locate(ordered[:k])
        if post is not None and post.p_breach >= 0.5:
            return k
    return None


def false_report_error(sc, reports: list[Report], downstream: set[str], bx: float, by: float,
                       frac: float = FALSE_REPORT_FRAC) -> float | None:
    """Dig-spot error with ``frac`` extra fake red reports from non-downstream homes."""
    rng = np.random.default_rng(sc.cfg.seed + 7919)
    pool = sorted(h.id for h in sc.world.households if h.id not in downstream)
    if not reports or not pool:
        return None
    n_fake = max(1, int(round(frac * len(reports))))
    last = max(reports, key=lambda r: r.cycle)
    fakes = []
    for i, hid in enumerate(rng.choice(pool, size=min(n_fake, len(pool)), replace=False)):
        b = sc.baselines.get(str(hid))
        delay = b.delay_med if b is not None and b.delay_med is not None else 30.0
        fakes.append(Report(id=f"FAKE-{i}", household=str(hid), cycle=last.cycle, supply_start=last.supply_start,
                            t_obs=last.supply_start + delay + float(rng.uniform(-3, 3)), turbidity=0.9,
                            chlorine=0.0, answer="dirty", source="fake"))
    mixed = copy.deepcopy(reports) + fakes
    by_cycle: dict[int, list[Report]] = {}
    for r in mixed:
        by_cycle.setdefault(r.cycle, []).append(r)
    positions = sc.positions()
    for rs in by_cycle.values():
        apply_levels(rs, sc.baselines, positions)
    post = sc.gis.locate(mixed)
    return None if post is None else round(float(post.error_m(bx, by)), 1)


def _one(cfg: ScenarioConfig) -> dict:
    sc = run_scenario(cfg)
    f = sc.final
    reports = sc.outbreak_reports()
    red_cycles = sorted({r.cycle for r in reports if r.level == "red"})
    tree = sc.world.tree
    piece = tree.piece_at(sc.breach.edge, sc.breach.frac)
    nodes = np.asarray([tree.attach(h.edge, h.frac) for h in sc.world.households])
    down = {h.id for h, d in zip(sc.world.households, tree.downstream(np.asarray([piece]), nodes)[0]) if d}
    signal = any(r.household in down and r.level in ("amber", "red") for r in reports)
    out = {
        "seed": cfg.seed,
        "alert": bool(f.get("alert")),
        "true_alert": bool(f.get("alert")) and signal,
        "first_red_cycle": (red_cycles[0] - sc.breach.start_cycle + 1) if red_cycles else None,
        "downstream_homes": int(tree.downstream(np.asarray([piece]), nodes).sum()),
        "complaints": f.get("complaints"),
    }
    if out["true_alert"]:
        bx, by = sc.streets.point_on_edge(sc.breach.edge, sc.breach.frac)
        out["reports_to_p50"] = reports_to_p50(sc, reports)
        out["false_report_error_m"] = false_report_error(sc, reports, down, bx, by)
    for net in ("gis", "rebuilt"):
        if net in f:
            g = f[net]
            out[net] = {"error_m": g["error_m"], "truth_in_region": g["truth_in_region"],
                        "region_m": g["region_length_m"], "ring_m": g["ring"]["radius_m"]}
    return out


def _summ(rows: list[dict], net: str) -> dict:
    v = [r[net] for r in rows if net in r]
    if not v:
        return {}
    err = np.asarray([x["error_m"] for x in v])
    return {
        "median_error_m": round(float(np.median(err)), 1),
        "p90_error_m": round(float(np.percentile(err, 90)), 1),
        "within_50m_rate": round(float(np.mean(err <= 50)), 3),
        "truth_in_region_rate": round(float(np.mean([x["truth_in_region"] for x in v])), 3),
        "median_region_m": round(float(np.median([x["region_m"] for x in v])), 1),
        "median_ring_m": round(float(np.median([x["ring_m"] for x in v])), 1),
    }


def evaluate(runs: int = 60, seed0: int = 1000, workers: int | None = None, **cfg_kw) -> dict:
    base = ScenarioConfig(frames=False, **cfg_kw)
    cfgs = [replace(base, seed=seed0 + i) for i in range(runs)]
    workers = workers or max(1, min(os.cpu_count() or 1, 8))
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            rows = list(ex.map(_one, cfgs))
    else:
        rows = [_one(c) for c in cfgs]
    alerted = [r for r in rows if r["alert"]]
    detected = [r for r in rows if r["true_alert"]]
    detectable = [r for r in rows if r["downstream_homes"] > 0]
    first = [r["first_red_cycle"] for r in detected if r["first_red_cycle"] is not None]
    tick = [r["complaints"]["first_ticket_error_m"] for r in detected
            if r["complaints"] and r["complaints"]["first_ticket_error_m"] is not None]
    cent = [r["complaints"]["centroid_error_m"] for r in detected
            if r["complaints"] and r["complaints"]["centroid_error_m"] is not None]
    return {
        "runs": runs,
        "outbreak_cycles": base.outbreak_cycles,
        "min_downstream_homes": base.min_downstream_homes,
        "households": base.n_households,
        "ensemble": base.ensemble,
        "ensemble_mode": base.ensemble_mode,
        "alerts": len(alerted),
        "detectable": len(detectable),
        "detected": len(detected),
        "detection_rate": round(len(detected) / max(len(detectable), 1), 3),
        "false_alarms": len(alerted) - len(detected),
        "false_alarm_rate": round((len(alerted) - len(detected)) / runs, 3),
        "alert_in_first_supply_rate": round(float(np.mean([c == 1 for c in first])) if first else 0.0, 3),
        "gis": _summ(detected, "gis"),
        "rebuilt": _summ(detected, "rebuilt"),
        "complaints": {
            "median_first_ticket_error_m": round(float(np.median(tick)), 1) if tick else None,
            "median_centroid_error_m": round(float(np.median(cent)), 1) if cent else None,
        },
        "reports_to_p50": _p50(detected),
        "false_reports": _false(detected),
        "rows": rows,
    }


def _p50(rows: list[dict]) -> dict:
    v = [r["reports_to_p50"] for r in rows if r.get("reports_to_p50") is not None]
    return {"median": float(np.median(v)) if v else None, "p90": float(np.percentile(v, 90)) if v else None,
            "reached_rate": round(len(v) / max(len(rows), 1), 3)}


def _false(rows: list[dict]) -> dict:
    pairs = [(r["gis"]["error_m"], r["false_report_error_m"]) for r in rows
             if "gis" in r and r.get("false_report_error_m") is not None]
    if not pairs:
        return {"fraction": FALSE_REPORT_FRAC, "runs": 0}
    clean, noisy = np.asarray(pairs).T
    return {"fraction": FALSE_REPORT_FRAC, "runs": len(pairs),
            "median_error_clean_m": round(float(np.median(clean)), 1),
            "median_error_with_false_m": round(float(np.median(noisy)), 1),
            "within_50m_rate_with_false": round(float(np.mean(noisy <= 50)), 3)}
