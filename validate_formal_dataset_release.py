"""Fail-closed validator for the formal responsibility dataset release."""

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from collections import Counter
from pathlib import Path
from typing import Any

from harness_v2.core import EpisodeSpec
from harness_v2.semantic_validator import ConformanceError
from harness_v2.trust_evidence import decode_serialization
from harness_v2.trust_registry import package_digest, runtime_environment, sha256_value
from harness_v2.workflow_backend import WorkflowBackend
from harness_v2.workflow_evaluator import evaluate_workflow, evaluator_primitive_evidence
from harness_v2.workflow_policy import NoOpPolicy, QueryConditionedReferencePolicy
from harness_v2.workflow_scenario_registry import WORKFLOW_SCENARIO_REGISTRY
from harness_v2.workflow_trust import (
    TrustedWorkflowEvaluator,
    TrustedWorkflowPolicy,
    TrustedWorkflowRuntime,
    WorkflowTrustRegistry,
    build_trusted_reset_receipt,
    causal_environment_digest,
    verify_trusted_evaluation_receipt,
    verify_trusted_replay_receipt,
    verify_trusted_reset_receipt,
)


ROOT = Path(__file__).resolve().parent
QUERY_VARIANTS = ROOT / "responsibility_ai_coding_v1/WORKFLOW_QUERY_VARIANTS_V1.json"
RUNTIME_ID = "workflow.runtime.v2"
RUNTIME_VERSION = "2"
REFERENCE_POLICY_ID = "workflow.reference.v2"
NOOP_POLICY_ID = "workflow.noop.v2"
EVALUATOR_ID = "workflow.responsibility.v2"
TRUST_ARMS = ("reference", "repeat", "noop", "query_deleted", "query_shuffled")


class ReleaseValidationError(ValueError):
    pass


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(*(_keys(item) for item in value.values()), set())
    if isinstance(value, list):
        return set().union(*(_keys(item) for item in value), set())
    return set()


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ReleaseValidationError(f"{path}:{line_number} is not an object")
            rows.append(value)
    return rows


def _environment_digest(run: Any) -> str:
    return causal_environment_digest(run)


def _trusted_query_to_scenario() -> dict[str, str]:
    """Load the code-side Query registry; bundle mappings are never executable."""

    raw = json.loads(QUERY_VARIANTS.read_text(encoding="utf-8"))
    variants = raw.get("items", raw.get("variants", raw))
    if not isinstance(variants, dict):
        raise ReleaseValidationError("trusted Query variant source is malformed")
    responsibility_to_scenario = {
        spec["responsibility_id"]: scenario
        for scenario, spec in WORKFLOW_SCENARIO_REGISTRY.items()
    }
    result: dict[str, str] = {}
    for responsibility_id, queries in variants.items():
        scenario = responsibility_to_scenario.get(responsibility_id)
        if scenario is None:
            continue
        if not isinstance(queries, list) or any(not isinstance(query, str) or not query.strip() for query in queries):
            raise ReleaseValidationError(f"trusted Query variants are malformed for {responsibility_id}")
        for query in queries:
            if query in result and result[query] != scenario:
                raise ReleaseValidationError("trusted Query text maps to multiple responsibilities")
            result[query] = scenario
    return result


def _workflow_package_digests() -> dict[str, str]:
    core = ROOT / "harness_v2/core.py"
    backend = ROOT / "harness_v2/workflow_backend.py"
    policy = ROOT / "harness_v2/workflow_policy.py"
    evaluator = ROOT / "harness_v2/workflow_evaluator.py"
    trust = ROOT / "harness_v2/workflow_trust.py"
    trust_registry = ROOT / "harness_v2/trust_registry.py"
    trust_evidence = ROOT / "harness_v2/trust_evidence.py"
    semantic_validator = ROOT / "harness_v2/semantic_validator.py"
    registry = ROOT / "harness_v2/workflow_scenario_registry.py"
    return {
        "backend": package_digest([backend], package_id="workflow-backend", version=RUNTIME_VERSION),
        "harness": package_digest(
            [core, trust_registry, trust_evidence, semantic_validator],
            package_id="harness-v2", version=RUNTIME_VERSION,
        ),
        "policy": package_digest([policy, QUERY_VARIANTS], package_id="workflow-policy", version=RUNTIME_VERSION),
        "evaluator": package_digest([evaluator, registry], package_id="workflow-evaluator", version=RUNTIME_VERSION),
        "runtime": package_digest(
            [
                core,
                backend,
                policy,
                evaluator,
                trust,
                trust_registry,
                trust_evidence,
                semantic_validator,
                registry,
                QUERY_VARIANTS,
            ],
            package_id="workflow-runtime", version=RUNTIME_VERSION,
        ),
    }


