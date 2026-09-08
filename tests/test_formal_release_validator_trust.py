from __future__ import annotations

import hashlib
import json
from copy import deepcopy

import pytest

import validate_formal_dataset_release as validator
from harness_v2.core import EpisodeSpec
from harness_v2.trust_evidence import decode_serialization, serialization
from harness_v2.trust_registry import sha256_value
from harness_v2.workflow_scenario_registry import WORKFLOW_SCENARIO_REGISTRY
from harness_v2.workflow_trust import (
    build_trusted_evaluation_receipt,
    build_trusted_replay_receipt,
    build_trusted_reset_receipt,
    verify_trusted_replay_receipt,
)


SCENARIO = "mail_arrival_notification"
RESPONSIBILITY = WORKFLOW_SCENARIO_REGISTRY[SCENARIO]["responsibility_id"]
EPISODE_ID = "wf_0123456789abcdef0123"
QUERY = "Tell me as soon as the mail is delivered."
SHUFFLED_QUERY = "Let me know when the laundry is finished."


def _reseal(receipt):
    body = {key: value for key, value in receipt.items() if key != "receipt_digest"}
    return {**body, "receipt_digest": sha256_value(body)}


def _write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _make_release(tmp_path, monkeypatch):
    query_map = {QUERY: SCENARIO, SHUFFLED_QUERY: "laundry_completion_notification"}
    monkeypatch.setattr(validator, "_trusted_query_to_scenario", lambda: query_map)
    config = {}
    horizon = 3600
    registry = validator._build_code_side_workflow_registry(
        scenario=SCENARIO, config=config, horizon_seconds=horizon
    )
    runtime = registry.require(validator.RUNTIME_ID)
    bootstrap = {
        "query": QUERY,
        "public_profile": {"notification_channel": "app"},
        "horizon_seconds": horizon,
        "allowed_action_kinds": ["act", "wait"],
    }
    deleted_bootstrap = deepcopy(bootstrap)
    deleted_bootstrap.pop("query")
    specs = {
        "reference": EpisodeSpec(EPISODE_ID, deepcopy(bootstrap), 17, 32),
        "repeat": EpisodeSpec(EPISODE_ID, deepcopy(bootstrap), 17, 32),
        "noop": EpisodeSpec(EPISODE_ID, deepcopy(bootstrap), 17, 32),
        "query_deleted": EpisodeSpec(EPISODE_ID, deleted_bootstrap, 17, 32),
        "query_shuffled": EpisodeSpec(EPISODE_ID, {**deepcopy(bootstrap), "query": SHUFFLED_QUERY}, 17, 32),
    }
    reset = build_trusted_reset_receipt(runtime, specs["reference"])
    replays = {}
    evaluations = {}
    policy_ids = {
        arm: validator.NOOP_POLICY_ID if arm == "noop" else validator.REFERENCE_POLICY_ID
        for arm in validator.TRUST_ARMS
    }
    results = {}
    for arm in validator.TRUST_ARMS:
        arm_reset_digest = (
            build_trusted_reset_receipt(runtime, specs[arm])["receipt_digest"]
            if arm in {"query_deleted", "query_shuffled"}
            else reset["receipt_digest"]
        )
        replays[arm] = build_trusted_replay_receipt(
            runtime, specs[arm], policy_ids[arm], reset_receipt_digest=arm_reset_digest
        )
        run = verify_trusted_replay_receipt(
            replays[arm], runtime, specs[arm], expected_policy_id=policy_ids[arm],
            expected_reset_receipt_digest=arm_reset_digest,
        )
        evaluations[arm] = build_trusted_evaluation_receipt(
            runtime, validator.EVALUATOR_ID, SCENARIO, run
        )
        results[arm] = decode_serialization(evaluations[arm]["result"], "evaluation")
    assert results["reference"]["success"] is True
    assert results["noop"]["success"] is False

    initial_public = decode_serialization(reset["initial_public_observation"], "initial public")
    initial_private = decode_serialization(reset["initial_private_state"], "initial private")
    public = {
        "episode_id": EPISODE_ID,
        "query": QUERY,
        "public_profile": bootstrap["public_profile"],
        "initial_observation": initial_public,
        "allowed_action_kinds": bootstrap["allowed_action_kinds"],
        "backend_fidelity_tier": "T2",
    }
    trace_digests = {
        arm: replays[arm]["sealed_run"]["artifact"]["trace_digest"]
        for arm in validator.TRUST_ARMS
    }
    environment_digests = {}
    for arm in validator.TRUST_ARMS:
        artifact = replays[arm]["sealed_run"]["artifact"]
        from harness_v2.core import RunArtifact
        run = RunArtifact(
            artifact["episode_id"], artifact["status"], tuple(artifact["public_trace"]),
            tuple(artifact["private_trace"]), artifact["trace_digest"],
        )
        environment_digests[arm] = validator._environment_digest(run)
    process_payload = {
        "native_initial_devices": initial_private["devices"],
        "native_initial_household": initial_private["household"],
        "native_initial_workflow": initial_private["workflow"],
        "actual_exogenous_realization": initial_private["exogenous_pending"],
        "backend_effective_config": config,
        "horizon_seconds": horizon,
        "causal_noop_environment_digest": environment_digests["noop"],
    }
    process_receipt_digest = validator._sha(process_payload)
    contract = deepcopy(WORKFLOW_SCENARIO_REGISTRY[SCENARIO])
    private = {
        "episode_id": EPISODE_ID,
        "responsibility_id": RESPONSIBILITY,
        "support_status": "FULL",
        "dataset_track": "intervention_required",
        "backend_id": "household_workflow_harness_v2",
        "backend_fidelity_tier": "T2",
        "contract": contract,
        "contract_digest": validator._sha(contract),
        "process_digest": environment_digests["noop"],
        "process_receipt": {"digest": process_receipt_digest, "payload": process_payload},
        "reset_receipt": {"digest": validator._sha({"episode_id": EPISODE_ID}), "payload": {"episode_id": EPISODE_ID}},
        "replay_receipt": {
            "reference_trace_digest": trace_digests["reference"],
            "repeat_trace_digest": trace_digests["repeat"],
            "noop_trace_digest": trace_digests["noop"],
            "query_deleted_trace_digest": trace_digests["query_deleted"],
            "query_shuffled_trace_digest": trace_digests["query_shuffled"],
            "reference_environment_digest": environment_digests["reference"],
            "repeat_environment_digest": environment_digests["repeat"],
            "noop_environment_digest": environment_digests["noop"],
        },
        "admission_receipt": {
            "gates": {
                "reference_success": True,
                "noop_fails": True,
                "query_deletion_fails": True,
                "query_shuffle_fails": True,
                "deterministic_replay": True,
                "action_sensitive": True,
            },
            "reference_score": results["reference"],
            "noop_score": results["noop"],
            "query_deleted_score": results["query_deleted"],
            "query_shuffled_score": results["query_shuffled"],
        },
        "evaluator_binding": {"id": validator.EVALUATOR_ID},
        "query_interventions": {"shuffled_query": SHUFFLED_QUERY},
        "trusted_runtime": {
            "runtime_id": runtime.runtime_id,
            "version": runtime.version,
            "registry_entry_digest": runtime.registry_entry_digest,
        },
        "trusted_reset_receipt": reset,
        "trusted_replays": replays,
        "trusted_evaluations": evaluations,
    }
    public_path = tmp_path / "episodes_public.jsonl"
    private_path = tmp_path / "episodes_private.jsonl"
    _write_jsonl(public_path, [public])
    _write_jsonl(private_path, [private])
    manifest = {
        "schema_version": "formal-workflow-release-v2",
        "split_policy": "none",
        "statistics": {"responsibility_count": 1, "episode_count": 1},
        "runtime_environment": validator.runtime_environment(),
        "query_variant_source_sha256": hashlib.sha256(validator.QUERY_VARIANTS.read_bytes()).hexdigest(),
        "public_sha256": hashlib.sha256(public_path.read_bytes()).hexdigest(),
        "private_sha256": hashlib.sha256(private_path.read_bytes()).hexdigest(),
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return public, private


def _rewrite_private(tmp_path, private):
    path = tmp_path / "episodes_private.jsonl"
    _write_jsonl(path, [private])
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["private_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")


def test_workflow_package_digests_bind_transitive_trust_helpers(monkeypatch):
    captured = {}

    def capture(paths, *, package_id, version):
        captured[package_id] = {path.name for path in paths}
        return f"digest:{package_id}:{version}"

    monkeypatch.setattr(validator, "package_digest", capture)
    validator._workflow_package_digests()
    helpers = {"trust_registry.py", "trust_evidence.py", "semantic_validator.py"}
    assert helpers <= captured["harness-v2"]
    assert helpers <= captured["workflow-runtime"]


def test_validator_performs_full_code_side_replay(tmp_path, monkeypatch):
    _make_release(tmp_path, monkeypatch)
    result = validator.validate_release(tmp_path, minimum_responsibilities=1, minimum_episodes=1)
    assert result["status"] == "PASS"
    assert result["trusted_replay_count"] == 5


def test_validator_rejects_forged_trace_even_if_bundle_is_resealed(tmp_path, monkeypatch):
    _, private = _make_release(tmp_path, monkeypatch)
    attacked = deepcopy(private)
    receipt = attacked["trusted_replays"]["reference"]
    artifact = receipt["sealed_run"]["artifact"]
    artifact["private_trace"][-1]["value"]["action_cost"] = 999
    artifact["trace_digest"] = sha256_value({
        "episode_id": artifact["episode_id"], "status": artifact["status"],
        "public_trace": artifact["public_trace"], "private_trace": artifact["private_trace"],
    })
    sealed = serialization(artifact)
    receipt["sealed_run"] = {"artifact": artifact, "serialization": sealed, "artifact_digest": sealed["digest"]}
    attacked["trusted_replays"]["reference"] = _reseal(receipt)
    _rewrite_private(tmp_path, attacked)
    with pytest.raises(validator.ReleaseValidationError, match="deterministic workflow replay mismatch"):
        validator.validate_release(tmp_path, 1, 1)


def test_validator_rejects_bundle_supplied_runtime_and_evaluator_result(tmp_path, monkeypatch):
    _, private = _make_release(tmp_path, monkeypatch)
    unknown = deepcopy(private)
    unknown["trusted_runtime"]["runtime_id"] = "runtime.from.bundle"
    _rewrite_private(tmp_path, unknown)
    with pytest.raises(validator.ReleaseValidationError, match="untrusted workflow runtime"):
        validator.validate_release(tmp_path, 1, 1)

    _, private = _make_release(tmp_path, monkeypatch)
    forged = deepcopy(private)
    evaluation = forged["trusted_evaluations"]["noop"]
    evaluation["result"] = serialization({**forged["admission_receipt"]["noop_score"], "success": True})
    forged["trusted_evaluations"]["noop"] = _reseal(evaluation)
    _rewrite_private(tmp_path, forged)
    with pytest.raises(validator.ReleaseValidationError, match="evaluation recomputation mismatch"):
        validator.validate_release(tmp_path, 1, 1)


def test_validator_rejects_query_and_process_identity_attacks(tmp_path, monkeypatch):
    _, private = _make_release(tmp_path, monkeypatch)
    same_scenario_shuffle = deepcopy(private)
    same_scenario_shuffle["query_interventions"]["shuffled_query"] = QUERY
    _rewrite_private(tmp_path, same_scenario_shuffle)
    with pytest.raises(validator.ReleaseValidationError, match="cross-responsibility"):
        validator.validate_release(tmp_path, 1, 1)

    _, private = _make_release(tmp_path, monkeypatch)
    labeled = deepcopy(private)
    labeled["process_receipt"]["payload"]["hidden_label"] = RESPONSIBILITY
    labeled["process_digest"] = labeled["process_receipt"]["digest"] = validator._sha(labeled["process_receipt"]["payload"])
    _rewrite_private(tmp_path, labeled)
    with pytest.raises(validator.ReleaseValidationError, match="responsibility label"):
        validator.validate_release(tmp_path, 1, 1)


def test_validator_rejects_public_leak_and_manifest_hash_attack(tmp_path, monkeypatch):
    public, _ = _make_release(tmp_path, monkeypatch)
    leaked = deepcopy(public)
    leaked["scenario_type"] = SCENARIO
    _write_jsonl(tmp_path / "episodes_public.jsonl", [leaked])
    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["public_sha256"] = hashlib.sha256((tmp_path / "episodes_public.jsonl").read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(validator.ReleaseValidationError, match="leaks private keys"):
        validator.validate_release(tmp_path, 1, 1)

    _make_release(tmp_path, monkeypatch)
    (tmp_path / "episodes_private.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(validator.ReleaseValidationError, match="private file hash"):
        validator.validate_release(tmp_path, 1, 1)
