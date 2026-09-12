#!/usr/bin/env python3
"""Execute a frozen all-sample two-room thermal contract on fresh native runs."""

from __future__ import annotations

import argparse
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from query_construction.contracts import compile_contract
from query_construction.temporal import evaluate
from unified_compiler.agent_interface import make_agent_backend


ROUTE = "modelica_buildings_aixlib"
SEED = 23
CONDITIONS = [
    {
        "id": "room_temperature_lower",
        "kind": "invariant",
        "goal": {"quantity": "room.temperature", "unit": "degC", "comparator": "ge", "target": 18},
        "start_seconds": 60,
        "end_seconds": 3600,
        "parameter_origin": "predeclared pilot service band; product source supplies no numeric temperature",
    },
    {
        "id": "room_temperature_upper",
        "kind": "invariant",
        "goal": {"quantity": "room.temperature", "unit": "degC", "comparator": "le", "target": 24},
        "start_seconds": 60,
        "end_seconds": 3600,
        "parameter_origin": "predeclared pilot service band; product source supplies no numeric temperature",
    },
]


def _action(policy, index, observation):
    mean = (observation["room_a_temperature_c"] + observation["room_b_temperature_c"]) / 2
    if policy == "idle":
        return 0.0
    if policy == "one_shot":
        return 0.5 if index == 0 else 0.0
    if policy == "fixed_0_3":
        return 0.3
    if policy == "feedback":
        return min(1.0, max(0.0, 0.4 + 0.25 * (20.0 - mean)))
    raise ValueError(policy)


def _run(policy):
    backend = make_agent_backend(ROUTE)
    try:
        initial = backend.reset(seed=SEED)
        compiled = compile_contract(ROUTE, CONDITIONS, initial["observation"])
        current = initial
        samples = [deepcopy(initial)]
        actions = []
        index = 0
        while not current["done"]:
            action = _action(policy, index, current["observation"])
            current = backend.step(action, dt_seconds=60)
            actions.append(action)
            samples.append(deepcopy(current))
            index += 1
        score = evaluate(compiled["clauses"], samples, cadence_seconds=60, horizon_seconds=3600)
        return {"policy": policy, "actions": actions, "samples": samples, "evaluation": score,
                "temperature_range_c": [
                    min(s["observation"][key] for s in samples[1:] for key in ("room_a_temperature_c", "room_b_temperature_c")),
                    max(s["observation"][key] for s in samples[1:] for key in ("room_a_temperature_c", "room_b_temperature_c")),
                ]}
    finally:
        backend.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    runs = [_run(name) for name in ("idle", "one_shot", "fixed_0_3", "feedback")]
    initial = runs[0]["samples"][0]["observation"]
    public = compile_contract(ROUTE, CONDITIONS, initial)["public_contract"]
    result = {
        "schema": "evidence-query-thermal-diagnostic.v1",
        "item_id": "core-transfer-multiroom-comfort-01",
        "route_id": ROUTE,
        "seed": SEED,
        "query": "Keep both rooms comfortably warm throughout this occupied one-hour period.",
        "public_contract": public,
        "runs": runs,
        "provider_calls": 0,
        "core_contrast_passed": (
            all(run["evaluation"]["task_success"] is False for run in runs[:3])
            and runs[3]["evaluation"]["task_success"] is True
        ),
        "scope": "fresh native same-seed policy contrast; product-derived query, no model ability or population claim",
    }
    payload = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "sha256": sha256(payload.encode()).hexdigest(),
        "core_contrast_passed": result["core_contrast_passed"],
        "outcomes": {run["policy"]: run["evaluation"]["task_success"] for run in runs},
        "ranges_c": {run["policy"]: run["temperature_range_c"] for run in runs},
    }))


if __name__ == "__main__":
    main()
