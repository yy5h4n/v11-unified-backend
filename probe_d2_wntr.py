#!/usr/bin/env python3
"""Probe the D2 WNTR residential water backend and write gate evidence."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from unified_compiler.adapters.d2_wntr import WNTRDependencyError, WNTRResidentialWaterAdapter, sha256

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "generated/d2_wntr_v1"
REPORT = BASE / "gate_report.json"


def save_trace(name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    path = BASE / f"{name}.jsonl"
    path.write_text("".join(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows), encoding="utf-8")
    return {"path": str(path.relative_to(ROOT)), "row_count": len(rows), "sha256": sha256(path)}


def build() -> dict[str, Any]:
    try:
        adapter = WNTRResidentialWaterAdapter()
    except WNTRDependencyError as exc:
        BASE.mkdir(parents=True, exist_ok=True)
        report = {
            "schema_version": "d2-wntr-backend-gate-v1",
            "backend": "WNTR",
            "status": "FAIL_CLOSED",
            "passed": False,
            "evidence_pending": True,
            "exclusion_reasons": ["WNTR_RUNTIME_UNAVAILABLE"],
            "error": str(exc),
        }
        return report

    records: dict[str, dict[str, Any]] = {}
    for label, action in (("valve_closed", 0.0), ("valve_open", 1.0)):
        for replicate in (1, 2):
            adapter.reset(seed=0)
            records[f"{label}_{replicate}"] = adapter.run(action, label, replicate)
    adapter.close()

    closed = records["valve_closed_1"]
    opened = records["valve_open_1"]
    pairs = list(zip(closed["trace"], opened["trace"]))
    same_horizon = len(closed["trace"]) == len(opened["trace"]) and bool(pairs)
    time_aligned = same_horizon and all(a["time_seconds"] == b["time_seconds"] for a, b in pairs)
    deterministic = all(records[f"{label}_1"]["trace_digest"] == records[f"{label}_2"]["trace_digest"] for label in ("valve_closed", "valve_open"))
    finite = all(math.isfinite(value) for record in records.values() for row in record["trace"] for value in row["observation"].values())
    action_available = {row["action_isolation_valve_open"] for row in closed["trace"]} == {0.0} and {row["action_isolation_valve_open"] for row in opened["trace"]} == {1.0}
    roles = ("pressure_kitchen_m", "flow_house_isolation_m3_s", "tank_level_m", "leak_total_m3_s")
    deltas = {role: max(abs(a["observation"][role] - b["observation"][role]) for a, b in pairs) if pairs else 0.0 for role in roles}
    action_sensitive = any(value > 1e-9 for value in deltas.values())
    provenance_ok = all(record["provenance"].get("wntr_version") == "1.3.0" and record["provenance"].get("wntr_importable") for record in records.values())

    BASE.mkdir(parents=True, exist_ok=True)
    traces = {"valve_closed": save_trace("valve_closed_trace", closed["trace"]), "valve_open": save_trace("valve_open_trace", opened["trace"])}
    blockers: list[str] = []
    if not same_horizon: blockers.append("CAUSAL_CONTRAST_HORIZON_MISMATCH")
    if not time_aligned: blockers.append("CAUSAL_CONTRAST_TIME_MISALIGNMENT")
    if not deterministic: blockers.append("CROSS_RESET_REPLAY_MISMATCH")
    if not finite: blockers.append("NONFINITE_OBSERVATION")
    if not action_available: blockers.append("ACTION_NOT_OBSERVED")
    if not action_sensitive: blockers.append("ACTION_INSENSITIVE_WATER_RESPONSE")
    if not provenance_ok: blockers.append("PROVENANCE_GATE_FAILED")
    return {
        "schema_version": "d2-wntr-backend-gate-v1",
        "physical_process_id": "wntr:d2_residential_water_supply",
        "backend": "WNTR",
        "backend_engine": "WNTRSimulator",
        "backend_version": "1.3.0",
        "status": "REAL_RUNTIME_PROBED" if not blockers else "FAIL_CLOSED",
        "passed": not blockers,
        "evidence_pending": False,
        "runtime_stepping_gate": same_horizon and finite,
        "action_available_gate": action_available,
        "action_sensitivity_gate": action_sensitive,
        "determinism_gate": deterministic,
        "time_alignment_gate": time_aligned,
        "provenance_gate": provenance_ok,
        "replay_arm_count": len(records),
        "replay_arms": {name: {"action": record["action"], "replicate": record["replicate"], "trace_digest": record["trace_digest"]} for name, record in records.items()},
        "observation_roles": {"pressure": "kitchen/bathroom junction pressure", "flow": "source_fill and house_isolation link flowrate", "tank_level": "house_tank water level", "leak_consequence": "pressure-dependent kitchen and bathroom emitter demand"},
        "agent_closed_loop": {
            "interface": ["reset(seed)->observation", "observe()->observation", "step(action, delta_t|steps)->transition"],
            "runtime_mechanism": "persistent isolated worker retaining one WaterNetworkModel and WNTRSimulator; each command invokes one native hydraulic timestep",
            "physical_step_seconds": 3600,
            "mid_run_action_change": True,
            "prefix_rerun_per_step": False,
            "termination": "six-hour pinned network horizon",
            "limitations": ["WNTR hydraulic timestep is fixed at 3600 seconds", "EPANET native library is not used by this route"],
        },
        "maximum_observed_deltas": deltas,
        "row_count": len(closed["trace"]),
        "trajectory_digests": {name: record["trace_digest"] for name, record in records.items()},
        "causal_trace_refs": traces,
        "exclusion_reasons": blockers,
        "adapter_sha256": sha256(ROOT / "unified_compiler/adapters/d2_wntr.py"),
        "probe_sha256": sha256(Path(__file__).resolve()),
        "provenance": closed["provenance"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = json.dumps(build(), indent=2, sort_keys=True) + "\n"
    if args.check:
        if not REPORT.is_file() or REPORT.read_text(encoding="utf-8") != content:
            raise SystemExit("stale D2 WNTR gate report")
        return
    BASE.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
