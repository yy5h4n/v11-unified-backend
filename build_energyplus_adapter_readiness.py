#!/usr/bin/env python3
"""Bind frozen responsibility contracts to probed EnergyPlus process data.

This is a readiness ledger, not an Episode compiler.  A responsibility is
Episode-eligible only when every physical/evaluator gate is explicitly true.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
PACKET = ROOT / "generated/direct_adapter_packet_v1.json"
PROCESS_POOL = ROOT / "generated/energyplus_direct_process_pool_v2.json"
THERMAL_ACTION_GATE = ROOT / "generated/energyplus_thermal_runtime_v1/gate_report.json"
HUMIDITY_ACTION_GATE = ROOT / "generated/energyplus_humidity_runtime_v1/gate_report.json"
LIGHTING_ACTION_GATE = ROOT / "generated/energyplus_lighting_runtime_v1/gate_report.json"
OUTPUT = ROOT / "generated/energyplus_adapter_readiness_v1.json"
BLIND_ACTION_REQUIRED = {"rd_b62dd368cce0", "rd_bc53f8b79068"}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()

def usable_action_evidence(evidence: dict[str, Any] | None) -> dict[str, Any] | None:
    if evidence and evidence.get("passed") is True and all(evidence.get(gate) is True for gate in ("runtime_action_adapter_gate","action_available_gate","action_sensitivity_gate","determinism_gate")):
        return evidence
    return None


def readiness(candidate: dict[str, Any], process: dict[str, Any] | None, action_evidence: dict[str, Any] | None) -> tuple[str, list[str]]:
    adapter_status = candidate["adapter_readiness"]
    if adapter_status == "CONTRACT_UNDERSPECIFIED":
        return "BLOCKED_CONTRACT_UNDERSPECIFIED", ["CONTRACT_UNDERSPECIFIED"]
    if adapter_status == "MODEL_VARIANT_REQUIRED":
        return "BLOCKED_MODEL_VARIANT_REQUIRED", ["MODEL_VARIANT_REQUIRED"]
    if process is None:
        return "BLOCKED_NO_PHYSICAL_PROCESS", ["NO_PHYSICAL_PROCESS"]

    blockers = ["RESPONSIBILITY_OBSERVATIONS_NOT_MATCHED", "RESPONSIBILITY_ACTIONS_NOT_MATCHED", "SAME_PROCESS_LINEAGE_NOT_ESTABLISHED", "RESPONSIBILITY_EVALUATOR_NOT_BOUND"]
    if action_evidence is not None:
        return "CAPABILITY_PROBED_CONTRACT_BINDING_PENDING", blockers
    return "OBSERVATION_DATA_PROBED_ACTION_CAPABILITY_PENDING", blockers + ["ACTION_CAPABILITY_NOT_PROBED"]


def build() -> dict[str, Any]:
    packet = json.loads(PACKET.read_text(encoding="utf-8"))
    pool = json.loads(PROCESS_POOL.read_text(encoding="utf-8"))
    thermal_action = json.loads(THERMAL_ACTION_GATE.read_text(encoding="utf-8"))
    humidity_action = json.loads(HUMIDITY_ACTION_GATE.read_text(encoding="utf-8"))
    lighting_action = json.loads(LIGHTING_ACTION_GATE.read_text(encoding="utf-8"))
    by_family = {row["family"]: row for row in pool["processes"]}
    rows: list[dict[str, Any]] = []
    for candidate in packet["candidates"]:
        family = candidate["process_family"]
        process_family = "thermal_occupancy_energy" if family in {"temperature", "occupancy_energy"} else family
        process = by_family.get(process_family)
        # The tested intervention is a thermostat action, so it can discharge
        # causal gates only for temperature responsibilities.  It must not be
        # reused as evidence for occupancy-conditioned end-use control.
        action_evidence = None
        if family == "temperature": action_evidence = thermal_action
        elif family == "humidity_iaq_ventilation": action_evidence = humidity_action
        elif family == "lighting_blinds" and candidate["responsibility_id"] not in BLIND_ACTION_REQUIRED: action_evidence = lighting_action
        action_evidence = usable_action_evidence(action_evidence)
        status, blockers = readiness(candidate, process, action_evidence)
        rows.append({
            "responsibility_id": candidate["responsibility_id"],
            "query": candidate["query"],
            "responsibility_family": family,
            "physical_process_family": process_family,
            "observation_process_id": process.get("physical_process_id") if process else None,
            "observation_trace_ref": process.get("source_trace_ref") if process else None,
            "action_capability_process_id": action_evidence.get("physical_process_id") if action_evidence else None,
            "action_capability_gate_ref": str({"temperature": THERMAL_ACTION_GATE, "humidity_iaq_ventilation": HUMIDITY_ACTION_GATE, "lighting_blinds": LIGHTING_ACTION_GATE}[family].relative_to(ROOT)) if action_evidence else None,
            "action_capability_trace_refs": action_evidence.get("causal_trace_refs") if action_evidence else None,
            "candidate_window_count": len(process.get("windows", [])) if process else 0,
            "evidence_gates": {
                "family_observation_probe": bool(process and process.get("observability_gate") is True),
                "family_action_capability_probe": bool(action_evidence and action_evidence.get("action_sensitivity_gate") is True),
                "capability_cross_run_determinism": bool(action_evidence and action_evidence.get("determinism_gate") is True),
            },
            "responsibility_gates": {"required_observations_covered": False, "required_actions_covered": False, "same_process_lineage": False, "evaluator_complete": False},
            "family_match_only": True,
            "readiness_status": status,
            "blockers": blockers,
            "episode_eligible": False,
            "episode_count": 0,
        })

    counts = Counter(row["readiness_status"] for row in rows)
    return {
        "schema_version": "energyplus-adapter-readiness-v1",
        "status": "READINESS_LEDGER_NOT_EPISODE_RELEASE",
        "responsibility_candidate_count": len(rows),
        "episode_eligible_responsibility_count": 0,
        "episode_count": 0,
        "readiness_counts": dict(sorted(counts.items())),
        "source_artifacts": {
            "direct_adapter_packet": str(PACKET.relative_to(ROOT)),
            "direct_adapter_packet_sha256": file_sha256(PACKET),
            "physical_process_pool": str(PROCESS_POOL.relative_to(ROOT)),
            "physical_process_pool_sha256": file_sha256(PROCESS_POOL),
            "thermal_action_gate": str(THERMAL_ACTION_GATE.relative_to(ROOT)),
            "thermal_action_gate_sha256": file_sha256(THERMAL_ACTION_GATE),
            "humidity_action_gate": str(HUMIDITY_ACTION_GATE.relative_to(ROOT)),
            "humidity_action_gate_sha256": file_sha256(HUMIDITY_ACTION_GATE),
            "lighting_action_gate": str(LIGHTING_ACTION_GATE.relative_to(ROOT)),
            "lighting_action_gate_sha256": file_sha256(LIGHTING_ACTION_GATE),
        },
        "responsibilities": rows,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = json.dumps(build(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != content:
            raise SystemExit(f"stale readiness ledger: {OUTPUT}")
        return
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
