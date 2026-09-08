#!/usr/bin/env python3
"""Build a fresh Harness V2 batch for every FULL responsibility.

This stage consumes independently reviewed provisional non-saturated windows
and certifies them through the Harness V2 adapter. Dataset-side no-op/reference, Query intervention,
action-sensitivity and determinism checks remain outside Harness Core.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import platform
from datetime import datetime
from decimal import Decimal
from importlib.metadata import version
from typing import Any, Mapping

from harness_v2.core import EpisodeSpec, Harness, RunArtifact
from harness_v2.episode_validator import EpisodeValidationSuite
from harness_v2.evaluator_v3 import evaluate_thermal_v3
from harness_v2.simuhome_adapter import SimuHomeHarnessAdapter


ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT.parents[4]
SIMUHOME_DATA = WORKSPACE / "external" / "SimuHome" / "data" / "benchmark"
MAPPING = ROOT / "generated" / "responsibility_backend_mapping_v2.json"
QUERIES = ROOT / "responsibility_ai_coding_v1" / "FULL_QUERY_VARIANTS_V3_REVIEWED.jsonl"
OUTPUT = ROOT / "generated" / "harness_v2_full_episode_batch_v2"
SCAN_REPORT = ROOT / "runs" / "non_saturated_episode_scan_v1" / "scan_report.json"
FAMILY_RESPONSIBILITY = {
    "kitchen": "rd_split_016151c7c038",
    "multiroom": "rd_split_b8457e559b4d",
}
TARGET_C = 22.0
LOWER_C = 20.0
UPPER_C = 24.0


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def thermal_rooms(config: dict[str, Any]) -> list[str]:
    return sorted(
        room
        for room, value in config["rooms"].items()
        if any(device.get("device_type") in {"air_conditioner", "heat_pump"} for device in value.get("devices", []))
    )


def command(device_id: str, mode: str, target_c: float) -> dict[str, Any]:
    return {
        "device_id": device_id,
        "capability": "thermal.control",
        "operation": "set",
        "parameters": {"mode": mode, "target_c": target_c},
    }


class WaitPolicy:
    def decide(self, _view: dict[str, Any]) -> dict[str, Any]:
        return {"kind": "wait"}


class ReactiveThermalPolicy:
    def __init__(self, target_rooms: list[str], target_c: float | Mapping[str, float]):
        self._target_rooms = tuple(target_rooms)
        self._target_c = target_c

    def decide(self, view: dict[str, Any]) -> dict[str, Any]:
        devices = view["observation"]["devices"]
        targets = self._target_c if isinstance(self._target_c, Mapping) else {room: self._target_c for room in self._target_rooms}
        commands = [command(devices[room]["device_id"], "auto", float(targets[room])) for room in self._target_rooms]
        return {"kind": "act", "commands": commands}


class ReviewedQueryPolicy:
    """Reference semantic policy keyed by the separately reviewed Query artifact."""

    def __init__(self, query_to_responsibility: Mapping[str, str]):
        self._query_to_responsibility = query_to_responsibility

    def decide(self, view: dict[str, Any]) -> dict[str, Any]:
        query = view.get("query", {})
        if not isinstance(query, dict):
            return {"kind": "wait"}
        responsibility_id = self._query_to_responsibility.get(query.get("text", ""))
        if responsibility_id is None:
            return {"kind": "wait"}
        if responsibility_id == "rd_split_016151c7c038":
            target_rooms = ["kitchen"]
        else:
            target_rooms = list(query.get("context", {}).get("room_refs", {}).get("target_room_ids", []))
        if not target_rooms:
            return {"kind": "wait"}
        visible = view["observation"]["devices"]
        if not set(target_rooms) <= set(visible):
            return {"kind": "wait"}
        return ReactiveThermalPolicy(target_rooms, float(view["user_preferences"]["thermal_target_c"])).decide(view)


def observation_frames(run: RunArtifact) -> list[dict[str, Any]]:
    return [item["value"] for item in run.public_trace if item["type"] == "observation"]


def active_frames(run: RunArtifact) -> list[dict[str, Any]]:
    """The terminal observation is at/after the exclusive evening boundary."""
    frames = observation_frames(run)
    return frames[:-1] if len(frames) > 1 else frames


def score(run: RunArtifact, target_rooms: list[str]) -> float:
    frames = active_frames(run)
    if run.status != "completed" or not frames:
        return float("inf")
    room_mae = {
        room: sum(abs(frame["rooms"][room]["temperature_c"] - TARGET_C) for frame in frames) / len(frames)
        for room in target_rooms
    }
    return max(room_mae.values())


def hard_pass(run: RunArtifact, target_rooms: list[str]) -> bool:
    return run.status == "completed" and all(
        LOWER_C <= frame["rooms"][room]["temperature_c"] <= UPPER_C
        for frame in active_frames(run)
        for room in target_rooms
    )


def action_signature(run: RunArtifact) -> str:
    return digest([item["action"] for item in run.public_trace if item["type"] == "action"])


def trajectory_signature(run: RunArtifact, target_rooms: list[str]) -> str:
    return digest([
        {room: frame["rooms"][room]["temperature_c"] for room in target_rooms}
        for frame in active_frames(run)
    ])


def serialize_run(run: RunArtifact) -> dict[str, Any]:
    return {
        "episode_id": run.episode_id,
        "status": run.status,
        "trace_digest": run.trace_digest,
        "public_trace": list(run.public_trace),
        "private_trace": list(run.private_trace),
    }


def jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


def thermal_capability_manifest(initial_observation: dict[str, Any]) -> dict[str, Any]:
    devices = [
        {
            "device_id": item["device_id"],
            "device_type": item["device_type"],
            "room_id": room,
            "availability": "available",
            "capabilities": ["thermal.control"],
        }
        for room, item in sorted(initial_observation["devices"].items())
    ]
    return {
        "manifest_version": "simuhome-thermal-capability-2.0.0",
        "backend_id": "simuhome_room_thermal",
        "action_kinds": ["act", "wait"],
        "track_action_kinds": {
            "explicit_profile_control": ["act", "wait"],
            "interactive_clarification": ["act", "wait"],
        },
        "reply_provider_available": False,
        "devices": devices,
        "capabilities": [{
            "capability_id": "thermal.control",
            "operations": [{
                "name": "set",
                "parameters": [
                    {"name": "mode", "type": "string", "required": True, "allowed_values": ["auto", "heat", "off"]},
                    {"name": "target_c", "type": "number", "required": True, "minimum": 7.0, "maximum": 32.0},
                ],
            }],
        }],
    }


def evaluator_manifest(run: RunArtifact, target_rooms: list[str], visible_rooms: list[str]) -> dict[str, Any]:
    frames = active_frames(run)
    if not frames:
        raise RuntimeError("cannot bind evaluator without active frames")
    start = frames[0]["virtual_time"]
    release = datetime.fromisoformat(start).replace(hour=23, minute=0, second=0).isoformat(sep=" ")
    initial_devices = observation_frames(run)[0]["devices"]
    optional_metrics: dict[str, Any] = {
        "realized_collateral": {
            "device_scope_map": {
                frame["device_id"]: ("in_scope" if room in target_rooms else "out_of_scope")
                for room, frame in initial_devices.items()
                if room in visible_rooms
            }
        }
    }
    if all(initial_devices[room].get("energy_semantics") != "unavailable" for room in target_rooms):
        optional_metrics["energy"] = {
            "binding": "/private_evaluator_primitives/energy_proxy_wh_interval",
            "aggregation": "sum",
            "semantics": "simulator_duty_gated_rated_power_proxy_wh",
            "scope": "responsibility_target_rooms",
        }
    return {
        "manifest_version": "simuhome-evening-thermal-evaluator-3.0.0",
        "thermal_evaluator_v3": {
            "protocol_version": "thermal_evaluator_v3",
            "room_bindings": {room: f"/public_state/temperature.{room}" for room in target_rooms},
            "active_window": {"start_inclusive": start, "release_exclusive": release},
            "temperature_unit": "C",
            "acceptable_band_c": {"lower": str(LOWER_C), "upper": str(UPPER_C)},
            "max_staleness_seconds": "0",
        },
        "public_preferences": {"visibility": "public", "target_c": str(TARGET_C)},
        "nrg": {"definition_version": "band-sat-nrg-1", "gamma": "0.10", "min_discrete_units": 2},
        "optional_metrics": optional_metrics,
    }


def evaluator_trace(run: RunArtifact, sample_minutes: int, energy_room_ids: list[str] | None = None) -> dict[str, Any]:
    all_observations = observation_frames(run)
    observations = all_observations[:-1] if len(all_observations) > 1 else all_observations
    backend_by_index = {
        item["index"]: item["value"]
        for item in run.private_trace
        if item["type"] == "backend_state"
    }
    immediate_by_index = {
        item["index"]: item["feedback"].get("applied_commands", [])
        for item in run.private_trace
        if item["type"] == "action_result" and item["accepted"]
    }
    frames = []
    for index, observation in enumerate(observations):
        applied = list(immediate_by_index.get(index, []))
        if index + 1 in backend_by_index:
            applied.extend(backend_by_index[index + 1].get("applied_commands", []))
        unique = {canonical(item): item for item in applied}
        stamp = observation["virtual_time"]
        next_devices = all_observations[index + 1]["devices"] if index + 1 < len(all_observations) else observation["devices"]
        energy_rooms = sorted(next_devices) if energy_room_ids is None else energy_room_ids
        interval_energy = [next_devices[room].get("last_interval_energy_proxy_wh") for room in energy_rooms]
        energy_known = bool(interval_energy) and all(value is not None for value in interval_energy)
        frames.append({
            "frame_index": index,
            "timestamp": stamp,
            "duration_to_next_seconds": sample_minutes * 60,
            "public_state": {
                f"temperature.{room}": {
                    "value": item["temperature_c"],
                    "unit": "C",
                    "quality": "fresh",
                    "observed_at": stamp,
                }
                for room, item in observation["rooms"].items()
            },
            "private_evaluator_primitives": {
                "energy_proxy_wh_interval": {
                    "value": sum(float(value) for value in interval_energy) if energy_known else None,
                    "unit": "Wh",
                    "quality": "fresh" if energy_known else "missing",
                    "observed_at": stamp,
                }
            },
            "applied_commands": list(unique.values()),
        })
    return {"frames": frames}


def physical_windows() -> list[dict[str, Any]]:
    report = json.loads(SCAN_REPORT.read_text(encoding="utf-8"))
    if report.get("status") != "candidate_scan_only" or report.get("formal_episode_count") != 0:
        raise RuntimeError("non-saturated scan is not a provisional-only artifact")
    candidates = [row for row in report["raw_gap_rows"] if row.get("recommended_for_formal_admission") is True]
    windows: list[dict[str, Any]] = []
    for row in candidates:
        responsibility_id = FAMILY_RESPONSIBILITY[row["family"]]
        path = SIMUHOME_DATA / row["source_file"]
        if sha256_file(path) != row["source_sha256"]:
            raise RuntimeError(f"candidate source hash mismatch: {path.name}")
        config = json.loads(path.read_text(encoding="utf-8"))["initial_home_config"]
        if digest(config) != row["source_config_sha256"]:
            raise RuntimeError(f"candidate config hash mismatch: {path.name}")
        target_rooms = sorted(row["room_ids"])
        trajectory_lengths = {len(values) for values in row["noop"]["trajectory_c"].values()}
        if len(trajectory_lengths) != 1:
            raise RuntimeError("candidate rooms have inconsistent horizon lengths")
        windows.append({
            "responsibility_id": responsibility_id,
            "legacy_episode_id": f"scan_v1.{row['family']}.{path.stem}",
            "legacy_physical_process_id": row["responsibility_process_signature"],
            "source_path": path,
            "source_config_sha256": row["source_config_sha256"],
            "config": config,
            "visible_room_ids": thermal_rooms(config),
            "target_room_ids": target_rooms,
            "sample_minutes": 15,
            "horizon_steps": trajectory_lengths.pop(),
            "scan_reference_gap": row["reference_gap"],
            "scan_improved_obligation_units": row["improved_obligation_units"],
        })
    return sorted(windows, key=lambda row: (row["responsibility_id"], row["legacy_episode_id"]))


def build(out: Path = OUTPUT) -> dict[str, Any]:
    mapping = json.loads(MAPPING.read_text(encoding="utf-8"))
    full_ids = {row["responsibility_id"] for row in mapping["mappings"] if row["support_status"] == "FULL"}
    if full_ids != set(FAMILY_RESPONSIBILITY.values()):
        raise RuntimeError(f"FULL responsibility coverage changed: {sorted(full_ids)}")
    query_rows = load_jsonl(QUERIES)
    if {row["responsibility_id"] for row in query_rows} != full_ids:
        raise RuntimeError("reviewed Query coverage does not equal FULL mapping")
    by_responsibility = {row["responsibility_id"]: row for row in query_rows}
    query_to_responsibility = {
        variant["text"]: row["responsibility_id"]
        for row in query_rows
        for variant in row["query_variants"]
    }

    public_rows: list[dict[str, Any]] = []
    private_rows: list[dict[str, Any]] = []
    gate_rows: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for index, window in enumerate(physical_windows()):
        rid = window["responsibility_id"]
        query_row = by_responsibility[rid]
        variants = query_row["query_variants"]
        primary = variants[index % len(variants)]
        other = next(row for row in query_rows if row["responsibility_id"] != rid)
        request_context: dict[str, Any] = {}
        requirements = set(query_row["public_context_requirements"])
        if "room_refs" in requirements:
            request_context["room_refs"] = {"target_room_ids": window["target_room_ids"]}
        if "named_period_refs" in requirements:
            request_context["named_period_refs"] = {
                "evening": {"start_local": "17:00", "end_local": "23:00", "end_inclusive": False}
            }
        primary_query = {"text": primary["text"], "language": "en", "context": request_context}
        alternate_variants = {
            item["form"]: {"text": item["text"], "language": "en", "context": deepcopy(request_context)}
            for item in variants if item["query_id"] != primary["query_id"]
        }
        shuffled_context: dict[str, Any] = {
            "named_period_refs": {"evening": {"start_local": "17:00", "end_local": "23:00", "end_inclusive": False}}
        }
        if "room_refs" in set(other["public_context_requirements"]):
            shuffled_context["room_refs"] = {
                "target_room_ids": window["visible_room_ids"] if len(window["visible_room_ids"]) >= 2 else []
            }
        alternate_variants.update({
            "deleted": {"text": "", "language": "en", "context": deepcopy(request_context)},
            "shuffled": {"text": other["query_variants"][0]["text"], "language": "en", "context": shuffled_context},
        })
        episode_id = "harness_v2_full__" + digest({
            "responsibility_id": rid,
            "physical_process_id": window["legacy_physical_process_id"],
            "query_id": primary["query_id"],
        })[:20]
        bootstrap = {
            "query": primary_query,
            "track": "explicit_profile_control",
            "user_preferences": {
                "thermal_target_c": TARGET_C,
                "acceptable_temperature_c": {"lower": LOWER_C, "upper": UPPER_C},
            },
            "horizon_steps": window["horizon_steps"],
            "decision_interval_minutes": window["sample_minutes"],
            "allowed_action_kinds": ["act", "wait"],
            "interaction_budget": {"remaining_questions": 0},
        }
        episode = EpisodeSpec(episode_id, bootstrap, seed=index, max_decisions=window["horizon_steps"] + 1)

        def run_one(spec: EpisodeSpec, policy: Any) -> RunArtifact:
            backend = SimuHomeHarnessAdapter(window["config"], window["visible_room_ids"], sample_minutes=window["sample_minutes"])
            return Harness(backend).run_one(spec, policy)

        scorer = lambda run, rooms=window["target_room_ids"]: score(run, rooms)
        policies = {
            "agent": lambda: ReviewedQueryPolicy(query_to_responsibility),
            "noop": WaitPolicy,
            "oracle": lambda rooms=window["target_room_ids"]: ReactiveThermalPolicy(rooms, TARGET_C),
            "contrast": lambda rooms=window["target_room_ids"], config=window["config"]: ReactiveThermalPolicy(
                rooms,
                {
                    room: UPPER_C if float(config["rooms"][room]["state"]["temperature"]) / 100 <= TARGET_C else LOWER_C
                    for room in rooms
                },
            ),
        }
        suite = EpisodeValidationSuite(run_one, scorer)
        initial_observation = SimuHomeHarnessAdapter(
            window["config"], window["visible_room_ids"], sample_minutes=window["sample_minutes"]
        ).reset(episode).public_observation
        capability_manifest = thermal_capability_manifest(initial_observation)
        bootstrap["capability_manifest"] = capability_manifest
        episode = EpisodeSpec(episode_id, bootstrap, seed=index, max_decisions=window["horizon_steps"] + 1)
        evidence = suite.collect(episode, policies=policies, query_variants=alternate_variants)
        replicate = suite.collect(episode, policies=policies, query_variants=alternate_variants)
        scores = dict(evidence.scores)
        metric_manifest = evaluator_manifest(
            evidence.runs["agent"], window["target_room_ids"], window["visible_room_ids"]
        )
        v3 = evaluate_thermal_v3(
            metric_manifest,
            evaluator_trace(evidence.runs["agent"], window["sample_minutes"], window["target_room_ids"]),
            evaluator_trace(evidence.runs["noop"], window["sample_minutes"], window["target_room_ids"]),
            evaluator_trace(evidence.runs["oracle"], window["sample_minutes"], window["target_room_ids"]),
        )
        reference_gap = v3.reference.band_satisfaction - v3.noop.band_satisfaction
        normalized_gain = v3.formal_nrg.value if v3.formal_nrg.availability == "available" else None
        paraphrase_names = [f"query:{item['form']}" for item in variants if item["query_id"] != primary["query_id"]]
        gates = {
            "all_runs_completed": all(run.status == "completed" for run in evidence.runs.values()),
            "deterministic_replay": all(evidence.runs[name].trace_digest == replicate.runs[name].trace_digest for name in evidence.runs),
            "reference_band_maintenance_pass": v3.reference.band_maintenance_pass,
            "agent_band_maintenance_pass": v3.agent.band_maintenance_pass,
            "noop_reference_gap_strict": reference_gap > Decimal("0.10"),
            "minimum_obligation_units": v3.agent.discrete_units >= 2,
            "formal_nrg_available": v3.formal_nrg.availability == "available",
            "agent_matches_oracle_score": abs(scores["agent"] - scores["oracle"]) < 1e-9,
            "action_sensitive": len({action_signature(evidence.runs[name]) for name in ("agent", "noop", "contrast")}) == 3,
            "trajectory_sensitive": trajectory_signature(evidence.runs["oracle"], window["target_room_ids"]) != trajectory_signature(evidence.runs["noop"], window["target_room_ids"]),
            "paraphrase_action_equivalent": all(action_signature(evidence.runs[name]) == action_signature(evidence.runs["agent"]) for name in paraphrase_names),
            "query_deletion_sensitive": action_signature(evidence.runs["query:deleted"]) != action_signature(evidence.runs["agent"]),
            "query_shuffle_sensitive": action_signature(evidence.runs["query:shuffled"]) != action_signature(evidence.runs["agent"]),
            "normalized_gain_defined": normalized_gain is not None,
            "full_home_thermal_view": set(window["visible_room_ids"]) == set(observation_frames(evidence.runs["agent"])[0]["rooms"]),
            "scan_provisional_gate_was_valid": window["scan_reference_gap"] > 0.10 and window["scan_improved_obligation_units"] >= 2,
            "capability_manifest_is_public_authority": capability_manifest["action_kinds"] == bootstrap["allowed_action_kinds"],
        }
        gates["passed"] = all(gates.values())
        gate_row = {
            "episode_id": episode_id,
            "responsibility_id": rid,
            "physical_process_id": window["legacy_physical_process_id"],
            "query_diagnostic": {"primary": primary_query, "shuffled": alternate_variants["shuffled"]},
            "scores": scores,
            "evaluator_v3": jsonable(v3.to_dict()),
            "scan_diagnostics": {
                "reference_gap": window["scan_reference_gap"],
                "improved_obligation_units": window["scan_improved_obligation_units"],
            },
            "action_signatures": {name: action_signature(run) for name, run in evidence.runs.items()},
            "normalized_gain": None if normalized_gain is None else float(normalized_gain),
            "gates": gates,
            "passed": gates["passed"],
        }
        gate_rows.append(gate_row)
        if not gates["passed"]:
            failures.append({"episode_id": episode_id, "failed_gates": [name for name, value in gates.items() if not value]})
            continue
        public_rows.append({
            "schema_version": "harness-v2-full-episode-public-v2",
            "episode_id": episode_id,
            "responsibility_id": rid,
            "query_surface": {"query_id": primary["query_id"], "form": primary["form"]},
            "agent_view": bootstrap,
            "initial_observation": observation_frames(evidence.runs["agent"])[0],
            "visible_room_ids": window["visible_room_ids"],
            "target_scope_provenance": "natural_query" if rid == "rd_split_016151c7c038" else "public_request_context.room_refs.target_room_ids",
            "gold_actions_released": False,
        })
        private_rows.append({
            "schema_version": "harness-v2-full-episode-private-v2",
            "episode_id": episode_id,
            "responsibility_id": rid,
            "physical_process_id": window["legacy_physical_process_id"],
            "source": {
                "source_file": window["source_path"].name,
                "source_file_sha256": sha256_file(window["source_path"]),
                "source_config_sha256": window["source_config_sha256"],
                "legacy_episode_id": window["legacy_episode_id"],
            },
            "target_room_ids": window["target_room_ids"],
            "evaluator_manifest": metric_manifest,
            "validation": gate_row,
            "runs": {name: serialize_run(run) for name, run in evidence.runs.items()},
            "replicate_trace_digests": {name: run.trace_digest for name, run in replicate.runs.items()},
        })

    out.mkdir(parents=True, exist_ok=True)
    public_text = "".join(canonical(row) + "\n" for row in public_rows)
    private_text = "".join(canonical(row) + "\n" for row in private_rows)
    (out / "episodes_public.jsonl").write_text(public_text, encoding="utf-8")
    (out / "episodes_private.jsonl").write_text(private_text, encoding="utf-8")
    gate = {
        "schema_version": "harness-v2-episode-validation-v2",
        "validation_layer": "EpisodeValidationSuite_outside_Harness_Core",
        "candidate_count": len(gate_rows),
        "passed_count": sum(row["passed"] for row in gate_rows),
        "failed_count": sum(not row["passed"] for row in gate_rows),
        "all_passed": bool(gate_rows) and all(row["passed"] for row in gate_rows),
        "episodes": gate_rows,
    }
    (out / "validation_gate.json").write_text(json.dumps(gate, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report = {
        "schema_version": "harness-v2-full-episode-batch-v2",
        "status": "PASS" if gate["all_passed"] else "FAIL_CLOSED",
        "candidate_count": len(gate_rows),
        "episode_count": len(public_rows),
        "responsibility_count": len({row["responsibility_id"] for row in public_rows}),
        "responsibility_episode_counts": {
            rid: sum(row["responsibility_id"] == rid for row in public_rows) for rid in sorted(full_ids)
        },
        "query_variant_count": sum(len(row["query_variants"]) for row in query_rows),
        "validation_failures": failures,
        "public_sha256": hashlib.sha256(public_text.encode("utf-8")).hexdigest(),
        "private_sha256": hashlib.sha256(private_text.encode("utf-8")).hexdigest(),
        "source_mapping_sha256": sha256_file(MAPPING),
        "source_queries_sha256": sha256_file(QUERIES),
        "source_scan_report_sha256": sha256_file(SCAN_REPORT),
        "implementation_hashes": {
            "builder_sha256": sha256_file(Path(__file__)),
            "harness_core_sha256": sha256_file(ROOT / "harness_v2" / "core.py"),
            "validation_suite_sha256": sha256_file(ROOT / "harness_v2" / "episode_validator.py"),
            "simuhome_harness_adapter_sha256": sha256_file(ROOT / "harness_v2" / "simuhome_adapter.py"),
            "capability_protocol_sha256": sha256_file(ROOT / "harness_v2" / "capability_protocol.py"),
            "evaluator_v3_sha256": sha256_file(ROOT / "harness_v2" / "evaluator_v3.py"),
            "thermal_adapter_sha256": sha256_file(ROOT / "unified_compiler" / "simuhome_multiroom_thermal_adapter.py"),
        },
        "runtime": {
            "python": platform.python_version(),
            "pydantic": version("pydantic"),
            "rfc8785": version("rfc8785"),
        },
        "limitations": [
            "SimuHome thermal dynamics are synthetic and not a calibrated building model.",
            "The eleven physical windows came from an independently reviewed provisional non-saturated scan and are freshly certified here.",
            "The reference Query policy is a construction diagnostic, not a deployable benchmark baseline model.",
        ],
    }
    (out / "build_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out / "DATASET_CARD.md").write_text(
        "# Harness V2 FULL Responsibility Episode Batch V1\n\n"
        "Fresh Harness V2 replays for the two responsibilities currently matched FULL. "
        "Harness Core runs one policy on one Episode; no-op/oracle, Query interventions, "
        "action sensitivity, determinism, and admission are computed separately by "
        "EpisodeValidationSuite. Agent-visible input contains the complete public Query DTO, "
        "explicit user thermal preferences, the action protocol, and every controllable thermal "
        "room in the source home; it excludes responsibility IDs, evaluator state, future state, "
        "oracle results, and gold actions.\n\n"
        "This is a provisional synthetic SimuHome batch, not a calibrated-building result.\n",
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    print(json.dumps(build(args.output), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
