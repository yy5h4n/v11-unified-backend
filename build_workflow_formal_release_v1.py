"""Compile and gate the T2 household-workflow portion of the formal release."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from typing import Any

from harness_v2.core import EpisodeSpec, Harness
from harness_v2.workflow_backend import DEFAULT_PUBLIC_PROFILE, START, WorkflowBackend
from harness_v2.workflow_policy import NoOpPolicy, WorkflowReferencePolicy
from harness_v2.trust_registry import runtime_environment
from harness_v2.workflow_trust import (
    TrustedWorkflowRuntime,
    causal_environment_digest,
    build_trusted_evaluation_receipt,
    build_trusted_replay_receipt,
    build_trusted_reset_receipt,
    verify_trusted_evaluation_receipt,
    verify_trusted_replay_receipt,
    verify_trusted_reset_receipt,
)
from validate_formal_dataset_release import (
    EVALUATOR_ID,
    NOOP_POLICY_ID,
    REFERENCE_POLICY_ID,
    RUNTIME_ID,
    _build_code_side_workflow_registry,
)

ROOT = Path(__file__).resolve().parent
CONTRACTS = ROOT / "generated/formal_responsibility_contracts_v1.json"
CATALOG = ROOT / "responsibility_ai_coding_v1/NATURAL_STANDING_INTENT_QUERY_CATALOG_V2_5.json"
QUERY_VARIANTS = ROOT / "responsibility_ai_coding_v1/WORKFLOW_QUERY_VARIANTS_V1.json"
OUTPUT = ROOT / "generated/formal_workflow_release_v1/intervention_required"
VARIANTS_PER_RESPONSIBILITY = 10
def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _final_private(run) -> dict[str, Any]:
    return [row["value"] for row in run.private_trace if row["type"] == "backend_state"][-1]


def _environment_digest(run) -> str:
    return causal_environment_digest(run)


def _private_config(variant: int) -> dict[str, int]:
    return {
        "washer_cycle_seconds": 900 + 60 * variant,
        "vacuum_clean_seconds": 600 + 60 * variant,
        "dishwasher_cycle_seconds": 720 + 60 * variant,
        "fridge_alarm_seconds": 180 + 60 * variant,
        "stove_hazard_seconds": 420 + 60 * variant,
        "bathroom_occupancy_seconds": 180 + 60 * variant,
        "coffee_brew_seconds": 120 + 30 * variant,
        "initial_supply_level": 1 + variant,
    }


def _public_profile(contract: dict[str, Any], config: dict[str, int], variant: int) -> dict[str, Any]:
    requirements = contract.get("profile_requirements", [])
    profile = deepcopy(DEFAULT_PUBLIC_PROFILE)
    profile.update({
        "authorization": "control listed household devices through the published interfaces",
        "weekly_cleaning_due_at": (START + timedelta(minutes=2 + variant)).isoformat().replace("+00:00", "Z"),
        "television_curfew_end": "06:00",
        "intended_wake_at": (
            START
            + timedelta(
                minutes=20
                + 2 * variant
                + (1 if contract.get("scenario_type") == "coffee_ready_at_wake" else 0)
            )
        ).isoformat().replace("+00:00", "Z"),
        "morning_period": {"start": "05:00", "end": "12:00"},
    })
    missing = [field for field in requirements if field not in profile]
    if missing:
        raise RuntimeError(f"no concrete public profile values for {contract.get('scenario_type')}: {missing}")
    if contract.get("scenario_type") == "coffee_ready_at_wake":
        profile["coffee_brew_duration_seconds"] = config["coffee_brew_seconds"]
    return profile


def _episode(
    episode_id: str,
    seed: int,
    query: str | None,
    horizon_seconds: int,
    public_profile: dict[str, Any],
) -> EpisodeSpec:
    public_bootstrap = {
        "public_profile": deepcopy(public_profile),
        "horizon_seconds": horizon_seconds,
        "allowed_action_kinds": ["act", "wait", "install_rule", "cancel_rule", "ask"],
    }
    if query is not None:
        public_bootstrap["query"] = query
    return EpisodeSpec(
        episode_id=episode_id,
        public_bootstrap=public_bootstrap,
        seed=seed,
        max_decisions=min(128, max(80, horizon_seconds // 3600 + 20)),
    )


def _trusted_arm(
    runtime: TrustedWorkflowRuntime,
    spec: EpisodeSpec,
    *,
    policy_id: str,
    scenario: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], Any, dict[str, Any]]:
    """Reset, execute, replay, and evaluate one intervention arm fail-closed."""

    reset = build_trusted_reset_receipt(runtime, spec)
    verify_trusted_reset_receipt(reset, runtime, spec)
    replay = build_trusted_replay_receipt(
        runtime,
        spec,
        policy_id,
        reset_receipt_digest=reset["receipt_digest"],
    )
    run = verify_trusted_replay_receipt(
        replay,
        runtime,
        spec,
        expected_policy_id=policy_id,
        expected_reset_receipt_digest=reset["receipt_digest"],
    )
    evaluation = build_trusted_evaluation_receipt(runtime, EVALUATOR_ID, scenario, run)
    score = verify_trusted_evaluation_receipt(
        evaluation,
        runtime,
        EVALUATOR_ID,
        scenario,
        run,
    )
    return reset, replay, evaluation, run, score


def build() -> dict[str, Any]:
    contract_data = json.loads(CONTRACTS.read_text(encoding="utf-8"))
    query_variant_data = json.loads(QUERY_VARIANTS.read_text(encoding="utf-8"))
    query_variants = query_variant_data["items"]
    routes = [row for row in contract_data["routes"] if row["backend_route"] == "household_workflow_t2"]
    incomplete = {row["responsibility_id"]: row["capability_match"]["missing_requirements"] for row in routes if row["support_status"] != "FULL"}
    if incomplete:
        raise RuntimeError(json.dumps({"formal_release_blocked_by_partial_capability_matches": incomplete}, indent=2, sort_keys=True))
    route_ids = {row["responsibility_id"] for row in routes}
    if set(query_variants) != route_ids:
        raise RuntimeError(
            f"query variant coverage mismatch: missing={sorted(route_ids - set(query_variants))}, "
            f"extra={sorted(set(query_variants) - route_ids)}"
        )
    for responsibility_id, variants in query_variants.items():
        if len(variants) < 4 or len(set(variants)) != len(variants) or any(not isinstance(text, str) or not text.strip() for text in variants):
            raise RuntimeError(f"{responsibility_id} must have at least four unique non-empty Query variants")
    public_rows, private_rows, failures = [], [], []
    implementation_hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in [
            Path(__file__),
            ROOT / "harness_v2/core.py",
            ROOT / "harness_v2/workflow_backend.py",
            ROOT / "harness_v2/workflow_policy.py",
            ROOT / "harness_v2/workflow_evaluator.py",
            ROOT / "harness_v2/workflow_trust.py",
            ROOT / "harness_v2/trust_registry.py",
            ROOT / "harness_v2/trust_evidence.py",
            ROOT / "harness_v2/semantic_validator.py",
        ]
    }
    query_to_scenario = {
        query: route["contract"]["scenario_type"]
        for route in routes
        for query in query_variants[route["responsibility_id"]]
    }
    if len(query_to_scenario) != sum(len(items) for items in query_variants.values()):
        raise RuntimeError("a Query variant maps to more than one responsibility")
    seen_probe_noop_environments: set[str] = set()
    seen_probe_reference_environments: set[str] = set()
    for route_index, route in enumerate(routes):
        query_options = query_variants[route["responsibility_id"]]
        scenario = route["contract"]["scenario_type"]
        horizon_seconds = int(route["contract"]["minimum_horizon_seconds"])
        if horizon_seconds <= 0 or horizon_seconds > 604800:
            raise RuntimeError(f"invalid minimum horizon for {route['responsibility_id']}: {horizon_seconds}")
        for variant in range(VARIANTS_PER_RESPONSIBILITY):
            query = query_options[variant % len(query_options)]
            episode_id = f"wf_{_sha({'release': 'v1', 'route_index': route_index, 'variant': variant})[:20]}"
            config = _private_config(variant)
            public_profile = _public_profile(route["contract"], config, variant)
            shuffled_query = query_variants[routes[(route_index + 1) % len(routes)]["responsibility_id"]][variant % 4]
            runtime = _build_code_side_workflow_registry(
                scenario=scenario,
                config=config,
                horizon_seconds=horizon_seconds,
            ).require(RUNTIME_ID)
            seed_base = 1000 + variant * 97 + int(route["responsibility_id"][-4:], 16)
            for seed_attempt in range(2048):
                seed = seed_base + seed_attempt * 7919
                probe_spec = _episode(episode_id, seed, query, horizon_seconds, public_profile)
                noop_probe = Harness(runtime.backend_factory()).run_one(probe_spec, NoOpPolicy())
                reference_probe = Harness(runtime.backend_factory()).run_one(
                    probe_spec,
                    WorkflowReferencePolicy(scenario),
                )
                noop_probe_digest = _environment_digest(noop_probe)
                reference_probe_digest = _environment_digest(reference_probe)
                if (
                    noop_probe_digest not in seen_probe_noop_environments
                    and reference_probe_digest not in seen_probe_reference_environments
                ):
                    seen_probe_noop_environments.add(noop_probe_digest)
                    seen_probe_reference_environments.add(reference_probe_digest)
                    break
            else:
                raise RuntimeError(f"could not find a distinct causal process for {scenario} variant {variant}")
            spec = _episode(episode_id, seed, query, horizon_seconds, public_profile)
            deleted_spec = _episode(episode_id, seed, None, horizon_seconds, public_profile)
            shuffled_spec = _episode(episode_id, seed, shuffled_query, horizon_seconds, public_profile)
            arm_inputs = {
                "reference": (spec, REFERENCE_POLICY_ID),
                "repeat": (spec, REFERENCE_POLICY_ID),
                "noop": (spec, NOOP_POLICY_ID),
                "query_deleted": (deleted_spec, REFERENCE_POLICY_ID),
                "query_shuffled": (shuffled_spec, REFERENCE_POLICY_ID),
            }
            trusted_resets: dict[str, Any] = {}
            trusted_replays: dict[str, Any] = {}
            trusted_evaluations: dict[str, Any] = {}
            runs: dict[str, Any] = {}
            scores: dict[str, Any] = {}
            for arm, (arm_spec, policy_id) in arm_inputs.items():
                reset, replay, evaluation, run, score = _trusted_arm(
                    runtime,
                    arm_spec,
                    policy_id=policy_id,
                    scenario=scenario,
                )
                trusted_resets[arm] = reset
                trusted_replays[arm] = replay
                trusted_evaluations[arm] = evaluation
                runs[arm] = run
                scores[arm] = score
            reference_a = runs["reference"]
            reference_b = runs["repeat"]
            noop = runs["noop"]
            query_deleted = runs["query_deleted"]
            query_shuffled = runs["query_shuffled"]
            reference_score = scores["reference"]
            noop_score = scores["noop"]
            deleted_score = scores["query_deleted"]
            shuffled_score = scores["query_shuffled"]
            reference_environment_digest = _environment_digest(reference_a)
            repeat_environment_digest = _environment_digest(reference_b)
            noop_environment_digest = _environment_digest(noop)
            gates = {
                "reference_success": reference_score["success"],
                "noop_fails": not noop_score["success"],
                "query_deletion_fails": not deleted_score["success"],
                "query_shuffle_fails": not shuffled_score["success"],
                "deterministic_replay": reference_a.trace_digest == reference_b.trace_digest and reference_environment_digest == repeat_environment_digest,
                "action_sensitive": reference_environment_digest != noop_environment_digest,
            }
            if not all(gates.values()):
                failures.append({"episode_id": spec.episode_id, "gates": gates, "reference": reference_score, "noop": noop_score})
                continue
            reset_payload = {
                "episode_id": spec.episode_id,
                "seed": spec.seed,
                "public_bootstrap": spec.public_bootstrap,
                "private_scenario_type": scenario,
                "private_workflow_config": config,
                "private_horizon_seconds": horizon_seconds,
                "implementation_hashes": implementation_hashes,
            }
            initial_private = reference_a.private_trace[0]["value"]
            process_payload = {
                "native_initial_devices": initial_private["devices"],
                "native_initial_household": initial_private["household"],
                "native_initial_workflow": initial_private["workflow"],
                "actual_exogenous_realization": initial_private["exogenous_pending"],
                "backend_effective_config": config,
                "horizon_seconds": horizon_seconds,
                "causal_noop_environment_digest": noop_environment_digest,
            }
            initial = reference_a.public_trace[0]["value"]
            public_rows.append({
                "episode_id": spec.episode_id,
                "query": query,
                "public_profile": spec.public_bootstrap["public_profile"],
                "initial_observation": initial,
                "allowed_action_kinds": spec.public_bootstrap["allowed_action_kinds"],
                "backend_fidelity_tier": "T2",
            })
            private_rows.append({
                "episode_id": spec.episode_id,
                "responsibility_id": route["responsibility_id"],
                "support_status": "FULL",
                "dataset_track": "intervention_required",
                "backend_id": route["backend_id"],
                "backend_fidelity_tier": "T2",
                "contract": route["contract"],
                "contract_digest": route["contract_digest"],
                "process_digest": noop_environment_digest,
                "process_receipt": {"digest": _sha(process_payload), "payload": process_payload},
                "reset_receipt": {"digest": _sha(reset_payload), "payload": reset_payload},
                "trusted_runtime": {
                    "runtime_id": runtime.runtime_id,
                    "version": runtime.version,
                    "registry_entry_digest": runtime.registry_entry_digest,
                },
                "trusted_reset_receipt": trusted_resets["reference"],
                "trusted_replays": trusted_replays,
                "trusted_evaluations": trusted_evaluations,
                "query_interventions": {"shuffled_query": shuffled_query},
                "replay_receipt": {
                    "reference_trace_digest": reference_a.trace_digest,
                    "repeat_trace_digest": reference_b.trace_digest,
                    "noop_trace_digest": noop.trace_digest,
                    "query_deleted_trace_digest": query_deleted.trace_digest,
                    "query_shuffled_trace_digest": query_shuffled.trace_digest,
                    "reference_environment_digest": reference_environment_digest,
                    "repeat_environment_digest": repeat_environment_digest,
                    "noop_environment_digest": noop_environment_digest,
                    "query_deleted_environment_digest": _environment_digest(query_deleted),
                    "query_shuffled_environment_digest": _environment_digest(query_shuffled),
                },
                "admission_receipt": {
                    "gates": gates,
                    "reference_score": reference_score,
                    "noop_score": noop_score,
                    "query_deleted_score": deleted_score,
                    "query_shuffled_score": shuffled_score,
                },
                "evaluator_binding": {"id": EVALUATOR_ID, "cost_metric": "action_cost", "cost_unit": "action_unit"},
            })
    if failures:
        raise RuntimeError(json.dumps({"workflow_admission_failures": failures[:10], "count": len(failures)}, indent=2))
    reference_environments = [row["replay_receipt"]["reference_environment_digest"] for row in private_rows]
    noop_environments = [row["replay_receipt"]["noop_environment_digest"] for row in private_rows]
    if len(set(reference_environments)) != len(reference_environments):
        groups: dict[str, list[str]] = defaultdict(list)
        for row in private_rows:
            groups[row["replay_receipt"]["reference_environment_digest"]].append(row["episode_id"])
        raise RuntimeError(json.dumps({"duplicate_realized_reference_environments": [items for items in groups.values() if len(items) > 1]}, indent=2))
    if len(set(noop_environments)) != len(noop_environments):
        groups = defaultdict(list)
        for row in private_rows:
            groups[row["replay_receipt"]["noop_environment_digest"]].append(row["episode_id"])
        raise RuntimeError(json.dumps({"duplicate_realized_noop_environments": [items for items in groups.values() if len(items) > 1]}, indent=2))
    OUTPUT.mkdir(parents=True, exist_ok=True)
    public_path, private_path = OUTPUT / "episodes_public.jsonl", OUTPUT / "episodes_private.jsonl"
    public_path.write_text("".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in public_rows), encoding="utf-8")
    private_path.write_text("".join(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n" for row in private_rows), encoding="utf-8")
    manifest = {
        "schema_version": "formal-workflow-release-v1",
        "split_policy": "none",
        "dataset_track": "intervention_required",
        "statistics": {
            "responsibility_count": len({row["responsibility_id"] for row in private_rows}),
            "episode_count": len(private_rows),
            "unique_process_count": len({row["process_digest"] for row in private_rows}),
            "unique_reference_environment_count": len(set(reference_environments)),
            "unique_noop_environment_count": len(set(noop_environments)),
            "unique_query_count": len({row["query"] for row in public_rows}),
            "minimum_query_variants_per_responsibility": min(
                len({public_rows[index]["query"] for index, private in enumerate(private_rows) if private["responsibility_id"] == responsibility_id})
                for responsibility_id in route_ids
            ),
        },
        "query_variant_source": str(QUERY_VARIANTS.relative_to(ROOT)) if QUERY_VARIANTS.is_relative_to(ROOT) else str(QUERY_VARIANTS),
        "query_variant_source_sha256": hashlib.sha256(QUERY_VARIANTS.read_bytes()).hexdigest(),
        "implementation_hashes": implementation_hashes,
        "runtime_environment": runtime_environment(),
        "public_sha256": hashlib.sha256(public_path.read_bytes()).hexdigest(),
        "private_sha256": hashlib.sha256(private_path.read_bytes()).hexdigest(),
        "status": "PASS",
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    print(json.dumps(build(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
