#!/usr/bin/env python3
"""Probe and gate the real Modelica Buildings/AixLib D2 backend.

Only a successful OpenModelica run earns ``REAL_RUNTIME_PROBED``.  Missing
compiler, unpinned libraries, malformed output, and action-insensitive traces
remain ``EVIDENCE_PENDING`` with explicit blockers.  This probe emits backend
traces only; it does not create dataset records or invoke shared evaluation.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from d2_modelica_buildings_aixlib_adapter import (
    ACTION,
    MODEL_NAME,
    MODEL_SOURCE,
    ModelicaBackendError,
    ModelicaBuildingsAixLibAdapter,
    ModelicaRuntimeUnavailable,
    canonical_digest,
    probe_runtime,
    sha256,
)


ROOT = Path(__file__).resolve().parent
BASE = ROOT / "generated" / "d2_modelica_buildings_aixlib_v1"
REPORT = BASE / "gate_report.json"


def _save_trace(name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    path = BASE / f"{name}.jsonl"
    path.write_text("".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    return {"path": str(path.relative_to(ROOT)), "row_count": len(rows), "sha256": sha256(path)}


def _pending(probe: dict[str, Any], blockers: list[str], records: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema_version": "d2-modelica-buildings-aixlib-backend-gate-v1",
        "physical_process_id": "modelica:d2_two_room_thermo_hygrometric",
        "backend": "Modelica",
        "backend_family": "Buildings/AixLib",
        "status": "EVIDENCE_PENDING",
        "passed": False,
        "runtime_stepping_gate": False,
        "action_available_gate": False,
        "action_sensitivity_gate": False,
        "determinism_gate": False,
        "time_alignment_gate": False,
        "provenance_gate": False,
        "replay_arm_count": len(records or {}),
        "replay_arms": {
            name: {"action": row.get("action"), "replicate": row.get("replicate"), "trace_digest": row.get("trace_digest")}
            for name, row in (records or {}).items()
        },
        "action_adapter": ACTION,
        "observation_roles": {
            "room_a_temperature_c": "Modelica output roomATemperature",
            "room_b_temperature_c": "Modelica output roomBTemperature",
            "room_a_relative_humidity_pct": "Modelica output roomARelativeHumidity",
            "room_b_relative_humidity_pct": "Modelica output roomBRelativeHumidity",
            "heater_heat_flow_w": "Modelica output heaterHeatFlow",
        },
        "model_name": MODEL_NAME,
        "model_source_path": str(MODEL_SOURCE.relative_to(ROOT)),
        "model_source_sha256": sha256(MODEL_SOURCE) if MODEL_SOURCE.is_file() else None,
        "runtime_probe": probe,
        "surrogate_model_used": False,
        "evidence_state": "no certified trajectory is emitted until all runtime gates pass",
        "exclusion_reasons": blockers,
        "adapter_sha256": sha256(ROOT / "d2_modelica_buildings_aixlib_adapter.py"),
        "probe_sha256": sha256(Path(__file__).resolve()),
    }


def build() -> dict[str, Any]:
    runtime_probe = probe_runtime()
    if not runtime_probe["available"]:
        return _pending(runtime_probe, list(runtime_probe["blockers"]))

    adapter = ModelicaBuildingsAixLibAdapter()
    records: dict[str, dict[str, Any]] = {}
    blockers: list[str] = []
    for label, action in (("radiator_closed", 0.0), ("radiator_open", 1.0)):
        for replicate in (1, 2):
            try:
                adapter.reset(seed=0)
                records[f"{label}_{replicate}"] = adapter.run(action, label, replicate)
            except (ModelicaBackendError, ModelicaRuntimeUnavailable, OSError, ValueError) as exc:
                blockers.append(f"REAL_RUNTIME_RUN_FAILED:{type(exc).__name__}:{exc}")
                return _pending(runtime_probe, blockers, records)

    closed = records["radiator_closed_1"]["trace"]
    opened = records["radiator_open_1"]["trace"]
    same_horizon = bool(closed) and len(closed) == len(opened)
    time_aligned = same_horizon and all(a["time_s"] == b["time_s"] for a, b in zip(closed, opened))
    finite = all(
        math.isfinite(value)
        for record in records.values()
        for row in record["trace"]
        for value in row["observation"].values()
    )
    deterministic = all(
        records[f"{label}_1"]["trace_digest"] == records[f"{label}_2"]["trace_digest"]
        for label in ("radiator_closed", "radiator_open")
    )
    # The first observation is the reset state (default action 0); only
    # post-doStep rows certify that the commanded online action was applied.
    action_available = bool(closed[1:] and opened[1:]) and {row["action_radiator_valve"] for row in closed[1:]} == {0.0} and {row["action_radiator_valve"] for row in opened[1:]} == {1.0}
    deltas = {
        role: max((abs(a["observation"][role] - b["observation"][role]) for a, b in zip(closed, opened)), default=0.0)
        for role in closed[0]["observation"]
    }
    action_sensitive = any(delta > 1e-9 for role, delta in deltas.items() if role != "room_a_relative_humidity_pct" and role != "room_b_relative_humidity_pct")
    provenance = all(
        record["provenance"].get("backend") == "Modelica"
        and record["provenance"].get("model_name") == MODEL_NAME
        and record["provenance"].get("surrogate_model_used") is False
        for record in records.values()
    )
    if not same_horizon:
        blockers.append("CAUSAL_CONTRAST_HORIZON_MISMATCH")
    if not time_aligned:
        blockers.append("CAUSAL_CONTRAST_TIME_MISALIGNMENT")
    if not finite:
        blockers.append("NONFINITE_OBSERVATION")
    if not deterministic:
        blockers.append("CROSS_RUN_RESET_REPLAY_MISMATCH")
    if not action_available:
        blockers.append("ACTION_NOT_OBSERVED")
    if not action_sensitive:
        blockers.append("ACTION_INSENSITIVE_THERMAL_RESPONSE")
    if not provenance:
        blockers.append("PROVENANCE_GATE_FAILED")
    traces = {
        "radiator_closed": _save_trace("radiator_closed_trace", closed),
        "radiator_open": _save_trace("radiator_open_trace", opened),
    }
    gate = _pending(runtime_probe, blockers, records)
    gate.update(
        {
            "status": "REAL_RUNTIME_PROBED" if not blockers else "EVIDENCE_PENDING",
            "passed": not blockers,
            "runtime_stepping_gate": same_horizon and finite,
            "action_available_gate": action_available,
            "action_sensitivity_gate": action_sensitive,
            "determinism_gate": deterministic,
            "time_alignment_gate": time_aligned,
            "provenance_gate": provenance,
            "causal_trace_refs": traces,
            "maximum_observed_deltas": deltas,
            "trajectory_digests": {name: row["trace_digest"] for name, row in records.items()},
        }
    )
    return gate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = json.dumps(build(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not REPORT.is_file() or REPORT.read_text(encoding="utf-8") != content:
            raise SystemExit("stale Modelica Buildings/AixLib backend gate report")
        return
    BASE.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
