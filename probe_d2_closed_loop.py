#!/usr/bin/env python3
"""Run fail-closed conformance probes for the four D2 Agent routes.

This is intentionally separate from the historical fixed-action replay
probes.  It sends a new action after an observation has already been returned,
and records whether the native backend preserves that live state.  It emits
evidence only after every check passes; runtime/import errors produce a
pending report and never a synthetic observation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from unified_compiler.d2_closed_loop import D2ClosedLoopAdapter, make_d2_backend

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "generated" / "d2_closed_loop_v1"
SCHEMA = "d2-closed-loop-conformance-v1"
ROUTES = {
    "energyplus_iaq": {"dt_seconds": 600.0, "actions": (0.0, 1.0)},
    "wntr_residential_water": {"dt_seconds": 3600.0, "actions": (0.0, 1.0)},
    "fds_smoke_fire": {"dt_seconds": 1.0, "actions": (0.0, 1.0)},
    "modelica_buildings_aixlib": {"dt_seconds": 60.0, "actions": (0.0, 1.0)},
}


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _finite(value: Any) -> bool:
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    if isinstance(value, dict):
        return all(_finite(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return all(_finite(v) for v in value)
    return True


def probe(route_id: str) -> dict[str, Any]:
    spec = ROUTES[route_id]
    adapter: D2ClosedLoopAdapter | None = None
    checks = {
        "reset_deterministic": False,
        "time_monotone": False,
        "state_continuous": False,
        "mid_trajectory_action_switch": False,
        "illegal_action_fail_closed": False,
    }
    try:
        adapter = make_d2_backend(route_id)
        first = adapter.reset(seed=0)
        first_again = adapter.reset(seed=0)
        checks["reset_deterministic"] = first["observation"] == first_again["observation"]
        legal = adapter.legal_actions()
        a0, a1 = spec["actions"]
        left = adapter.step(a0, spec["dt_seconds"])
        right = adapter.step(a1, spec["dt_seconds"])
        checks["time_monotone"] = left["time_seconds"] > first["time_seconds"] and right["time_seconds"] > left["time_seconds"]
        checks["state_continuous"] = adapter.observe() == right["observation"] and _finite(right["observation"])

        # Compare a same-prefix trajectory whose second command is changed.
        adapter.reset(seed=0)
        adapter.step(a0, spec["dt_seconds"])
        switched = adapter.step(a1, spec["dt_seconds"])
        adapter.reset(seed=0)
        adapter.step(a0, spec["dt_seconds"])
        unchanged = adapter.step(a0, spec["dt_seconds"])
        checks["mid_trajectory_action_switch"] = switched["observation"] != unchanged["observation"]

        try:
            adapter.step(2.0, spec["dt_seconds"])
        except (ValueError, RuntimeError, TypeError):
            checks["illegal_action_fail_closed"] = True
        passed = all(checks.values()) and route_id != "fds_smoke_fire"
        return {
            "schema_version": SCHEMA,
            "route_id": route_id,
            "passed": passed,
            "interactive_step_verified": passed,
            "interactive_conformance_gate": passed,
            "interactive_execution_mode": {
                "energyplus_iaq": "persistent_callback_barrier",
                "wntr_residential_water": "persistent_session_native_hydraulic_step",
                "fds_smoke_fire": "full_history_real_backend_replay_not_online",
                "modelica_buildings_aixlib": "persistent_fmi2_do_step",
            }[route_id],
            "agent_closed_loop": {
                "interface": ["reset(seed)->receipt", "observe()->observation", "legal_actions()->schema", "step(action, dt_seconds)->transition"],
                "dt_seconds": spec["dt_seconds"],
                "legal_actions": legal,
            },
            "interactive_deterministic_reset": checks["reset_deterministic"],
            "interactive_time_monotone": checks["time_monotone"],
            "interactive_state_continuity": checks["state_continuous"],
            "mid_trajectory_action_switch_verified": checks["mid_trajectory_action_switch"],
            "interactive_illegal_action_fail_closed": checks["illegal_action_fail_closed"],
            "checks": checks,
            "adapter_sha256": sha256(_adapter_path(route_id)),
        }
    except Exception as exc:
        return {
            "schema_version": SCHEMA,
            "route_id": route_id,
            "passed": False,
            "interactive_step_verified": False,
            "interactive_conformance_gate": False,
            "checks": checks,
            "error": f"{type(exc).__name__}: {exc}",
            "adapter_sha256": sha256(_adapter_path(route_id)),
        }
    finally:
        if adapter is not None:
            adapter.close()


def _adapter_path(route_id: str) -> Path:
    return {
        "energyplus_iaq": ROOT / "d2_humidity_air_quality_adapter.py",
        "wntr_residential_water": ROOT / "unified_compiler" / "adapters" / "d2_wntr.py",
        "fds_smoke_fire": ROOT / "d2_fds_adapter.py",
        "modelica_buildings_aixlib": ROOT / "d2_modelica_buildings_aixlib_adapter.py",
    }[route_id]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--route", choices=[*ROUTES, "all"], default="all")
    args = parser.parse_args()
    routes = list(ROUTES) if args.route == "all" else [args.route]
    BASE.mkdir(parents=True, exist_ok=True)
    for route_id in routes:
        path = BASE / f"{route_id}.json"
        path.write_text(json.dumps(probe(route_id), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(BASE), "routes": routes}, sort_keys=True))


if __name__ == "__main__":
    main()
