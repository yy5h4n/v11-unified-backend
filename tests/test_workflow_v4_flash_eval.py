import json

from evaluate_workflow_v4_flash import REQUEST_BODY_FIELDS, SYSTEM_PROMPT, action_protocol, aggregate_results, build_messages, evaluate_one, load_episode, prompt_template_sha256, smoke_accepted


class FakeClient:
    model = "fake-v4"

    def __init__(self, content):
        self.content = content

    def complete(self, messages):
        return {"content": self.content, "usage": {"total_tokens": 5}, "latency_ms": 1.0}


class SequenceClient(FakeClient):
    def __init__(self, contents):
        self.contents = iter(contents)

    def complete(self, messages):
        return {"content": next(self.contents), "usage": {"total_tokens": 5}, "latency_ms": 1.0}


def test_prompt_contains_only_public_episode_material():
    public, private, spec, backend = load_episode(0)
    initial = backend.reset(spec).public_observation
    view = {**spec.public_bootstrap, "observation": initial, "last_feedback": None, "allowed_actions": spec.public_bootstrap["allowed_action_kinds"]}
    wire = json.dumps(build_messages(view))
    assert public["query"] in wire
    assert private["responsibility_id"] not in wire
    assert private["contract_digest"] not in wire
    assert "oracle" not in wire
    assert SYSTEM_PROMPT.endswith("single JSON object.")
    assert "Think silently" in SYSTEM_PROMPT
    assert "If uncertain" not in SYSTEM_PROMPT
    assert len(prompt_template_sha256()) == 64
    assert "response_format" not in REQUEST_BODY_FIELDS
    assert "tools" not in REQUEST_BODY_FIELDS


def test_protocol_is_inventory_driven_and_wait_is_explicit():
    _, _, spec, backend = load_episode(0)
    initial = backend.reset(spec).public_observation
    view = {**spec.public_bootstrap, "observation": initial, "last_feedback": None, "allowed_actions": spec.public_bootstrap["allowed_action_kinds"]}
    protocol = action_protocol(view)
    assert protocol["forms"]["wait_for"] == {"kind": "wait", "mode": "for", "duration_seconds": 60}
    assert {row["device_id"] for row in protocol["device_interfaces"]} == set(initial["devices"])


def test_invalid_model_output_fails_closed():
    result = evaluate_one(FakeClient("not json"), 0)
    assert result["run_status"] == "protocol_invalid"
    assert result["api_successes"] == 1
    assert result["parsed_json_actions"] == 0
    assert result["responsibility_success"] is False
    assert smoke_accepted(result) is False


def test_valid_long_wait_completes_without_fabricated_success():
    result = evaluate_one(FakeClient('{"kind":"wait","mode":"for","duration_seconds":3600}'), 0)
    assert result["run_status"] == "completed"
    assert result["responsibility_success"] is False
    assert result["action_cost"] == 0
    assert smoke_accepted(result) is True


def test_backend_rejection_cannot_be_smoke_accepted():
    rejected = json.dumps({
        "kind": "act",
        "commands": [{"device_id": "missing", "capability": "fake", "operation": "fake", "parameters": {}}],
    })
    wait = '{"kind":"wait","mode":"for","duration_seconds":3600}'
    result = evaluate_one(SequenceClient([rejected, wait]), 0)
    assert result["run_status"] == "completed"
    assert result["rejected_action_count"] == 1
    assert result["all_actions_accepted"] is False
    assert smoke_accepted(result) is False


def test_report_surface_omits_private_evaluator_details():
    result = evaluate_one(FakeClient('{"kind":"wait","mode":"for","duration_seconds":3600}'), 0)
    wire = json.dumps(result)
    assert "score" not in result
    assert "scenario" not in wire
    assert "failure_reasons" not in wire
    assert "gates" not in wire


def test_metrics_keep_zero_success_cost_null_and_support_heldout_slice():
    failed = evaluate_one(FakeClient('{"kind":"wait","mode":"for","duration_seconds":3600}'), 0)
    metrics = aggregate_results([failed])
    assert metrics["responsibility_success_rate"] == 0.0
    assert metrics["success_conditioned_action_cost"] is None
    assert "decision_call_api_success_rate_after_transport_retries" in metrics
    assert aggregate_results([])["episode_count"] == 0
