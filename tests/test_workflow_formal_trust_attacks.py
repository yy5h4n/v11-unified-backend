from copy import deepcopy

import pytest

from harness_v2.core import ActionOutcome, BackendStep, EpisodeSpec
from harness_v2.semantic_validator import ConformanceError
from harness_v2.trust_evidence import serialization
from harness_v2.trust_registry import sha256_value
from harness_v2.workflow_trust import (
    TrustedWorkflowEvaluator,
    TrustedWorkflowPolicy,
    TrustedWorkflowRuntime,
    WorkflowTrustRegistry,
    build_trusted_evaluation_receipt,
    build_trusted_replay_receipt,
    build_trusted_reset_receipt,
    run_trusted_policy,
    verify_trusted_evaluation_receipt,
    verify_trusted_replay_receipt,
    verify_trusted_reset_receipt,
)


class TinyBackend:
    def reset(self, episode):
        self.episode_id = episode.episode_id
        self.seed = episode.seed
        self.value = episode.seed % 7
        self.applied = []
        return self._step(False)

    def state_digest(self):
        return sha256_value({"episode_id": self.episode_id, "seed": self.seed, "value": self.value})

    def execute_atomic(self, action):
        if action["kind"] == "act":
            command = deepcopy(action["commands"][0])
            self.value += command["parameters"]["amount"]
            self.applied = [{"command": command, "source": "agent", "status": "committed"}]
            return ActionOutcome(True, {"status": "accepted"}, {"applied_commands": deepcopy(self.applied)})
        return ActionOutcome(True, {"status": "accepted"}, {"applied_commands": []})

    def advance(self, triggering_action):
        return self._step(True)

    def _step(self, terminal):
        return BackendStep(
            {"episode_id": self.episode_id, "value": self.value, "events": []},
            {"seed": self.seed, "value": self.value, "applied_commands": deepcopy(self.applied)},
            terminal,
        )


class AddPolicy:
    def __init__(self, amount):
        self.amount = amount

    def decide(self, _view):
        return {
            "kind": "act",
            "commands": [{
                "device_id": "counter.main",
                "capability": "counter.control",
                "operation": "add",
                "parameters": {"amount": self.amount},
            }],
        }


def _runtime():
    policy = TrustedWorkflowPolicy(
        policy_id="policy.add.v1",
        package_digest=sha256_value("policy-package"),
        config={"amount": 2},
        factory=lambda config: AddPolicy(config["amount"]),
    )
    evaluator = TrustedWorkflowEvaluator(
        evaluator_id="evaluator.counter.v1",
        package_digest=sha256_value("evaluator-package"),
        manifest={"metric": "final_value", "version": "1"},
        evaluate=lambda scenario, run: {
            "scenario": scenario,
            "success": run.status == "completed",
            "final_value": run.private_trace[-1]["value"]["value"],
        },
    )
    return TrustedWorkflowRuntime(
        runtime_id="runtime.workflow.test.v1",
        version="1",
        package_digest=sha256_value("runtime-package"),
        backend_package_digest=sha256_value("backend-package"),
        harness_package_digest=sha256_value("harness-package"),
        backend_factory=TinyBackend,
        policies={policy.policy_id: policy},
        evaluators={evaluator.evaluator_id: evaluator},
    )


def _episode(suffix="one"):
    return EpisodeSpec(
        episode_id=f"episode.{suffix}",
        public_bootstrap={"query": "Add two.", "allowed_action_kinds": ["act", "wait"]},
        seed=17,
        max_decisions=2,
    )


def _reseal_receipt(receipt):
    body = {key: value for key, value in receipt.items() if key != "receipt_digest"}
    return {**body, "receipt_digest": sha256_value(body)}


