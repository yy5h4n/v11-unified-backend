from evaluate_workflow_formal_release_v1 import evaluate_policy
from harness_v2.workflow_policy import NoOpPolicy, WorkflowReferencePolicy


def test_reference_policy_replays_to_full_success_with_cost():
    result = evaluate_policy(lambda scenario: WorkflowReferencePolicy(scenario), policy_name="reference")
    assert result["main_metrics"]["eligible_episode_count"] == 300
    assert result["main_metrics"]["responsibility_success_rate"] == 1.0
    assert result["main_metrics"]["success_conditioned_resource_cost"] is not None
    assert len(result["by_responsibility"]) == 30


def test_noop_policy_replays_to_zero_success_and_na_cost():
    result = evaluate_policy(lambda scenario: NoOpPolicy(), policy_name="noop")
    assert result["main_metrics"]["eligible_episode_count"] == 300
    assert result["main_metrics"]["responsibility_success_rate"] == 0.0
    assert result["main_metrics"]["success_conditioned_resource_cost"] is None