def _build_code_side_workflow_registry(
    *, scenario: str, config: dict[str, Any], horizon_seconds: int,
) -> WorkflowTrustRegistry:
    query_to_scenario = _trusted_query_to_scenario()
    digests = _workflow_package_digests()
    reference = TrustedWorkflowPolicy(
        policy_id=REFERENCE_POLICY_ID,
        package_digest=digests["policy"],
        config={"query_to_scenario": query_to_scenario},
        factory=lambda value: QueryConditionedReferencePolicy(value["query_to_scenario"]),
    )
    noop = TrustedWorkflowPolicy(
        policy_id=NOOP_POLICY_ID,
        package_digest=digests["policy"],
        config={"wait_seconds": None},
        factory=lambda value: NoOpPolicy(value["wait_seconds"]),
    )
    evaluator = TrustedWorkflowEvaluator(
        evaluator_id=EVALUATOR_ID,
        package_digest=digests["evaluator"],
        manifest={"handler_registry": "workflow_evaluator.EVALUATOR_HANDLERS", "version": RUNTIME_VERSION},
        evaluate=lambda scenario_type, run: evaluate_workflow(scenario_type, run),
    )
    runtime = TrustedWorkflowRuntime(
        runtime_id=RUNTIME_ID,
        version=RUNTIME_VERSION,
        package_digest=digests["runtime"],
        backend_package_digest=digests["backend"],
        harness_package_digest=digests["harness"],
        backend_factory=lambda: WorkflowBackend(
            private_scenario_type=scenario,
            private_config=deepcopy(config),
            private_horizon_seconds=horizon_seconds,
        ),
        policies={REFERENCE_POLICY_ID: reference, NOOP_POLICY_ID: noop},
        evaluators={EVALUATOR_ID: evaluator},
    )
    return WorkflowTrustRegistry([runtime])


def _episode_from_receipt(receipt: dict[str, Any], expected_bootstrap: dict[str, Any]) -> EpisodeSpec:
    if not isinstance(receipt, dict) or not isinstance(receipt.get("episode_spec"), dict):
        raise ReleaseValidationError("trusted reset receipt lacks serialized EpisodeSpec")
    try:
        value = decode_serialization(receipt["episode_spec"], "workflow EpisodeSpec")
    except ConformanceError as exc:
        raise ReleaseValidationError(str(exc)) from exc
    if not isinstance(value, dict) or set(value) != {"episode_id", "public_bootstrap", "seed", "max_decisions"}:
        raise ReleaseValidationError("trusted EpisodeSpec has an invalid shape")
    if value["public_bootstrap"] != expected_bootstrap:
        raise ReleaseValidationError("trusted EpisodeSpec public bootstrap does not match public release")
    seed, max_decisions = value["seed"], value["max_decisions"]
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ReleaseValidationError("trusted EpisodeSpec seed must be an integer")
    if isinstance(max_decisions, bool) or not isinstance(max_decisions, int) or not 1 <= max_decisions <= 128:
        raise ReleaseValidationError("trusted EpisodeSpec max_decisions is outside [1, 128]")
    return EpisodeSpec(value["episode_id"], deepcopy(value["public_bootstrap"]), seed, max_decisions)


def _validate_machine_contract(row: dict[str, Any]) -> str:
    responsibility_id = row.get("responsibility_id")
    matches = [
        (scenario, spec) for scenario, spec in WORKFLOW_SCENARIO_REGISTRY.items()
        if spec["responsibility_id"] == responsibility_id
    ]
    if len(matches) != 1:
        raise ReleaseValidationError(f"{row.get('episode_id')} is not bound to one trusted workflow responsibility")
    scenario, expected = matches[0]
    contract = row.get("contract")
    if not isinstance(contract, dict):
        raise ReleaseValidationError(f"{row.get('episode_id')} lacks a Contract")
    for key, value in expected.items():
        if contract.get(key) != value:
            raise ReleaseValidationError(f"{row.get('episode_id')} Contract diverges from code-side {key}")
    return scenario