def test_trusted_workflow_reset_replay_and_evaluation_recompute():
    runtime = _runtime()
    episode = _episode()
    reset = build_trusted_reset_receipt(runtime, episode)
    verify_trusted_reset_receipt(reset, runtime, episode)
    replay = build_trusted_replay_receipt(
        runtime, episode, "policy.add.v1", reset_receipt_digest=reset["receipt_digest"]
    )
    run = verify_trusted_replay_receipt(
        replay,
        runtime,
        episode,
        expected_policy_id="policy.add.v1",
        expected_reset_receipt_digest=reset["receipt_digest"],
    )
    evaluation = build_trusted_evaluation_receipt(runtime, "evaluator.counter.v1", "counter", run)
    result = verify_trusted_evaluation_receipt(
        evaluation, runtime, "evaluator.counter.v1", "counter", run
    )
    assert result == {"scenario": "counter", "success": True, "final_value": 5}


def test_reset_rejects_forged_initial_state_even_after_attacker_reseals():
    runtime = _runtime()
    episode = _episode()
    attacked = deepcopy(build_trusted_reset_receipt(runtime, episode))
    forged = {"episode_id": episode.episode_id, "value": 999, "events": []}
    attacked["initial_public_observation"] = serialization(forged)
    attacked = _reseal_receipt(attacked)
    with pytest.raises(ConformanceError, match="initial public observation mismatch"):
        verify_trusted_reset_receipt(attacked, runtime, episode)


def test_replay_rejects_forged_applied_commands_after_full_reseal():
    runtime = _runtime()
    episode = _episode()
    reset = build_trusted_reset_receipt(runtime, episode)
    attacked = deepcopy(build_trusted_replay_receipt(
        runtime, episode, "policy.add.v1", reset_receipt_digest=reset["receipt_digest"]
    ))
    artifact = attacked["sealed_run"]["artifact"]
    action_result = next(row for row in artifact["private_trace"] if row["type"] == "action_result")
    action_result["feedback"]["applied_commands"][0]["command"]["parameters"]["amount"] = 999
    artifact["trace_digest"] = sha256_value({
        "episode_id": artifact["episode_id"],
        "status": artifact["status"],
        "public_trace": artifact["public_trace"],
        "private_trace": artifact["private_trace"],
    })
    sealed = serialization(artifact)
    attacked["sealed_run"] = {
        "artifact": artifact,
        "serialization": sealed,
        "artifact_digest": sealed["digest"],
    }
    attacked = _reseal_receipt(attacked)
    with pytest.raises(ConformanceError, match="deterministic workflow replay mismatch"):
        verify_trusted_replay_receipt(
            attacked,
            runtime,
            episode,
            expected_policy_id="policy.add.v1",
            expected_reset_receipt_digest=reset["receipt_digest"],
        )


def test_replay_is_bound_to_episode_and_code_side_policy_registry():
    runtime = _runtime()
    episode = _episode()
    reset = build_trusted_reset_receipt(runtime, episode)
    replay = build_trusted_replay_receipt(
        runtime, episode, "policy.add.v1", reset_receipt_digest=reset["receipt_digest"]
    )
    with pytest.raises(ConformanceError, match="episode_spec_digest"):
        verify_trusted_replay_receipt(
            replay,
            runtime,
            _episode("copied"),
            expected_policy_id="policy.add.v1",
            expected_reset_receipt_digest=reset["receipt_digest"],
        )
    with pytest.raises(ConformanceError, match="untrusted workflow policy"):
        build_trusted_replay_receipt(
            runtime, episode, "policy.from.bundle", reset_receipt_digest=reset["receipt_digest"]
        )
    with pytest.raises(ConformanceError, match="untrusted workflow runtime"):
        WorkflowTrustRegistry([runtime]).require("runtime.from.bundle")


def test_evaluation_rejects_forged_result_after_attacker_reseals():
    runtime = _runtime()
    episode = _episode()
    run = run_trusted_policy(runtime, episode, "policy.add.v1")
    attacked = deepcopy(build_trusted_evaluation_receipt(
        runtime, "evaluator.counter.v1", "counter", run
    ))
    attacked["result"] = serialization({"scenario": "counter", "success": True, "final_value": 999})
    attacked = _reseal_receipt(attacked)
    with pytest.raises(ConformanceError, match="evaluation recomputation mismatch"):
        verify_trusted_evaluation_receipt(
            attacked, runtime, "evaluator.counter.v1", "counter", run
        )
