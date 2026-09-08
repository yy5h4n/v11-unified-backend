#!/usr/bin/env python3
"""Replay-gate mined CityLearn battery/PV processes with trajectory scoring."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
from typing import Any, Callable

from probe_citylearn_battery_replay import (
    ROOT,
    building_battery_runtime,
    canonical_digest,
    require_assets,
    shared_sizing_catalogs,
)


INITIAL_SOC = 0.25
TERMINAL_RESERVE_SOC = 0.20
WITNESS_RESERVE_SOC = 0.21
MIN_IMPORT_IMPROVEMENT_KWH = 0.25


Policy = Callable[[float, float, Any, int, int], float]


def idle_policy(load: float, solar: float, battery: Any, step: int, steps: int) -> float:
    return 0.0


def self_consumption_policy(
    load: float, solar: float, battery: Any, step: int, steps: int
) -> float:
    """Feasibility witness only; it is never exposed as a gold action trace."""
    net_without_storage = load - solar
    if net_without_storage < 0.0:
        return min(-net_without_storage / battery.nominal_power, 1.0)
    usable_energy = max(
        battery.energy_init - WITNESS_RESERVE_SOC * battery.capacity,
        0.0,
    )
    discharge_limit = usable_energy * battery.round_trip_efficiency
    discharge = min(net_without_storage, battery.nominal_power, discharge_limit)
    return -discharge / battery.nominal_power


def replay(building_id: str, start: int, steps: int, policy: Policy) -> dict[str, Any]:
    runtime = building_battery_runtime(building_id)
    battery = deepcopy(runtime["battery_template"])
    battery.reset()
    battery.force_set_soc(INITIAL_SOC)
    records: list[dict[str, float | int]] = []
    for local_step in range(steps):
        source_step = start + local_step
        load = float(runtime["load"][source_step])
        solar = float(runtime["solar"][source_step])
        action = float(policy(load, solar, battery, local_step, steps))
        if not -1.0 <= action <= 1.0:
            raise RuntimeError(f"policy emitted illegal battery action: {action}")
        battery.charge(action * battery.nominal_power)
        storage = float(battery.electricity_consumption[local_step])
        records.append(
            {
                "source_step": source_step,
                "action": action,
                "soc": float(battery.soc[local_step]),
                "load": load,
                "solar": solar,
                "storage_electricity": storage,
                "net_electricity": load - solar + storage,
            }
        )
        if local_step + 1 < steps:
            battery.next_time_step()
    return {
        "terminated": len(records) == steps,
        "records": records,
        "trajectory_digest": canonical_digest(records),
    }


def trajectory_score(replay_result: dict[str, Any]) -> dict[str, float | int]:
    records = replay_result["records"]
    hard_violations = sum(not (0.0 <= row["soc"] <= 1.0) for row in records)
    terminal_soc = float(records[-1]["soc"])
    terminal_deficit = max(TERMINAL_RESERVE_SOC - terminal_soc, 0.0)
    grid_import = sum(max(float(row["net_electricity"]), 0.0) for row in records)
    grid_export = sum(max(-float(row["net_electricity"]), 0.0) for row in records)
    return {
        "hard_violation_count": hard_violations,
        "terminal_soc": terminal_soc,
        "terminal_reserve_deficit": terminal_deficit,
        "grid_import_kwh": grid_import,
        "grid_export_kwh": grid_export,
    }


def validate_process(process: dict[str, Any]) -> dict[str, Any]:
    building_id = process["building_id"]
    start = int(process["source_start_row"])
    steps = int(process["source_end_row"]) - start + 1
    idle_a = replay(building_id, start, steps, idle_policy)
    idle_b = replay(building_id, start, steps, idle_policy)
    witness_a = replay(building_id, start, steps, self_consumption_policy)
    witness_b = replay(building_id, start, steps, self_consumption_policy)
    deterministic = idle_a == idle_b and witness_a == witness_b
    idle_score = trajectory_score(idle_a)
    witness_score = trajectory_score(witness_a)
    improvement = idle_score["grid_import_kwh"] - witness_score["grid_import_kwh"]
    passed = (
        deterministic
        and idle_a["terminated"]
        and witness_a["terminated"]
        and witness_score["hard_violation_count"] == 0
        and witness_score["terminal_reserve_deficit"] <= 1e-9
        and improvement >= MIN_IMPORT_IMPROVEMENT_KWH
    )
    return {
        "process_id": process["process_id"],
        "building_id": building_id,
        "window": {"start": start, "end": start + steps - 1, "steps": steps},
        "deterministic": deterministic,
        "terminated": idle_a["terminated"] and witness_a["terminated"],
        "idle_score": idle_score,
        "feasibility_witness_score": witness_score,
        "grid_import_improvement_kwh": improvement,
        "trajectory_digests": {
            "idle": idle_a["trajectory_digest"],
            "feasibility_witness": witness_a["trajectory_digest"],
        },
        "passed": passed,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--process-pool",
        type=Path,
        default=ROOT / "generated" / "battery_pv_process_pool.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "generated" / "battery_pv_process_replay_gate.json",
    )
    args = parser.parse_args()
    require_assets()
    process_pool = json.loads(args.process_pool.read_text(encoding="utf-8"))
    with shared_sizing_catalogs():
        reports = [validate_process(process) for process in process_pool["processes"]]
    failed = [report for report in reports if not report["passed"]]
    report = {
        "schema_version": "v11-battery-pv-replay-gate-1",
        "process_pool_digest": process_pool["process_pool_digest"],
        "process_count": len(reports),
        "passed_count": len(reports) - len(failed),
        "failed_count": len(failed),
        "all_passed": not failed,
        "initial_soc": INITIAL_SOC,
        "terminal_reserve_soc": TERMINAL_RESERVE_SOC,
        "feasibility_witness_reserve_soc": WITNESS_RESERVE_SOC,
        "minimum_grid_import_improvement_kwh": MIN_IMPORT_IMPROVEMENT_KWH,
        "gold_actions_released": False,
        "processes": reports,
    }
    payload = json.dumps(report, sort_keys=True, separators=(",", ":"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + ".part")
    temporary.write_text(payload + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(
        json.dumps(
            {
                "process_count": report["process_count"],
                "passed_count": report["passed_count"],
                "failed_count": report["failed_count"],
                "all_passed": report["all_passed"],
                "output": str(args.output),
            },
            sort_keys=True,
        )
    )
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
