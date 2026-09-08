from copy import deepcopy

import pytest

from harness_v2.golden import agent_view, digest
from harness_v2.semantic_validator import ConformanceError
from harness_v2.trust_evidence import (
    build_policy_execution_receipt,
    build_reset_receipt,
    public_environment_projection,
    seal_manifest,
    verify_manifest,
    verify_policy_execution_receipt,
    verify_reset_receipt,
)
from harness_v2.trust_registry import TrustedRuntime, sha256_value


def runtime(view, manifest_digest="a" * 64):
    policy = lambda agent_view, callbacks, policy_id, policy_config: [{"kind": "yield_without_mutation"} for _ in callbacks]
    policy_config = {"policy_id": "policy.agent", "fixture_version": "test"}
    return TrustedRuntime(
        runtime_id="runtime.test",
        version="1",
        package_digest=digest("runtime-package"),
        backend_package_digest=digest("backend-package"),
        adapter_package_digest=digest("adapter-package"),
        evaluator_package_digest=digest("evaluator-package"),
        policy_package_digests={"policy.agent": digest("policy-package")},
        policy_configs={"policy.agent": policy_config},
        evaluator_manifest_digests={"manifest.test": manifest_digest},
        reconstruct_reset=lambda seed_digest, exogenous_digest: {"public_projection": public_environment_projection(view), "private_temperature_state": {"kitchen": 18}},
        project_reset=lambda native: native["public_projection"],
        execute_policy=policy,
        replay=lambda native, session, arm_id, query_digest, manifest: {},
    )


def test_trusted_reset_recomputes_native_projection_and_bootstrap_binding():
    view = agent_view("Keep the kitchen comfortable.")
    native = {"public_projection": public_environment_projection(view), "private_temperature_state": {"kitchen": 18}}
    trust = runtime(view)
    receipt = build_reset_receipt(trust, native, seed_digest=digest("seed"), exogenous_realization_digest=digest("exogenous"))
    assert verify_reset_receipt(receipt, view, trust, expected_seed_digest=digest("seed"), expected_exogenous_realization_digest=digest("exogenous")) == native


def test_trusted_reset_rejects_resealed_bootstrap_temperature_drift():
    view = agent_view("Keep the kitchen comfortable.")
    native = {"public_projection": public_environment_projection(view), "private_temperature_state": {"kitchen": 18}}
    trust = runtime(view)
    receipt = build_reset_receipt(trust, native, seed_digest=digest("seed"), exogenous_realization_digest=digest("exogenous"))
    attacked = deepcopy(view)
    attacked["observations"][0]["value"] = 29
    with pytest.raises(ConformanceError):
        verify_reset_receipt(receipt, attacked, trust, expected_seed_digest=digest("seed"), expected_exogenous_realization_digest=digest("exogenous"))


def test_manifest_target_rewrite_is_rejected_by_trusted_catalog():
    body = {"manifest_id": "manifest.test", "implementation_hash": digest("evaluator-package"), "target": 22}
    sealed = seal_manifest(body)
    trust = runtime({}, sealed["manifest_digest"])
    verify_manifest(sealed, trust)
    attacked = seal_manifest({**body, "target": 21})
    with pytest.raises(ConformanceError):
        verify_manifest(attacked, trust)


def test_policy_receipt_rejects_choice_not_produced_by_trusted_policy():
    view = agent_view("Keep the kitchen comfortable.")
    callbacks = [{"callback_id": "callback.reset", "reason": "episode_reset"}]
    choices = [{"kind": "yield_without_mutation"}]
    trust = runtime(view)
    receipt = build_policy_execution_receipt(trust, policy_id="policy.agent", policy_config_digest=sha256_value(trust.policy_configs["policy.agent"]), agent_view=view, callbacks=callbacks, terminal_choices=choices)
    verify_policy_execution_receipt(receipt, trust, agent_view=view, callbacks=callbacks, terminal_choices=choices)
    with pytest.raises(ConformanceError):
        verify_policy_execution_receipt(receipt, trust, agent_view=view, callbacks=callbacks, terminal_choices=[{"kind": "commit_transaction"}])
