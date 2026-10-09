#!/usr/bin/env python
"""Seed DynamoDB with 12 normal supply cycles from the Bhagirathpura ward.

Usage:
    # Local (DynamoDB Local or real AWS credentials):
    python -m aws.seed_baselines

    # Against a deployed stack:
    UPSTREAM_READINGS_TABLE=upstream-ReadingsTable-xxx \
    UPSTREAM_META_TABLE=upstream-MetaTable-xxx \
    python -m aws.seed_baselines

The script loads the same ward pickle the Lambda uses, simulates 12 normal
supply cycles (no outbreak), and writes the readings into DynamoDB so that
the live photo scoring has baselines to compare against.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Ensure the project root is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from upstream.scenario import ScenarioConfig, run_scenario
from upstream.sim.streets import load_streets


def main():
    # 1. Load the Bhagirathpura scenario config
    scenario_path = Path(__file__).resolve().parent.parent / "data" / "scenarios" / "bhagirathpura.json"
    if not scenario_path.exists():
        print(f"ERROR: scenario not found at {scenario_path}", file=sys.stderr)
        return 1

    data = json.loads(scenario_path.read_text())
    cfg_dict = dict(data["meta"]["config"])
    cfg_dict["strengths"] = tuple(cfg_dict["strengths"])
    cfg = ScenarioConfig(**cfg_dict)

    # Override: no outbreak, just normal baseline cycles
    cfg.outbreak_cycles = 0
    cfg.frames = False

    print(f"Running {cfg.baseline_cycles} baseline cycles on {cfg.streets}...")
    sc = run_scenario(cfg)

    # 2. Connect to DynamoDB
    try:
        import boto3
    except ImportError:
        print("ERROR: boto3 not installed. Run: pip install boto3", file=sys.stderr)
        return 1

    readings_table = os.environ.get("UPSTREAM_READINGS_TABLE", "UpstreamReadings")
    meta_table = os.environ.get("UPSTREAM_META_TABLE", "UpstreamMeta")

    dynamodb = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1"))

    from upstream.intake.dynamo import DynamoStore
    store = DynamoStore(resource=dynamodb, readings=readings_table, meta=meta_table)

    # 3. Seed supply cycles and readings from the scenario
    epoch = datetime.now(timezone.utc).isoformat()
    reports = sc.reports
    if not reports:
        print("WARNING: no reports generated", file=sys.stderr)
        return 1

    # Group by cycle
    cycles = {}
    for r in reports:
        cycles.setdefault(r.cycle, []).append(r)

    total = 0
    for cycle_num in sorted(cycles.keys()):
        cycle_reports = cycles[cycle_num]
        supply_start = cycle_reports[0].supply_start
        started_at = epoch

        # Register the supply cycle
        db_cycle = store.add_supply(supply_start, started_at)
        print(f"  Cycle {db_cycle}: {len(cycle_reports)} readings")

        for r in cycle_reports:
            # Assign a report ID
            r.id = store.next_report_id(db_cycle)
            r.cycle = db_cycle
            store.add_report(r, received_at=epoch)
            total += 1

    print(f"\nSeeded {total} readings across {len(cycles)} supply cycles into DynamoDB.")
    print(f"  Readings table: {readings_table}")
    print(f"  Meta table:     {meta_table}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
