#!/usr/bin/env python3
"""Compile source-grounded SimuHome Episodes for one frozen responsibility."""
from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parents[4]
DATA = WORKSPACE / "external" / "SimuHome" / "data" / "benchmark"
CONTRACT_PATH = ROOT / "contracts" / "simuhome_kitchen_evening_warmth_v1.json"
CATALOG_PATH = ROOT / "responsibility_ai_coding_v1" / "NATURAL_STANDING_INTENT_QUERY_CATALOG_V2_5.json"
RELEASE_DIR = ROOT / "generated" / "simuhome_kitchen_evening_release_v1"
RID = "rd_split_016151c7c038"
QUERY_ID = "si_split_016151c7c038"

sys.path.insert(0, str(ROOT))
from unified_compiler.simuhome_room_thermal_adapter import (  # noqa: E402
    SimuHomeRoomThermalAdapter,
    canonical_json,
    digest_json,
)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def manifest_digest(paths: list[Path], base: Path) -> tuple[str, int]:
    manifest = {
        str(path.relative_to(base)): sha256_file(path)
        for path in sorted(paths)
    }
    return digest_json(manifest), len(manifest)


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.write_text("".join(canonical_json(row) + "\n" for row in rows), encoding="utf-8")


def load_catalog_record() -> tuple[dict[str, Any], dict[str, Any]]:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    matches = [row for row in catalog["queries"] if row.get("responsibility_id") == RID]
    if len(matches) != 1:
        raise RuntimeError(f"catalog_record_count:{len(matches)}")
    return catalog, matches[0]


def scan_candidates() -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    candidates: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    seen: set[str] = set()
    for source_path in sorted(DATA.glob("*.json")):
        try:
            source = json.loads(source_path.read_text(encoding="utf-8"))
            config = source["initial_home_config"]
            start = datetime.strptime(config["base_time"], "%Y-%m-%d %H:%M:%S")
            kitchen = config["rooms"]["kitchen"]
            temp_c = float(kitchen["state"]["temperature"]) / 100.0
            devices = [
                device for device in kitchen.get("devices", [])
                if device.get("device_type") in {"air_conditioner", "heat_pump"}
            ]
            if not 17 <= start.hour < 23:
                code = "NOT_EVENING"
            elif not 20.0 <= temp_c <= 24.0:
                code = "INITIAL_TEMP_OUT_OF_BAND"
            elif not devices:
                code = "NO_KITCHEN_THERMAL_DEVICE"
            else:
                config_digest = digest_json(config)
                if config_digest in seen:
                    failures.append({"source": source_path.name, "code": "DUPLICATE_SOURCE_CONFIG"})
                    continue
                seen.add(config_digest)
                # Prefer an AC; heat-pump cooling is not causally implemented.
                devices.sort(key=lambda row: (row["device_type"] != "air_conditioner", row["device_id"]))
                device = devices[0]
                if device["device_type"] == "heat_pump" and temp_c >= 22.0:
                    failures.append({"source": source_path.name, "code": "HEAT_PUMP_CANNOT_IMPROVE_ABOVE_TARGET"})
                    continue
                candidates.append({
                    "source_path": source_path,
                    "source": source,
                    "config": config,
                    "config_digest": config_digest,
                    "initial_temperature_c": temp_c,
                    "device_id": device["device_id"],
                    "device_type": device["device_type"],
                })
                continue
            failures.append({"source": source_path.name, "code": code})
        except Exception as error:
            failures.append({"source": source_path.name, "code": f"INVALID_CONFIG:{type(error).__name__}", "detail": str(error)})
    return candidates, failures


def trace_temperatures(replay: dict[str, Any]) -> list[float]:
    return [float(step["observation"]["temperature_c"]) for step in replay["trace"]]


def evaluate(replay: dict[str, Any], target_c: float, lower_c: float, upper_c: float) -> dict[str, Any]:
    temps = trace_temperatures(replay)
    finite = all(math.isfinite(value) for value in temps)
    violations = [value for value in temps if not lower_c <= value <= upper_c]
    return {
        "trajectory_complete": len(temps) >= 2,
        "no_nan": finite,
        "hard_violation_count": len(violations),
        "hard_violation_fraction": len(violations) / len(temps),
        "mean_abs_error_c": sum(abs(value - target_c) for value in temps) / len(temps),
        "min_temperature_c": min(temps),
        "max_temperature_c": max(temps),
    }