def _validate_trusted_episode(public_row: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    episode_id = row["episode_id"]
    scenario = _validate_machine_contract(row)
    process = row.get("process_receipt", {}).get("payload", {})
    config = process.get("backend_effective_config")
    horizon = process.get("horizon_seconds")
    if not isinstance(config, dict):
        raise ReleaseValidationError(f"{episode_id} lacks backend effective config")
    minimum_horizon = WORKFLOW_SCENARIO_REGISTRY[scenario]["minimum_horizon_seconds"]
    if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon < minimum_horizon:
        raise ReleaseValidationError(f"{episode_id} horizon is shorter than its trusted Contract")
    query = public_row.get("query")
    query_map = _trusted_query_to_scenario()
    if not isinstance(query, str) or query_map.get(query) != scenario:
        raise ReleaseValidationError(f"{episode_id} Query is not registered for its responsibility")
    interventions = row.get("query_interventions")
    shuffled_query = interventions.get("shuffled_query") if isinstance(interventions, dict) else None
    if not isinstance(shuffled_query, str) or shuffled_query not in query_map or query_map[shuffled_query] == scenario:
        raise ReleaseValidationError(f"{episode_id} Query shuffle is not a registered cross-responsibility intervention")
    base_bootstrap = {
        "query": query,
        "public_profile": deepcopy(public_row.get("public_profile")),
        "horizon_seconds": horizon,
        "allowed_action_kinds": deepcopy(public_row.get("allowed_action_kinds")),
    }
    reset_receipt = row.get("trusted_reset_receipt")
    episode = _episode_from_receipt(reset_receipt, base_bootstrap)
    if episode.episode_id != episode_id:
        raise ReleaseValidationError(f"{episode_id} trusted EpisodeSpec ID mismatch")
    registry = _build_code_side_workflow_registry(scenario=scenario, config=config, horizon_seconds=horizon)
    binding = row.get("trusted_runtime")
    if not isinstance(binding, dict) or set(binding) != {"runtime_id", "version", "registry_entry_digest"}:
        raise ReleaseValidationError(f"{episode_id} has malformed trusted runtime binding")
    try:
        runtime = registry.require(binding["runtime_id"])
    except ConformanceError as exc:
        raise ReleaseValidationError(f"{episode_id}: {exc}") from exc
    if binding["version"] != runtime.version or binding["registry_entry_digest"] != runtime.registry_entry_digest:
        raise ReleaseValidationError(f"{episode_id} trusted runtime binding mismatch")
    try:
        verify_trusted_reset_receipt(reset_receipt, runtime, episode)
    except ConformanceError as exc:
        raise ReleaseValidationError(f"{episode_id}: {exc}") from exc
    try:
        trusted_initial_public = decode_serialization(
            reset_receipt["initial_public_observation"], "workflow initial public observation"
        )
    except ConformanceError as exc:
        raise ReleaseValidationError(f"{episode_id}: {exc}") from exc
    if public_row.get("initial_observation") != trusted_initial_public:
        raise ReleaseValidationError(f"{episode_id} public initial observation disagrees with trusted reset")
    replays, evaluations = row.get("trusted_replays"), row.get("trusted_evaluations")
    if not isinstance(replays, dict) or set(replays) != set(TRUST_ARMS):
        raise ReleaseValidationError(f"{episode_id} trusted replay arms are incomplete")
    if not isinstance(evaluations, dict) or set(evaluations) != set(TRUST_ARMS):
        raise ReleaseValidationError(f"{episode_id} trusted evaluation arms are incomplete")
    deleted_bootstrap = deepcopy(base_bootstrap)
    deleted_bootstrap.pop("query", None)
    specs = {
        "reference": episode,
        "repeat": episode,
        "noop": episode,
        "query_deleted": EpisodeSpec(episode_id, deleted_bootstrap, episode.seed, episode.max_decisions),
        "query_shuffled": EpisodeSpec(episode_id, {**deepcopy(base_bootstrap), "query": shuffled_query}, episode.seed, episode.max_decisions),
    }
    policy_ids = {arm: (NOOP_POLICY_ID if arm == "noop" else REFERENCE_POLICY_ID) for arm in TRUST_ARMS}
    runs, results = {}, {}
    for arm in TRUST_ARMS:
        if arm in {"query_deleted", "query_shuffled"}:
            arm_reset_digest = build_trusted_reset_receipt(runtime, specs[arm])["receipt_digest"]
        else:
            arm_reset_digest = reset_receipt["receipt_digest"]
        try:
            runs[arm] = verify_trusted_replay_receipt(
                replays[arm], runtime, specs[arm], expected_policy_id=policy_ids[arm],
                expected_reset_receipt_digest=arm_reset_digest,
            )
            results[arm] = verify_trusted_evaluation_receipt(
                evaluations[arm], runtime, EVALUATOR_ID, scenario, runs[arm],
            )
        except ConformanceError as exc:
            raise ReleaseValidationError(f"{episode_id} {arm}: {exc}") from exc
    expected_success = {"reference": True, "repeat": True, "noop": False, "query_deleted": False, "query_shuffled": False}
    for arm, expected in expected_success.items():
        if results[arm].get("success") is not expected:
            raise ReleaseValidationError(f"{episode_id} trusted {arm} evaluation has wrong success outcome")
    primitive_gates = evaluator_primitive_evidence(scenario)
    required_primitives = set(WORKFLOW_SCENARIO_REGISTRY[scenario]["required_evaluator_primitives"])
    if required_primitives - set(primitive_gates):
        raise ReleaseValidationError(f"{episode_id} evaluator lacks required primitive evidence bindings")
    reference_gates = results["reference"].get("gates", {})
    for primitive in required_primitives:
        evidence_gates = primitive_gates[primitive]
        if not evidence_gates or any(reference_gates.get(gate) is not True for gate in evidence_gates):
            raise ReleaseValidationError(f"{episode_id} evaluator primitive {primitive} lacks passing runtime evidence")
    if runs["reference"].trace_digest != runs["repeat"].trace_digest:
        raise ReleaseValidationError(f"{episode_id} trusted replay is nondeterministic")
    environment_digests = {arm: _environment_digest(runs[arm]) for arm in TRUST_ARMS}
    if environment_digests["reference"] != environment_digests["repeat"]:
        raise ReleaseValidationError(f"{episode_id} trusted environment replay is nondeterministic")
    if environment_digests["reference"] == environment_digests["noop"]:
        raise ReleaseValidationError(f"{episode_id} trusted environment is not action-sensitive")
    return {
        "trace_digests": {arm: runs[arm].trace_digest for arm in TRUST_ARMS},
        "environment_digests": environment_digests,
        "results": results,
    }


def validate_release(release_dir: Path, minimum_responsibilities: int = 30, minimum_episodes: int = 300) -> dict[str, Any]:
    manifest = json.loads((release_dir / "manifest.json").read_text(encoding="utf-8"))
    expected_environment = manifest.get("runtime_environment")
    current_environment = runtime_environment()
    if expected_environment != current_environment:
        raise ReleaseValidationError(
            f"runtime dependency mismatch: package requires {expected_environment}, current runtime is {current_environment}"
        )
    public_path = release_dir / "episodes_public.jsonl"
    private_path = release_dir / "episodes_private.jsonl"
    if manifest.get("public_sha256") != hashlib.sha256(public_path.read_bytes()).hexdigest():
        raise ReleaseValidationError("public file hash does not match manifest")
    if manifest.get("private_sha256") != hashlib.sha256(private_path.read_bytes()).hexdigest():
        raise ReleaseValidationError("private file hash does not match manifest")
    if manifest.get("query_variant_source_sha256") != hashlib.sha256(QUERY_VARIANTS.read_bytes()).hexdigest():
        raise ReleaseValidationError("Query variant source hash does not match code-side registry")
    public = _read_jsonl(public_path)
    private = _read_jsonl(private_path)
    if len(public) != len(private):
        raise ReleaseValidationError("public/private Episode counts differ")
    public_ids = [row.get("episode_id") for row in public]
    private_ids = [row.get("episode_id") for row in private]
    if public_ids != private_ids or any(not isinstance(value, str) or not value for value in public_ids):
        raise ReleaseValidationError("public/private Episode IDs are invalid or misaligned")
    if len(set(public_ids)) != len(public_ids):
        raise ReleaseValidationError("duplicate episode_id")
    forbidden_public_keys = {"responsibility_id", "scenario_type", "workflow_config", "contract", "evaluator_binding"}
    for public_row in public:
        leaked = forbidden_public_keys & _keys(public_row)
        if leaked:
            raise ReleaseValidationError(f"{public_row['episode_id']} leaks private keys: {sorted(leaked)}")
        if any(candidate in public_row["episode_id"] for candidate in ("rd_", "responsibility")):
            raise ReleaseValidationError(f"{public_row['episode_id']} is not an opaque Episode ID")
    responsibilities = {row.get("responsibility_id") for row in private}
    if None in responsibilities or len(responsibilities) < minimum_responsibilities:
        raise ReleaseValidationError(f"requires at least {minimum_responsibilities} distinct responsibilities")
    if len(private) < minimum_episodes:
        raise ReleaseValidationError(f"requires at least {minimum_episodes} Episodes")
    if any(row.get("support_status") != "FULL" for row in private):
        raise ReleaseValidationError("formal release contains a non-FULL match")
    tracks = Counter(row.get("dataset_track") for row in private)
    if set(tracks) - {"intervention_required", "restraint_required"} or not tracks:
        raise ReleaseValidationError("invalid dataset_track")
    process_digests = [row.get("process_digest") for row in private]
    if any(not isinstance(value, str) or len(value) != 64 for value in process_digests):
        raise ReleaseValidationError("missing or malformed process_digest")
    if len(set(process_digests)) != len(process_digests):
        raise ReleaseValidationError("Query variants or copied physical traces are being counted as distinct Episodes")
    required_receipts = {"reset_receipt", "replay_receipt", "admission_receipt", "evaluator_binding"}
    required_gates = {"reference_success", "noop_fails", "query_deletion_fails", "query_shuffle_fails", "deterministic_replay", "action_sensitive"}
    reference_trace_digests = []
    noop_trace_digests = []
    trusted_reference_trace_digests = []
    trusted_noop_trace_digests = []
    trusted_reference_environment_digests = []
    trusted_noop_environment_digests = []
    for public_row, row in zip(public, private):
        missing = required_receipts - set(row)
        if missing:
            raise ReleaseValidationError(f"{row['episode_id']} lacks receipts: {sorted(missing)}")
        if row.get("contract_digest") != _sha(row.get("contract")):
            raise ReleaseValidationError(f"{row['episode_id']} has a forged Contract digest")
        reset = row["reset_receipt"]
        if reset.get("digest") != _sha(reset.get("payload")):
            raise ReleaseValidationError(f"{row['episode_id']} has a forged reset receipt")
        process = row.get("process_receipt", {})
        if process.get("digest") != _sha(process.get("payload")):
            raise ReleaseValidationError(f"{row['episode_id']} has a forged process receipt")
        if row["responsibility_id"] in json.dumps(process.get("payload"), ensure_ascii=False):
            raise ReleaseValidationError(f"{row['episode_id']} process identity contains its responsibility label")
        replay = row["replay_receipt"]
        reference_digest = replay.get("reference_trace_digest")
        repeat_digest = replay.get("repeat_trace_digest")
        noop_digest = replay.get("noop_trace_digest")
        if any(not isinstance(value, str) or len(value) != 64 for value in (reference_digest, repeat_digest, noop_digest)):
            raise ReleaseValidationError(f"{row['episode_id']} has malformed replay digests")
        if reference_digest != repeat_digest or reference_digest == noop_digest:
            raise ReleaseValidationError(f"{row['episode_id']} fails deterministic or action-sensitive replay evidence")
        reference_trace_digests.append(reference_digest)
        noop_trace_digests.append(noop_digest)
        admission = row["admission_receipt"]
        gates = admission.get("gates", {})
        if set(gates) != required_gates or not all(gates.values()):
            raise ReleaseValidationError(f"{row['episode_id']} has missing or failed admission gates")
        if admission.get("reference_score", {}).get("success") is not True:
            raise ReleaseValidationError(f"{row['episode_id']} lacks a successful feasible reference")
        if admission.get("noop_score", {}).get("success") is not False:
            raise ReleaseValidationError(f"{row['episode_id']} does not require intervention")
        if admission.get("query_deleted_score", {}).get("success") is not False:
            raise ReleaseValidationError(f"{row['episode_id']} passes after Query deletion")
        if admission.get("query_shuffled_score", {}).get("success") is not False:
            raise ReleaseValidationError(f"{row['episode_id']} passes after Query shuffle")
        trusted = _validate_trusted_episode(public_row, row)
        trusted_digests = trusted["trace_digests"]
        trusted_environment_digests = trusted["environment_digests"]
        trusted_results = trusted["results"]
        legacy_to_trusted = {
            "reference_trace_digest": "reference",
            "repeat_trace_digest": "repeat",
            "noop_trace_digest": "noop",
            "query_deleted_trace_digest": "query_deleted",
            "query_shuffled_trace_digest": "query_shuffled",
        }
        for legacy_field, arm in legacy_to_trusted.items():
            if replay.get(legacy_field) != trusted_digests[arm]:
                raise ReleaseValidationError(f"{row['episode_id']} legacy {legacy_field} disagrees with trusted replay")
        environment_to_trusted = {
            "reference_environment_digest": "reference",
            "repeat_environment_digest": "repeat",
            "noop_environment_digest": "noop",
        }
        for legacy_field, arm in environment_to_trusted.items():
            if replay.get(legacy_field) != trusted_environment_digests[arm]:
                raise ReleaseValidationError(f"{row['episode_id']} legacy {legacy_field} disagrees with trusted environment replay")
        score_to_arm = {
            "reference_score": "reference",
            "noop_score": "noop",
            "query_deleted_score": "query_deleted",
            "query_shuffled_score": "query_shuffled",
        }
        for score_field, arm in score_to_arm.items():
            if admission.get(score_field) != trusted_results[arm]:
                raise ReleaseValidationError(f"{row['episode_id']} legacy {score_field} disagrees with trusted evaluator")
        trusted_reference_trace_digests.append(trusted_digests["reference"])
        trusted_noop_trace_digests.append(trusted_digests["noop"])
        trusted_reference_environment_digests.append(trusted_environment_digests["reference"])
        trusted_noop_environment_digests.append(trusted_environment_digests["noop"])
        if row.get("process_digest") != trusted_environment_digests["noop"]:
            raise ReleaseValidationError(f"{row['episode_id']} process identity is not its trusted no-op causal environment")
        if process.get("payload", {}).get("causal_noop_environment_digest") != trusted_environment_digests["noop"]:
            raise ReleaseValidationError(f"{row['episode_id']} process receipt is not bound to its trusted no-op causal environment")
    if len(set(reference_trace_digests)) != len(reference_trace_digests):
        raise ReleaseValidationError("copied reference traces are being counted as distinct Episodes")
    if len(set(noop_trace_digests)) != len(noop_trace_digests):
        raise ReleaseValidationError("copied no-op traces are being counted as distinct Episodes")
    if len(set(trusted_reference_trace_digests)) != len(trusted_reference_trace_digests):
        raise ReleaseValidationError("trusted code replay found copied reference traces")
    if len(set(trusted_noop_trace_digests)) != len(trusted_noop_trace_digests):
        raise ReleaseValidationError("trusted code replay found copied no-op traces")
    if len(set(trusted_reference_environment_digests)) != len(trusted_reference_environment_digests):
        raise ReleaseValidationError("trusted code replay found duplicate realized reference environments")
    if len(set(trusted_noop_environment_digests)) != len(trusted_noop_environment_digests):
        raise ReleaseValidationError("trusted code replay found duplicate realized no-op environments")
    if manifest.get("split_policy") != "none":
        raise ReleaseValidationError("this release must not define train/dev/test splits")
    declared = manifest.get("statistics", {})
    if declared.get("responsibility_count") != len(responsibilities) or declared.get("episode_count") != len(private):
        raise ReleaseValidationError("manifest statistics do not match data")
    return {
        "status": "PASS",
        "responsibility_count": len(responsibilities),
        "episode_count": len(private),
        "track_counts": dict(sorted(tracks.items())),
        "unique_process_count": len(set(process_digests)),
        "unique_reference_trace_count": len(set(reference_trace_digests)),
        "unique_noop_trace_count": len(set(noop_trace_digests)),
        "unique_reference_environment_count": len(set(trusted_reference_environment_digests)),
        "unique_noop_environment_count": len(set(trusted_noop_environment_digests)),
        "trusted_replay_count": len(trusted_reference_trace_digests) * len(TRUST_ARMS),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("release_dir", type=Path)
    parser.add_argument("--minimum-responsibilities", type=int, default=30)
    parser.add_argument("--minimum-episodes", type=int, default=300)
    args = parser.parse_args()
    print(json.dumps(validate_release(args.release_dir, args.minimum_responsibilities, args.minimum_episodes), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
