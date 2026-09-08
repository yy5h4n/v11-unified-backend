#!/usr/bin/env python3
"""Compile the first V2.5-catalog Episodes from fully matched HVAC processes."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
V10_RELEASE = ROOT.parent / "v10_diversity_aware_compiler" / "generated" / "diversity_pilot_v1"
CATALOG = ROOT / "responsibility_ai_coding_v1" / "NATURAL_STANDING_INTENT_QUERY_CATALOG_V2_5.json"
MAPPING = ROOT / "generated" / "responsibility_backend_mapping_v1.json"
RELEASE = ROOT / "generated" / "supported_hvac_release_v1"
REPLAY_GATE = ROOT / "generated" / "supported_hvac_replay_gate_v1.json"

GENERIC_COMFORT = "rd_37104b57370a"
WARM_WHILE_HOME = "rd_94d666a58c83"


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def canonical_digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build() -> dict[Path, str]:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    query_by_id = {row["responsibility_id"]: row for row in catalog["queries"]}
    mapping = json.loads(MAPPING.read_text(encoding="utf-8"))
    replay_gate = json.loads(REPLAY_GATE.read_text(encoding="utf-8"))
    passed_processes = {row["process_id"] for row in replay_gate["processes"] if row["passed"]}
    if replay_gate["candidate_process_count"] != 50 or replay_gate["passed_count"] != 48:
        raise RuntimeError("supported HVAC replay gate is not the reviewed 50-candidate/48-pass gate")
    supported = {
        row["responsibility_id"]
        for row in mapping["mappings"]
        if row["support_status"] == "SUPPORTED_REPLAYABLE"
    }
    expected = {GENERIC_COMFORT, WARM_WHILE_HOME}
    if supported != expected:
        raise RuntimeError(f"supported mapping changed: expected {sorted(expected)}, got {sorted(supported)}")

    public_rows = load_jsonl(V10_RELEASE / "episodes_public.jsonl")
    private_rows = load_jsonl(V10_RELEASE / "episodes_private.jsonl")
    public_by_id = {row["episode_id"]: row for row in public_rows}
    main = [row for row in private_rows if row.get("subset") == "main" and row["backend_binding"]["backend"] == "CityLearn"]
    if len(main) != 50 or len({row["physical_process_id"] for row in main}) != 50:
        raise RuntimeError("frozen v10 HVAC inventory is not the expected 50 unique primary processes")

    compiled_public: list[dict[str, Any]] = []
    compiled_private: list[dict[str, Any]] = []
    for old_private in sorted(main, key=lambda row: row["physical_process_id"]):
        if old_private["physical_process_id"] not in passed_processes:
            continue
        old_public = public_by_id[old_private["episode_id"]]
        mode = old_private["lifecycle"]["axes"]["thermal_mode"]
        rid = WARM_WHILE_HOME if mode == "heating" else GENERIC_COMFORT
        query = query_by_id[rid]
        process_id = old_private["physical_process_id"]
        episode_id = "v25_hvac__" + canonical_digest({"responsibility_id": rid, "process_id": process_id})[:20]
        contract_id = "contract__" + canonical_digest({"episode_id": episode_id, "contract_version": "v1"})[:20]

        public = {
            "schema_version": "responsibility-episode-v11-v25-pilot",
            "episode_id": episode_id,
            "responsibility_id": rid,
            "query": query["natural_query"],
            "split": old_public["split"],
            "horizon_steps": old_public["horizon_steps"],
            "observation_interval_minutes": old_public["observation_interval_minutes"],
            "initial_observation": old_public["initial_observation"],
            "allowed_actions": old_public["allowed_actions"],
            "termination": old_public["termination"],
            "profile_fields": {
                "comfort_target": "resident_setpoint_c",
                "comfort_tolerance_c": 2.0,
                "comfort_tolerance_status": "episode construction parameter",
            },
        }
        private = {
            "schema_version": "responsibility-episode-v11-v25-pilot-private",
            "episode_id": episode_id,
            "responsibility_id": rid,
            "standing_intent_id": query["standing_intent_id"],
            "contract_id": contract_id,
            "process_id": process_id,
            "binding_role": "PRIMARY",
            "physical_support_status": "SUPPORTED_REPLAYABLE",
            "backend_binding": old_private["backend_binding"],
            "physical_process": {
                "source_process": old_private["source_process"],
                "lifecycle": old_private["lifecycle"],
                "legacy_episode_id": old_private["episode_id"],
            },
            "responsibility_contract": {
                "contract_version": "trajectory-contract-v11-v25-pilot",
                "activation": {"type": "episode_start"},
                "release": {"type": "episode_end", "require_complete": True},
                "priority": {"type": "lexicographic", "order": ["trajectory_complete", "occupied_temperature_comfort"]},
                "clauses": [
                    {
                        "clause_id": "trajectory_complete",
                        "kind": "HARD_INVARIANT",
                        "predicate": {"type": "backend_trajectory_complete"},
                    },
                    {
                        "clause_id": "occupied_temperature_comfort",
                        "kind": "HARD_INVARIANT",
                        "when": {"field": "observation.occupant_count", "op": ">", "value": 0.0},
                        "predicate": {
                            "type": "absolute_error_at_most",
                            "observed_field": "effect.indoor_temperature_c",
                            "target_field": "observation.resident_setpoint_c",
                            "tolerance": 2.0,
                            "unit": "degC",
                            "parameter_provenance": "construction profile; not a universal user-semantic threshold",
                        },
                    },
                ],
            },
            "selection_lineage": {
                "source": "frozen v10 source-grounded HVAC process pool",
                "assignment_rule": "heating processes bind warm-while-home; cooling processes bind generic comfort",
                "solver_or_agent_output_used_for_membership": False,
                "gold_actions_released": False,
            },
        }
        compiled_public.append(public)
        compiled_private.append(private)

    process_ids = [row["process_id"] for row in compiled_private]
    contract_ids = [row["contract_id"] for row in compiled_private]
    episode_ids = [row["episode_id"] for row in compiled_private]
    if len(set(process_ids)) != len(process_ids) or len(set(contract_ids)) != len(contract_ids) or len(set(episode_ids)) != len(episode_ids):
        raise RuntimeError("compiled IDs are not unique")
    counts = Counter(row["responsibility_id"] for row in compiled_private)
    report = {
        "schema_version": "supported-hvac-build-report-v1",
        "candidate_process_count": 50,
        "episode_count": len(compiled_public),
        "excluded_process_count": 2,
        "process_count": len(set(process_ids)),
        "primary_contract_count": len(compiled_private),
        "responsibility_count": len(counts),
        "responsibility_episode_counts": dict(sorted(counts.items())),
        "split_counts": dict(sorted(Counter(row["split"] for row in compiled_public).items())),
        "gold_actions_released": False,
        "membership_uses_solver_or_agent_output": False,
        "source_mapping_sha256": hashlib.sha256(MAPPING.read_bytes()).hexdigest(),
        "source_replay_gate_sha256": hashlib.sha256(REPLAY_GATE.read_bytes()).hexdigest(),
        "public_digest": canonical_digest(compiled_public),
        "private_digest": canonical_digest(compiled_private),
    }
    return {
        RELEASE / "episodes_public.jsonl": "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in compiled_public),
        RELEASE / "episodes_private.jsonl": "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in compiled_private),
        RELEASE / "build_report.json": json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    }


def validate(outputs: dict[Path, str]) -> None:
    public = [json.loads(x) for x in outputs[RELEASE / "episodes_public.jsonl"].splitlines()]
    private = [json.loads(x) for x in outputs[RELEASE / "episodes_private.jsonl"].splitlines()]
    assert len(public) == len(private) == 48
    assert {x["episode_id"] for x in public} == {x["episode_id"] for x in private}
    assert len({x["process_id"] for x in private}) == 48
    assert all(x["binding_role"] == "PRIMARY" for x in private)
    assert all(not x["selection_lineage"]["gold_actions_released"] for x in private)
    assert all("backend_binding" not in x for x in public)
    assert all("process_id" not in x for x in public)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    outputs = build()
    validate(outputs)
    if args.check:
        stale = [str(path) for path, content in outputs.items() if not path.is_file() or path.read_text(encoding="utf-8") != content]
        if stale:
            raise SystemExit("stale supported HVAC release: " + ", ".join(stale))
        return
    RELEASE.mkdir(parents=True, exist_ok=True)
    for path, content in outputs.items():
        path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