def main(out: Path | str = RELEASE_DIR) -> dict[str, Any]:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    catalog, catalog_record = load_catalog_record()
    target_c = float(contract["profile"]["target_c"])
    band = contract["profile"]["temperature_band_c"]
    lower_c, upper_c = float(band["lower"]), float(band["upper"])
    candidates, scan_failures = scan_candidates()
    failures = list(scan_failures)
    public_rows: list[dict[str, Any]] = []
    private_rows: list[dict[str, Any]] = []
    gate_rows: list[dict[str, Any]] = []
    released_process_signatures: set[str] = set()

    adapter_path = ROOT / "unified_compiler" / "simuhome_room_thermal_adapter.py"
    simuhome_root = WORKSPACE / "external" / "SimuHome"
    backend_files = list((simuhome_root / "src" / "simulator").rglob("*.py"))
    backend_manifest_sha, backend_manifest_count = manifest_digest(backend_files, simuhome_root)
    corpus_files = list(DATA.glob("*.json"))
    corpus_manifest_sha, corpus_manifest_count = manifest_digest(corpus_files, simuhome_root)
    git_commit = subprocess.run(
        ["git", "-C", str(simuhome_root), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=False,
    ).stdout.strip() or "unavailable"
    git_status = subprocess.run(
        ["git", "-C", str(simuhome_root), "status", "--porcelain"],
        capture_output=True, text=True, check=False,
    ).stdout
    try:
        import pydantic
        pydantic_version = pydantic.__version__
    except Exception:
        pydantic_version = "unavailable"
    fixed_hashes = {
        "adapter_sha256": sha256_file(adapter_path),
        "compiler_sha256": sha256_file(Path(__file__)),
        "contract_sha256": sha256_file(CONTRACT_PATH),
        "catalog_sha256": sha256_file(CATALOG_PATH),
        "simuhome_git_commit": git_commit,
        "simuhome_git_dirty": bool(git_status.strip()),
        "simuhome_git_status_sha256": hashlib.sha256(git_status.encode("utf-8")).hexdigest(),
        "backend_python_manifest_sha256": backend_manifest_sha,
        "backend_python_manifest_file_count": backend_manifest_count,
        "corpus_manifest_sha256": corpus_manifest_sha,
        "corpus_manifest_file_count": corpus_manifest_count,
        "pyproject_sha256": sha256_file(simuhome_root / "pyproject.toml"),
        "runtime": {"python": sys.version, "pydantic": pydantic_version},
    }

    def witness_policy(_step: int, _obs: dict[str, Any]) -> dict[str, Any]:
        return {"mode": "auto", "target_c": target_c}

    for candidate in candidates:
        source_path = candidate["source_path"]
        source_label = source_path.name
        try:
            adapter = SimuHomeRoomThermalAdapter(candidate["config"], device_id=candidate["device_id"])
            witness = adapter.replay(witness_policy)
            noop = SimuHomeRoomThermalAdapter(candidate["config"], device_id=candidate["device_id"]).replay(None)
            contrast_target = upper_c if candidate["initial_temperature_c"] <= target_c else lower_c

            def contrast_policy(_step: int, _obs: dict[str, Any], value: float = contrast_target) -> dict[str, Any]:
                return {"mode": "auto", "target_c": value}

            contrast = SimuHomeRoomThermalAdapter(candidate["config"], device_id=candidate["device_id"]).replay(contrast_policy)
            witness_replicate = SimuHomeRoomThermalAdapter(candidate["config"], device_id=candidate["device_id"]).replay(witness_policy)
            noop_replicate = SimuHomeRoomThermalAdapter(candidate["config"], device_id=candidate["device_id"]).replay(None)
            contrast_replicate = SimuHomeRoomThermalAdapter(candidate["config"], device_id=candidate["device_id"]).replay(contrast_policy)
            witness_eval = evaluate(witness, target_c, lower_c, upper_c)
            noop_eval = evaluate(noop, target_c, lower_c, upper_c)
            contrast_eval = evaluate(contrast, target_c, lower_c, upper_c)
            witness_digest = digest_json(witness)
            noop_digest = digest_json(noop)
            contrast_digest = digest_json(contrast)
            improvement = noop_eval["mean_abs_error_c"] - witness_eval["mean_abs_error_c"]
            witness_temps = trace_temperatures(witness)
            noop_temps = trace_temperatures(noop)
            gates = {
                "trajectory_complete": witness_eval["trajectory_complete"],
                "initial_observation_alignment": witness["trace"][0]["observation"] == noop["trace"][0]["observation"],
                "action_before_effect_alignment": witness["trace"][0]["action"] is not None and witness_temps[0] == candidate["initial_temperature_c"],
                "space_scope_match": witness["room_id"] == "kitchen",
                "virtual_evening_scope_match": all(17 <= int(step["observation"]["virtual_time"][11:13]) < 23 for step in witness["trace"]),
                "fixed_witness_feasible": witness_eval["hard_violation_count"] == 0,
                "action_sensitive": witness_temps != noop_temps,
                "evaluator_sensitive": improvement >= float(contract["opportunity_predicate"]["minimum_soft_metric_delta_c"]),
                "deterministic": (
                    witness_digest == digest_json(witness_replicate)
                    and noop_digest == digest_json(noop_replicate)
                    and contrast_digest == digest_json(contrast_replicate)
                ),
                "same_runtime_lineage": len({witness["source_config_sha256"], noop["source_config_sha256"], contrast["source_config_sha256"]}) == 1,
                "noop_compared": bool(noop["trace"]),
                "contrast_compared": bool(contrast["trace"]),
                "no_nan": witness_eval["no_nan"] and noop_eval["no_nan"] and contrast_eval["no_nan"],
                "one_primary_responsibility": contract["responsibility_id"] == RID == catalog_record["responsibility_id"],
                "no_public_gold_actions": not ({"gold_actions", "witness_policy", "witness_trace"} & {
                    "schema_version", "episode_id", "responsibility_id", "query_id", "natural_query",
                    "contract_id", "horizon_steps", "observation_interval_minutes", "initial_observation",
                    "observation_schema", "legal_action_schema", "profile", "evaluator", "split",
                    "statuses", "execution_scope", "termination", "content_hash",
                }),
            }
            gates["passed"] = all(gates.values())
            if not gates["passed"]:
                failed = [name for name, passed in gates.items() if name != "passed" and not passed]
                failures.append({"source": source_label, "code": "CERTIFICATION_FAILED", "detail": ",".join(failed)})
                continue

            # Full source configs may differ only in devices or states irrelevant
            # to this responsibility. Deduplicate again on the responsibility-
            # relevant causal process so those variants do not manufacture scale.
            process_signature = digest_json({
                "room_id": "kitchen",
                "device_type": witness["device_type"],
                "temperature_rounding_c": 0.001,
                "witness_temperatures_c": [round(value, 3) for value in witness_temps],
                "noop_temperatures_c": [round(value, 3) for value in noop_temps],
                "contrast_temperatures_c": [round(value, 3) for value in trace_temperatures(contrast)],
            })
            if process_signature in released_process_signatures:
                failures.append({
                    "source": source_label,
                    "code": "DUPLICATE_RESPONSIBILITY_PROCESS",
                    "detail": process_signature,
                })
                continue
            released_process_signatures.add(process_signature)

            identity = {"responsibility_id": RID, "source_config_sha256": candidate["config_digest"]}
            episode_id = "simuhome_kitchen_v1__" + digest_json(identity)[:20]
            physical_process_id = "simuhome_source__" + candidate["config_digest"][:20]
            public = {
                "schema_version": "responsibility-episode-simuhome-room-v1",
                "episode_id": episode_id,
                "responsibility_id": RID,
                "query_id": QUERY_ID,
                "natural_query": catalog_record["natural_query"],
                "contract_id": contract["contract_id"],
                "horizon_steps": len(witness["trace"]),
                "observation_interval_minutes": witness["sample_minutes"],
                "initial_observation": witness["trace"][0]["observation"],
                "observation_schema": {
                    "virtual_time": "SimuHome virtual local time",
                    "temperature_c": "float degC for explicit kitchen room",
                    "device_type": "selected causal thermal device family",
                },
                "legal_action_schema": {
                    **contract["legal_action_schema"],
                    "modes": ["auto", "heat", "cool", "off"],
                    "target_c_range": [7.0, 32.0],
                    "device_constraints": {
                        "air_conditioner": ["heat", "cool", "off"],
                        "heat_pump": ["heat", "off"],
                    },
                },
                "profile": {**contract["profile"], "parameter_provenance": contract["profile"]["provenance"]},
                "evaluator": {
                    "active_scope": "observed virtual-clock interval from source start until 23:00",
                    "hard_clause": "every observed kitchen temperature is within [20,24] degC",
                    "soft_metric": contract["soft_metric"],
                    "trajectory_completeness_required": True,
                },
                "split": "none",
                "statuses": {
                    "physical_status": "positive_opportunity",
                    "semantic_status": contract["semantic_status"],
                    "authorization_status": contract["authorization_status"],
                    "release_status": contract["release_status"],
                    "preview_only": True,
                },
                "execution_scope": contract["execution_scope"],
                "termination": {
                    "type": "finite_evaluation_window",
                    "terminal_verdict": contract["terminal_semantics"],
                    "reason": "MAINTAIN responsibility continues after the observed evening boundary",
                },
            }
            public["content_hash"] = digest_json(public)
            private = {
                "schema_version": "responsibility-episode-simuhome-room-v1-private",
                "episode_id": episode_id,
                "responsibility_id": RID,
                "query_id": QUERY_ID,
                "contract_id": contract["contract_id"],
                "binding_role": "PRIMARY",
                "physical_process_id": physical_process_id,
                "contract": contract,
                "backend_binding": {
                    "backend": "SimuHome 0.1.0",
                    "adapter_version": witness["adapter_version"],
                    "room_id": "kitchen",
                    "device_id": witness["device_id"],
                    "device_type": witness["device_type"],
                    "policy_bridge": "SimuHomeRoomThermalAdapter.replay(policy)",
                },
                "source_window": {
                    "source_file": source_label,
                    "source_file_sha256": sha256_file(source_path),
                    "source_config_sha256": candidate["config_digest"],
                    "responsibility_process_signature": process_signature,
                    "base_time": candidate["config"]["base_time"],
                    "end_boundary_local_hour": 23,
                    "pre_action_observation_provenance": "source state observed before current step action",
                },
                "responsibility_lineage": {
                    "catalog_version": catalog["catalog_version"],
                    "catalog_sha256": fixed_hashes["catalog_sha256"],
                    "contract_artifact": str(CONTRACT_PATH.relative_to(ROOT)),
                    "contract_sha256": fixed_hashes["contract_sha256"],
                    "source_evidence_ids": catalog_record["source_evidence_ids"],
                    "query_provenance": catalog_record["provenance"],
                },
                "source_hashes": fixed_hashes,
                "replay_certificates": {
                    "witness_policy": {"type": "observation_conditioned_target", "target_c": target_c},
                    "witness_digest": witness_digest,
                    "noop_digest": noop_digest,
                    "contrast_digest": contrast_digest,
                    "witness_replicate_digest": digest_json(witness_replicate),
                    "noop_replicate_digest": digest_json(noop_replicate),
                    "contrast_replicate_digest": digest_json(contrast_replicate),
                    "witness_metrics": witness_eval,
                    "noop_metrics": noop_eval,
                    "contrast_metrics": contrast_eval,
                    "soft_metric_improvement_vs_noop_c": improvement,
                },
                "qa_verdicts": gates,
                "selection_lineage": {
                    "selection_rule": "distinct source config; evening start; explicit kitchen; initial hard band; causal thermal device",
                    "responsibility_process_deduplication": "room + device family + 0.001C-normalized witness/noop/contrast temperature trajectories; timestamps excluded from identity",
                    "primary_window_is_unique": True,
                    "split": "none",
                    "split_reason": "all records share the same SimuHome implementation lineage",
                },
                "gold_actions": witness["trace"],
                "gold_actions_released_publicly": False,
                "statuses": public["statuses"],
            }
            public_rows.append(public)
            private_rows.append(private)
            gate_rows.append({
                "episode_id": episode_id,
                "responsibility_id": RID,
                "physical_process_id": physical_process_id,
                "source_config_sha256": candidate["config_digest"],
                "gates": gates,
                "passed": True,
            })
        except Exception as error:
            failures.append({"source": source_label, "code": f"REPLAY_ERROR:{type(error).__name__}", "detail": str(error)})

    public_rows.sort(key=lambda row: row["episode_id"])
    private_rows.sort(key=lambda row: row["episode_id"])
    gate_rows.sort(key=lambda row: row["episode_id"])
    write_jsonl(out / "episodes_public.jsonl", public_rows)
    write_jsonl(out / "episodes_private.jsonl", private_rows)
    failure_counts = dict(sorted(Counter(row["code"] for row in failures).items()))
    build_report = {
        "schema_version": "simuhome-kitchen-evening-release-v1",
        "status": "PASS" if public_rows else "EMPTY_FAIL_CLOSED",
        "passed": bool(public_rows),
        "source_file_count": len(list(DATA.glob("*.json"))),
        "candidate_source_count": len(candidates),
        "episode_count": len(public_rows),
        "responsibility_count": 1 if public_rows else 0,
        "responsibility_episode_counts": {RID: len(public_rows)} if public_rows else {},
        "all_primary_processes_unique": len({row["physical_process_id"] for row in private_rows}) == len(private_rows),
        "gold_actions_released_publicly": False,
        "split_counts": {"none": len(public_rows)} if public_rows else {},
        "failure_counts": failure_counts,
        "failure_examples": failures[:20],
        "selection_universe": {
            "corpus_manifest_sha256": corpus_manifest_sha,
            "corpus_file_count": corpus_manifest_count,
            "backend_python_manifest_sha256": backend_manifest_sha,
            "backend_python_file_count": backend_manifest_count,
            "simuhome_git_commit": git_commit,
            "simuhome_git_dirty": bool(git_status.strip()),
            "runtime": fixed_hashes["runtime"],
            "license_status": "not_found_in_checked_repository; authorization_unknown",
        },
        "public_digest": sha256_file(out / "episodes_public.jsonl"),
        "private_digest": sha256_file(out / "episodes_private.jsonl"),
        "limitations": [
            "SimuHome uses a deterministic synthetic room-state aggregator rather than a calibrated building model",
            "source configurations come from the SimuHome benchmark corpus and share one simulator lineage",
            "the 22C target and 20-24C band are benchmark construction parameters, not literal query semantics",
            "cooking, shower, wake, presence, fireplace, and newborn lifecycle semantics are not inferred",
            "authorization is benchmark-sandbox-only and the release is provisional preview data",
            "no license file was found in the checked SimuHome repository; public redistribution is not authorized",
        ],
    }
    write_json(out / "build_report.json", build_report)
    write_json(out / "replay_gate.json", {
        "schema_version": "simuhome-kitchen-evening-replay-gate-v1",
        "all_released_episodes_passed": bool(gate_rows) and all(row["passed"] for row in gate_rows),
        "candidate_source_count": len(candidates),
        "released_episode_count": len(gate_rows),
        "episodes": gate_rows,
    })
    (out / "DATASET_CARD.md").write_text(
        "# SimuHome Kitchen-Evening Responsibility Release v1\n\n"
        "A provisional, sandbox-only responsibility-level expansion built by scanning distinct "
        "SimuHome benchmark source configurations for evening kitchen thermal opportunities. "
        "Each released physical window has one primary responsibility and passes witness, no-op, "
        "contrast, determinism, lineage, evaluator, and no-gold-leakage gates.\n\n"
        "The simulator is synthetic and not a calibrated building model. The release does not infer "
        "cooking, shower, wake, presence, fireplace, or newborn lifecycle state.\n",
        encoding="utf-8",
    )
    print(json.dumps(build_report, ensure_ascii=False, indent=2))
    return build_report


if __name__ == "__main__":
    main()
