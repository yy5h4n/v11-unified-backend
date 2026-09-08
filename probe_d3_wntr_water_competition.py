#!/usr/bin/env python3
"""Probe and pin evidence for the D3 WNTR water-competition route."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from d3_wntr_water_competition_adapter import (
    D3WNTRDependencyError,
    D3WNTRWaterCompetitionRoute,
    HORIZON_SECONDS,
    ROOT,
    TICK_SECONDS,
    dependency_status,
    sha256,
)

BASE = ROOT / "generated/d3_wntr_water_competition_v1"
REPORT = BASE / "gate_report.json"


def _trace(name: str, transitions: list[dict[str, Any]]) -> dict[str, Any]:
    BASE.mkdir(parents=True, exist_ok=True)
    path = BASE / f"{name}.jsonl"
    path.write_text("".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in transitions), encoding="utf-8")
    return {"path": str(path.relative_to(ROOT)), "row_count": len(transitions), "sha256": sha256(path)}


def _run(actions: list[dict[str, float]], seed: int = 0) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    route = D3WNTRWaterCompetitionRoute()
    try:
        initial = route.reset(seed)
        transitions = [route.step(action) for action in actions]
        return transitions, {"initial": initial, "legal_actions": route.legal_actions()}
    finally:
        route.close()


def _delta(left: list[dict[str, Any]], right: list[dict[str, Any]], field: str) -> float:
    return max(abs(a["observation"][field] - b["observation"][field]) for a, b in zip(left, right))


def build() -> dict[str, Any]:
    try:
        status = dependency_status()
        if not status.get("wntr_importable"):
            raise D3WNTRDependencyError(status.get("import_error", "WNTR unavailable"))
        both = {"shower_valve_open": 1.0, "laundry_valve_open": 1.0}
        a_closed = {"shower_valve_open": 0.0, "laundry_valve_open": 1.0}
        b_closed = {"shower_valve_open": 1.0, "laundry_valve_open": 0.0}
        baseline, meta = _run([both] * 2)
        shower_closed, _ = _run([a_closed] * 2)
        laundry_closed, _ = _run([b_closed] * 2)
        baseline_repeat, _ = _run([both] * 2)
    except (D3WNTRDependencyError, RuntimeError, OSError) as exc:
        BASE.mkdir(parents=True, exist_ok=True)
        return {
            "schema_version": "d3-wntr-water-competition-gate-v1",
            "backend": "WNTR",
            "status": "FAIL_CLOSED",
            "passed": False,
            "evidence_pending": True,
            "exclusion_reasons": ["WNTR_RUNTIME_UNAVAILABLE"],
            "error": str(exc),
        }

    all_rows = baseline + shower_closed + laundry_closed + baseline_repeat
    finite = all(math.isfinite(value) for row in all_rows for value in row["observation"].values())
    monotone = all(row["time_seconds"] == (i + 1) * TICK_SECONDS for i, row in enumerate(baseline))
    deterministic = baseline == baseline_repeat
    action_sensitivity = (
        _delta(baseline, shower_closed, "pressure_laundry_m") > 1e-9
        and _delta(baseline, laundry_closed, "pressure_shower_m") > 1e-9
    )
    # The two contrasts hold one service open and intervene on the other;
    # they are the bidirectional shared-resource causal gate.
    cross_deltas = {
        "shower_action_to_laundry_pressure_m": _delta(baseline, shower_closed, "pressure_laundry_m"),
        "shower_action_to_laundry_flow_m3_s": _delta(baseline, shower_closed, "flow_laundry_m3_s"),
        "laundry_action_to_shower_pressure_m": _delta(baseline, laundry_closed, "pressure_shower_m"),
        "laundry_action_to_shower_flow_m3_s": _delta(baseline, laundry_closed, "flow_shower_m3_s"),
    }
    action_available = meta["legal_actions"]["native"] and set(meta["legal_actions"]["channels"]) == {
        "shower_valve_open", "laundry_valve_open"
    }
    continuity = all(
        row["delta_t_seconds"] == TICK_SECONDS and row["time_seconds"] > previous
        for previous, row in zip([0.0] + [r["time_seconds"] for r in baseline[:-1]], baseline)
    )
    blockers: list[str] = []
    if not finite: blockers.append("NONFINITE_OBSERVATION")
    if not monotone: blockers.append("TIME_NOT_MONOTONE")
    if not deterministic: blockers.append("CROSS_RESET_REPLAY_MISMATCH")
    if not action_available: blockers.append("ACTION_SPACE_NOT_EXPOSED")
    if not action_sensitivity: blockers.append("BIDIRECTIONAL_SHARED_RESOURCE_GATE_FAILED")
    if not continuity: blockers.append("STATE_CONTINUITY_GATE_FAILED")
    traces = {
        "baseline": _trace("baseline_trace", baseline),
        "shower_closed": _trace("shower_closed_trace", shower_closed),
        "laundry_closed": _trace("laundry_closed_trace", laundry_closed),
    }
    return {
        "schema_version": "d3-wntr-water-competition-gate-v1",
        "physical_process_id": "wntr:d3_shared_water_services",
        "backend": "WNTR",
        "backend_engine": "WNTRSimulator",
        "backend_version": status.get("wntr_version"),
        "status": "REAL_RUNTIME_PROBED" if not blockers else "FAIL_CLOSED",
        "passed": not blockers,
        "evidence_pending": False,
        "runtime_stepping_gate": finite and monotone,
        "action_available_gate": action_available,
        "bidirectional_coupling_gate": action_sensitivity,
        "determinism_gate": deterministic,
        "state_continuity_gate": continuity,
        "action_channels": ["shower_valve_open", "laundry_valve_open"],
        "shared_resource": "municipal reservoir -> elevated house tank -> two pressure-dependent service branches",
        "coupling_semantics": "each service valve changes the shared tank/source hydraulic solution and therefore the other service pressure and served flow",
        "agent_closed_loop": {
            "interface": ["reset(seed)->observation", "observe()->observation", "legal_actions()->schema", "step(action, dt_seconds)->transition", "close()->None"],
            "runtime_mechanism": "one persistent WaterNetworkModel and WNTRSimulator in an isolated worker; one native hydraulic tick per step",
            "physical_step_seconds": TICK_SECONDS,
            "horizon_seconds": HORIZON_SECONDS,
            "mid_run_action_change": True,
            "prefix_rerun_per_step": False,
        },
        "cross_intervention_deltas": cross_deltas,
        "observation_roles": {
            "pressure": "shower/laundry junction pressure head in metres",
            "flow": "native service and municipal source link flow in m3/s",
            "tank": "shared tank water level in metres",
            "served_flow": "pressure-dependent WNTR emitter demand for each service",
        },
        "trace_refs": traces,
        "row_count": len(baseline),
        "exclusion_reasons": blockers,
        "provenance": {
            **status,
            "network_definition": "municipal reservoir + elevated tank + independent shower/laundry TCV branches + pressure-dependent leaks",
            "native_solver": "WNTRSimulator",
            "native_hydraulic_timestep_seconds": TICK_SECONDS,
        },
        "adapter_sha256": sha256(ROOT / "d3_wntr_water_competition_adapter.py"),
        "probe_sha256": sha256(Path(__file__).resolve()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = json.dumps(build(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not REPORT.is_file() or REPORT.read_text(encoding="utf-8") != content:
            raise SystemExit("stale D3 WNTR water competition gate report")
    else:
        BASE.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
