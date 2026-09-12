#!/usr/bin/env python3
"""Small native diagnostics for frozen evidence-query items.

Policies are named counterfactual probes, not gold action sequences.  The tool
never calls an LLM and never edits observations or future event schedules.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harness_v2.core import EpisodeSpec
from unified_compiler.adapters.d1_discrete_device_fault import (
    DiscreteFaultSchedule,
    DiscreteFaultWindow,
)
from unified_compiler.agent_interface import make_agent_backend


CHECK_TIME_SECONDS = 240
DEADLINE_SECONDS = 300
HORIZON_SECONDS = 600
CLOSE = {
    "device_id": "garage_door.main",
    "capability": "garage.door",
    "operation": "close",
    "parameters": {},
}


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _backend():
    episode = EpisodeSpec(
        "validation-garage-daily-check",
        {"scenario_type": "post_parking_garage_closure", "horizon_seconds": HORIZON_SECONDS},
        23,
    )
    # The fault is deliberately private and is not needed to satisfy the task:
    # it merely prevents an unsafe assumption that the initial state is always
    # actionable.  It ends before the public check time.
    schedule = DiscreteFaultSchedule((
        DiscreteFaultWindow("garage_door.main", 0, 3, "jammed"),
    ))
    return make_agent_backend("d1_discrete_device_fault", schedule=schedule, episode_spec=episode)


def _evaluate(samples):
    if len(samples) != HORIZON_SECONDS // 60 + 1:
        return {"evaluated": False, "task_success": None, "reason": "incomplete_native_samples"}
    for index, sample in enumerate(samples):
        if sample["time_seconds"] != index * 60:
            return {"evaluated": False, "task_success": None, "reason": "invalid_native_clock"}
    check = samples[CHECK_TIME_SECONDS // 60]["observation"]["devices"]["garage_door.main"]["state"]
    deadline = samples[DEADLINE_SECONDS // 60]["observation"]["devices"]["garage_door.main"]["state"]
    passed = deadline == "closed"
    return {
        "evaluated": True,
        "task_success": passed,
        "check_time_seconds": CHECK_TIME_SECONDS,
        "response_deadline_seconds": DEADLINE_SECONDS,
        "state_at_check": check,
        "state_at_deadline": deadline,
        "scope": "one disclosed daily-check opportunity; later reopening is outside this calibration query",
    }


def _run(policy):
    backend = _backend()
    try:
        current = backend.reset(seed=23)
        samples = [deepcopy(current)]
        actions = []
        while not current["done"]:
            action = {"kind": "act", "commands": []}
            if policy == "scheduled_close" and current["time_seconds"] == CHECK_TIME_SECONDS:
                door = current["observation"]["devices"]["garage_door.main"]
                if door["state"] == "open" and door.get("availability", "available") == "available":
                    action = {"kind": "act", "commands": [deepcopy(CLOSE)]}
            current = backend.step(action, dt_seconds=60)
            actions.append(deepcopy(action))
            samples.append(deepcopy(current))
        return {"policy": policy, "samples": samples, "actions": actions, "evaluation": _evaluate(samples)}
    finally:
        backend.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = {
        "schema": "evidence-query-native-diagnostic.v1",
        "item_id": "validation-garage-daily-check-01",
        "query": "At the daily check time in the task conditions, if the garage door is open, close it.",
        "route_id": "d1_discrete_device_fault",
        "seed": 23,
        "public_conditions": {
            "daily_check_time_seconds": CHECK_TIME_SECONDS,
            "close_response_deadline_seconds": DEADLINE_SECONDS,
            "native_cadence_seconds": 60,
            "horizon_seconds": HORIZON_SECONDS,
            "parameter_origin": "authored simulation condition; source requests a user-chosen time but supplies no clock value",
        },
        "runs": [_run("idle"), _run("scheduled_close")],
        "provider_calls": 0,
        "formal_benchmark_admission": False,
        "scope": "native calibration feasibility and idle contrast; not proof of dynamic adaptation or model ability",
    }
    result["diagnostic_passed"] = (
        result["runs"][0]["evaluation"]["task_success"] is False
        and result["runs"][1]["evaluation"]["task_success"] is True
    )
    payload = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "sha256": sha256(payload.encode()).hexdigest(),
        "diagnostic_passed": result["diagnostic_passed"],
        "evaluations": [run["evaluation"]["task_success"] for run in result["runs"]],
    }))


if __name__ == "__main__":
    main()
