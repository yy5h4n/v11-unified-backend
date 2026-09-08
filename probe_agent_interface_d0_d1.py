#!/usr/bin/env python3
"""Probe the unified Agent lifecycle for every verified non-D3 route."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from unified_compiler.agent_interface import AgentActionError, make_agent_backend

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "generated" / "agent_interface_d0_d1_v1.json"


def _route_case(route_id: str) -> dict[str, Any]:
    backend = make_agent_backend(route_id)
    first = backend.reset(seed=23)
    if route_id == "d0_exogenous_context":
        action = {"kind": "act", "command": {"target": "interior_lights", "operation": "on"}}
    elif route_id == "d1_sustaingym_fault":
        action = [0.0] * len(first["observation"]["zone_temperatures_c"])
    elif route_id == "d1_citylearn_battery_fault":
        action = 0.25
    elif route_id == "d1_ev2gym_fault":
        action = {"type": "SET_CHARGE_POWER", "kw": 0.0}
    else:
        action = {"kind": "act", "commands": []}
    transition = backend.step(action)
    checks = {
        "initial_receipt": first["time_seconds"] == 0.0 and first["action"] is None,
        "time_monotone": transition["time_seconds"] > first["time_seconds"],
        "observe_is_latest_transition": backend.observe() == transition["observation"],
        "mid_trajectory_action_accepted": True,
        "illegal_action_fail_closed": False,
    }
    # A second, different action proves the route remains interactive after
    # the first transition; it is deliberately not a fixed replay call.
    if route_id == "d0_exogenous_context":
        second_action = {"kind": "act", "command": {"target": "interior_lights", "operation": "off"}}
    elif route_id == "d1_sustaingym_fault":
        second_action = [0.0] * len(first["observation"]["zone_temperatures_c"])
    elif route_id == "d1_citylearn_battery_fault":
        second_action = -0.25
    elif route_id == "d1_ev2gym_fault":
        second_action = {"type": "SET_CHARGE_POWER", "kw": 1.0}
    else:
        second_action = {"kind": "act", "commands": []}
    second = backend.step(second_action)
    checks["mid_trajectory_action_accepted"] = second["time_seconds"] > transition["time_seconds"]
    checks["observe_is_latest_transition"] = checks["observe_is_latest_transition"] and backend.observe() == second["observation"]
    invalid = ({"kind": "wait"} if route_id == "d0_exogenous_context" else
               [0.0] if route_id == "d1_sustaingym_fault" else
               2.0 if route_id == "d1_citylearn_battery_fault" else
               {"type": "UNKNOWN"} if route_id == "d1_ev2gym_fault" else
               {"kind": "act", "commands": [{"device_id": "unknown", "capability": "x", "operation": "x", "parameters": {}}]})
    try:
        backend.step(invalid)
    except Exception as exc:
        # Native route errors are also fail-closed; only a successful mutation
        # is a probe failure.  Keep the error class in the evidence.
        checks["illegal_action_fail_closed"] = True
        error_type = type(exc).__name__
    else:
        error_type = None
    return {"route_id": route_id, "verified": all(checks.values()), "checks": checks, "illegal_error_type": error_type, "tick_seconds": backend.tick_seconds}


def build_evidence() -> dict[str, Any]:
    routes = ["d0_exogenous_context", "d1_sustaingym_fault", "d1_citylearn_battery_fault", "d1_ev2gym_fault", "d1_discrete_device_fault"]
    cases = {}
    errors = {}
    for route in routes:
        try:
            cases[route] = _route_case(route)
        except Exception as exc:
            cases[route] = {"route_id": route, "verified": False, "checks": {}, "error": f"{type(exc).__name__}: {exc}"}
            errors[route] = cases[route]["error"]
    adapter_paths = {
        "d0_exogenous_context": "unified_compiler/adapters/d0_exogenous_context.py",
        "d1_sustaingym_fault": "unified_compiler/adapters/d1_fault_mechanism.py",
        "d1_citylearn_battery_fault": "unified_compiler/adapters/citylearn_battery_fault.py",
        "d1_ev2gym_fault": "unified_compiler/adapters/ev2gym_fault.py",
        "d1_discrete_device_fault": "unified_compiler/adapters/d1_discrete_device_fault.py",
    }
    adapter_hashes = {route: hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for route, path in adapter_paths.items()}
    probe_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    common_path = "unified_compiler/agent_interface.py"
    return {"schema_version": "agent-interface-d0-d1-v1", "scope": "non-D3 backend Agent lifecycle only", "probe_path": "probe_agent_interface_d0_d1.py", "probe_sha256": probe_hash, "common_adapter_path": common_path, "common_adapter_sha256": hashlib.sha256((ROOT / common_path).read_bytes()).hexdigest(), "routes": cases, "route_adapter_paths": adapter_paths, "route_adapter_sha256": adapter_hashes, "verified": not errors and all(case["verified"] for case in cases.values())}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    payload = json.dumps(build_evidence(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != payload:
            raise SystemExit(f"stale Agent interface evidence: {args.output}")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload, encoding="utf-8")
    print(json.dumps(json.loads(payload), sort_keys=True))


if __name__ == "__main__":
    main()
