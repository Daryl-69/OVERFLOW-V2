"""The live ward: households, pipe map, learned baselines and travel times.

Live intake scores real reports against the ward a scenario was built on,
rebuilt *without* an outbreak. Building it runs the simulator (about a minute
on the Indore grid), so the AWS image builds it once at image-build time and
pickles it; the Lambdas unpickle it on cold start.

    python -m upstream.ward data/scenarios/bhagirathpura.json ward.pkl
"""

from __future__ import annotations

import json
import pickle
import sys
from pathlib import Path

from .scenario import Scenario, ScenarioConfig, run_scenario

WARD_FORMAT = 1


def scenario_config(data: dict) -> ScenarioConfig:
    cfg = dict(data["meta"]["config"])
    cfg["strengths"] = tuple(cfg["strengths"])
    return ScenarioConfig(**cfg)


def build_live_ward(data: dict) -> Scenario:
    """Rebuild the scenario's ward with no outbreak and no replay frames."""
    cfg = scenario_config(data)
    cfg.outbreak_cycles = 0
    cfg.frames = False
    return run_scenario(cfg)


def save_ward(ward: Scenario, path: str | Path) -> None:
    Path(path).write_bytes(pickle.dumps({"format": WARD_FORMAT, "ward": ward}, protocol=pickle.HIGHEST_PROTOCOL))


def load_ward(path: str | Path) -> Scenario:
    # Only ever load pickles this project built (bundled in the image or a
    # private bucket): unpickling untrusted data executes code.
    blob = pickle.loads(Path(path).read_bytes())  # noqa: S301
    if blob.get("format") != WARD_FORMAT:
        raise ValueError(f"ward pickle format {blob.get('format')} != {WARD_FORMAT}; rebuild it")
    return blob["ward"]


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print("usage: python -m upstream.ward SCENARIO.json OUT.pkl", file=sys.stderr)
        return 2
    ward = build_live_ward(json.loads(Path(argv[0]).read_text()))
    save_ward(ward, argv[1])
    print(f"wrote {argv[1]}: {len(ward.world.households)} households")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
