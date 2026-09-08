#!/usr/bin/env python3
"""Probe and pin evidence for the live EnergyPlus D3 shared-air route."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from d3_energyplus_shared_ventilation_adapter import (
    ACTION_NAMES, HORIZON_STEPS, ROOT, SHARED_CAPACITY_M3_S, TICK_SECONDS,
    D3EnergyPlusError, D3EnergyPlusSharedVentilationRoute, prepare_working_model,
    runtime_provenance, sha256,
)

BASE = ROOT / "generated/d3_energyplus_shared_ventilation_v1"
REPORT = BASE / "gate_report.json"


def _run(action: dict[str, float], steps: int = 6) -> tuple[dict[str, float], list[dict[str, Any]]]:
    route = D3EnergyPlusSharedVentilationRoute()
    try:
        initial = route.reset(seed=0)
        transitions = [route.step(action) for _ in range(steps)]
        return initial, transitions
    finally:
        route.close()


def _delta(left: list[dict[str, Any]], right: list[dict[str, Any]], field: str) -> float:
    return max(abs(a["observation"][field] - b["observation"][field]) for a, b in zip(left, right))


def _trace(name: str, initial: dict[str, float], transitions: list[dict[str, Any]]) -> dict[str, Any]:
    path = BASE / f"{name}.jsonl"
    path.write_text("".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in transitions), encoding="utf-8")
    return {"path": str(path.relative_to(ROOT)), "row_count": len(transitions), "sha256": sha256(path), "initial_observation": initial}


def build() -> dict[str, Any]:
    try:
        source_hash, model_hash = prepare_working_model()
        both = {ACTION_NAMES[0]: 1.0, ACTION_NAMES[1]: 1.0}
        a_closed = {ACTION_NAMES[0]: 0.0, ACTION_NAMES[1]: 1.0}
        b_closed = {ACTION_NAMES[0]: 1.0, ACTION_NAMES[1]: 0.0}
        initial, baseline = _run(both)
        initial_a, a_closed_trace = _run(a_closed)
        initial_b, b_closed_trace = _run(b_closed)
        _, repeat = _run(both)
    except (D3EnergyPlusError, OSError, RuntimeError) as exc:
        BASE.mkdir(parents=True, exist_ok=True)
        return {"schema_version": "d3-energyplus-shared-ventilation-gate-v1", "backend": "EnergyPlus", "status": "FAIL_CLOSED", "passed": False, "evidence_pending": True, "exclusion_reasons": ["ENERGYPLUS_RUNTIME_OR_MODEL_UNAVAILABLE"], "error": str(exc), "provenance": runtime_provenance()}

    rows = baseline + a_closed_trace + b_closed_trace
    finite = all(math.isfinite(v) for trace in rows for v in trace["observation"].values()) and all(math.isfinite(v) for v in initial.values())
    # reset() returns the first completed native callback; therefore the first
    # public step is the second physical zone timestep (t=1800 s).
    monotone = all(row["time_seconds"] == (i + 2) * TICK_SECONDS for i, row in enumerate(baseline))
    continuity = all(row["delta_t_seconds"] == TICK_SECONDS for row in baseline) and all(row["time_seconds"] > prev for prev, row in zip([0.0] + [r["time_seconds"] for r in baseline[:-1]], baseline))
    deterministic = baseline == repeat and initial == initial_a == initial_b
    capacity = all(row["observation"]["zone_a_actual_airflow_m3_s"] + row["observation"]["zone_b_actual_airflow_m3_s"] <= SHARED_CAPACITY_M3_S + 1e-9 for row in rows)
    # Cross intervention: close A while holding B requested, and vice versa.
    cross = {
        "zone_a_request_to_zone_b_airflow_m3_s": _delta(baseline, a_closed_trace, "zone_b_actual_airflow_m3_s"),
        "zone_a_request_to_zone_b_co2_ppm": _delta(baseline, a_closed_trace, "zone_b_co2_ppm"),
        "zone_b_request_to_zone_a_airflow_m3_s": _delta(baseline, b_closed_trace, "zone_a_actual_airflow_m3_s"),
        "zone_b_request_to_zone_a_co2_ppm": _delta(baseline, b_closed_trace, "zone_a_co2_ppm"),
    }
    bidirectional = all(cross[key] > 1e-6 for key in cross)
    blockers = []
    if not finite: blockers.append("NONFINITE_OBSERVATION")
    if not monotone: blockers.append("TIME_NOT_MONOTONE")
    if not continuity: blockers.append("STATE_CONTINUITY_FAILED")
    if not deterministic: blockers.append("RESET_NOT_DETERMINISTIC")
    if not capacity: blockers.append("EMS_SHARED_CAPACITY_NOT_VERIFIED")
    if not bidirectional: blockers.append("BIDIRECTIONAL_CROSS_INTERVENTION_FAILED")
    traces = {"baseline": _trace("baseline_trace", initial, baseline), "a_closed": _trace("a_closed_trace", initial_a, a_closed_trace), "b_closed": _trace("b_closed_trace", initial_b, b_closed_trace)}
    return {
        "schema_version": "d3-energyplus-shared-ventilation-gate-v1",
        "physical_process_id": "energyplus:d3_shared_finite_ventilation",
        "backend": "EnergyPlus", "backend_engine": "EnergyPlusAPI", "energyplus_version": "26.1.0",
        "status": "REAL_RUNTIME_PROBED" if not blockers else "FAIL_CLOSED", "passed": not blockers, "evidence_pending": bool(blockers),
        "runtime_stepping_gate": finite and monotone and continuity, "determinism_gate": deterministic,
        "ems_shared_capacity_gate": capacity, "bidirectional_coupling_gate": bidirectional,
        "action_channels": list(ACTION_NAMES), "shared_capacity_m3_s": SHARED_CAPACITY_M3_S,
        "shared_resource": "one native EMS-limited fan capacity serving Zone 1 and Zone 2 intake ventilation",
        "coupling_semantics": "EnergyPlus EMS reads both native schedule actuator requests and allocates native Zone Ventilation Air Exchange Flow Rate; increasing A reduces B actual airflow and raises B CO2, and vice versa",
        "cross_intervention_deltas": cross, "trace_refs": traces, "row_count": len(baseline), "exclusion_reasons": blockers,
        "agent_closed_loop": {"interface": ["reset(seed)->observation", "observe()->latest", "legal_actions()->schema", "step(action, dt_seconds)->transition", "close()->None"], "runtime_mechanism": "one persistent EnergyPlusAPI state with native runtime callbacks; no prefix replay", "physical_step_seconds": TICK_SECONDS, "horizon_steps": HORIZON_STEPS, "persistent_state": True, "prefix_rerun_per_step": False},
        "provenance": {**runtime_provenance(), "source_model_sha256_at_probe": source_hash, "working_model_sha256_at_probe": model_hash, "model_construction": "official VentilationSimpleTest plus pinned D3 EMS objects", "surrogate_model_used": False},
        "adapter_sha256": sha256(ROOT / "d3_energyplus_shared_ventilation_adapter.py"), "probe_sha256": sha256(Path(__file__).resolve()),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--check", action="store_true"); args = parser.parse_args()
    content = json.dumps(build(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not REPORT.is_file() or REPORT.read_text(encoding="utf-8") != content:
            raise SystemExit("stale D3 EnergyPlus shared ventilation gate report")
    else:
        BASE.mkdir(parents=True, exist_ok=True); REPORT.write_text(content, encoding="utf-8")


if __name__ == "__main__": main()
