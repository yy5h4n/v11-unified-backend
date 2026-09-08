from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

import evaluate_workflow_v4_flash_v8 as v8


ACTION1 = {"kind": "wait", "mode": "for", "duration_seconds": 60}
ACTION2 = {"kind": "act", "commands": []}


class FakeClient:
    model = v8.DEFAULT_MODEL

    def __init__(self, contents):
        self.contents = list(contents)
        self.requests = []

    def complete(self, messages):
        self.requests.append(deepcopy(messages))
        return {
            "content": self.contents.pop(0),
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            "latency_ms": 3,
        }


def view(*, feedback=None, step=0):
    return {
        "query": "Keep watching the home.",
        "public_profile": {"name": "resident"},
        "observation": {
            "episode_id": "ep-test",
            "step": step,
            "time": f"2026-01-01T00:{step:02d}:00Z",
            "tick_seconds": 60,
            "inventory": {"devices": []},
            "device_states": {},
            "events": [],
        },
        "last_feedback": feedback,
    }


def envelope(action):
    return "analysis that must disappear\n<answer>" + v8.canonical_json(action) + "</answer>"


def test_initial_request_is_system_then_single_user_bootstrap():
    client = FakeClient([envelope(ACTION1)])
    policy = v8.JsonEnvelopeHistoryPolicy(client)
    assert policy.decide(view()) == ACTION1
    assert [message["role"] for message in client.requests[0]] == ["system", "user"]
    payload = json.loads(client.requests[0][1]["content"])
    assert payload["message_type"] == "initial_request"
    assert set(payload) == {"message_type", "original_query", "public_preferences", "initial_observation"}


def test_second_request_contains_full_canonical_history_without_duplicate_previous_fields():
    client = FakeClient([envelope(ACTION1), envelope(ACTION2)])
    policy = v8.JsonEnvelopeHistoryPolicy(client)
    policy.decide(view())
    policy.decide(view(feedback={"accepted": True}, step=1))
    request = client.requests[1]
    assert [message["role"] for message in request] == ["system", "user", "assistant", "user"]
    assert request[2] == v8.canonical_assistant_action(ACTION1)
    observation = json.loads(request[3]["content"])
    assert observation["message_type"] == "environment_observation"
    serialized = json.dumps(request)
    assert "previous_action" not in serialized
    assert "previous_action_result" not in serialized


def test_raw_reasoning_is_not_replayed_or_persisted():
    secret = "PRIVATE_REASONING_SENTINEL"
    client = FakeClient([secret + "<answer>" + v8.canonical_json(ACTION1) + "</answer>", envelope(ACTION2)])
    policy = v8.JsonEnvelopeHistoryPolicy(client)
    policy.decide(view())
    assert secret not in json.dumps(policy.history)
    assert secret not in json.dumps(policy.output_records)
    assert set(policy.output_records[0]) == {
        "call_index", "canonical_action", "raw_response_sha256", "raw_response_byte_length", "usage", "protocol_error"
    }


def test_client_is_called_without_native_tools_arguments():
    client = FakeClient([envelope(ACTION1)])
    v8.JsonEnvelopeHistoryPolicy(client).decide(view())
    assert len(client.requests) == 1


def test_completion_tokens_are_output_tokens_and_provider_total_is_separate():
    client = FakeClient([envelope(ACTION1), envelope(ACTION2)])
    policy = v8.JsonEnvelopeHistoryPolicy(client)
    policy.decide(view())
    policy.decide(view(feedback={}, step=1))
    assert policy.prompt_tokens == 20
    assert policy.completion_tokens == policy.output_tokens == 4
    assert policy.provider_total_tokens == 24
    assert policy.provider_total_tokens_present_calls == 2
    assert policy.provider_total_tokens_mismatch_calls == 0
    usage = policy.output_records[0]["usage"]
    assert usage["api_output_tokens"] == usage["completion_tokens"] == usage["output_tokens"] == 2
    assert usage["diagnostic_prompt_tokens"] == 10
    assert usage["diagnostic_provider_total_tokens"] == 12
    assert "total_tokens" not in usage


def test_metrics_primary_token_metric_is_api_output_only_and_has_no_generic_total():
    row = {
        "responsibility_success": True,
        "device_command_count": 1,
        "run_status": "completed",
        "api_successes": 2,
        "parsed_json_actions": 2,
        "rejected_action_count": 0,
        "calls": 2,
        "latency_ms": 6.0,
        "harness_action_records": 2,
        "accepted_harness_actions": 2,
        "protocol_error_count": 0,
        "backend_error_count": 0,
        "prompt_tokens": 100,
        "completion_tokens": 7,
        "output_tokens": 7,
        "provider_total_tokens": 107,
        "provider_total_tokens_present_calls": 2,
        "provider_total_tokens_mismatch_calls": 0,
    }
    metrics = v8.metrics_with_device_total([row])
    assert metrics["primary_token_metric"] == "api_output_tokens"
    assert metrics["api_output_tokens"] == metrics["completion_tokens"] == metrics["output_tokens"] == 7
    assert "total_tokens" not in metrics
    assert "prompt_tokens" not in metrics
    assert "provider_total_tokens" not in metrics
    assert metrics["token_diagnostics"] == {
        "prompt_tokens": 100,
        "provider_total_tokens": 107,
        "provider_total_tokens_present_calls": 2,
        "provider_total_tokens_mismatch_calls": 0,
        "provider_total_synthesized": False,
    }


def test_missing_provider_total_is_not_synthesized():
    class MissingTotalClient(FakeClient):
        def complete(self, messages):
            self.requests.append(deepcopy(messages))
            return {"content": self.contents.pop(0), "usage": {"prompt_tokens": 10, "completion_tokens": 2}}

    policy = v8.JsonEnvelopeHistoryPolicy(MissingTotalClient([envelope(ACTION1)]))
    policy.decide(view())
    assert policy.provider_total_tokens == 0
    assert policy.provider_total_tokens_present_calls == 0


def test_finalize_pairs_last_action_with_latest_environment_state():
    client = FakeClient([envelope(ACTION1)])
    policy = v8.JsonEnvelopeHistoryPolicy(client)
    policy.decide(view())
    trace = (
        {"type": "observation", "index": 0, "value": view(step=0)["observation"]},
        {"type": "action", "index": 0, "action": ACTION1, "accepted": True, "feedback": {"ok": True}},
        {"type": "observation", "index": 1, "value": view(step=1)["observation"]},
    )
    policy.finalize(trace)
    assert [message["role"] for message in policy.history] == ["system", "user", "assistant", "user"]
    final = json.loads(policy.history[-1]["content"])
    assert final["action_result"] == {"ok": True}
    assert final["current_observation"]["step"] == 1


def test_conversation_validator_round_trip():
    initial = view()
    conversation = [
        {"role": "system", "content": "rules"},
        v8.build_initial_user_message(initial),
        v8.canonical_assistant_action(ACTION1),
        v8.build_environment_message({"ok": True}, view(step=1)["observation"]),
    ]
    assert v8.validate_canonical_conversation(
        conversation, expected_query=initial["query"], expected_episode_id="ep-test"
    ) == [ACTION1]


@pytest.mark.parametrize("mutation", [
    lambda c: c.append({"role": "assistant", "content": "<answer>{}</answer>"}),
    lambda c: c[2].update({"content": "thinking <answer>{\"kind\":\"wait\"}</answer>"}),
    lambda c: c[3].update({"role": "tool"}),
])
def test_conversation_validator_rejects_noncanonical_history(mutation):
    initial = view()
    conversation = [
        {"role": "system", "content": "rules"},
        v8.build_initial_user_message(initial),
        v8.canonical_assistant_action(ACTION1),
        v8.build_environment_message({}, view(step=1)["observation"]),
    ]
    mutation(conversation)
    with pytest.raises(RuntimeError):
        v8.validate_canonical_conversation(conversation)


def test_invalid_envelope_returns_protocol_invalid_action_without_leaking_text():
    policy = v8.JsonEnvelopeHistoryPolicy(FakeClient(["bad output SECRET"]))
    assert policy.decide(view()) == {"kind": "invalid_model_output"}
    assert policy.parsed_json_actions == 0
    assert policy.output_records[0]["canonical_action"] is None
    assert "SECRET" not in json.dumps(policy.output_records)


def test_api_error_aborts_without_canonical_action_or_protocol_error():
    class FailingClient:
        model = v8.DEFAULT_MODEL

        def complete(self, messages):
            raise v8.APIError("urlerror")

    policy = v8.JsonEnvelopeHistoryPolicy(FailingClient())
    with pytest.raises(v8.APIError, match="urlerror"):
        policy.decide(view())
    assert policy.calls == 1
    assert policy.api_successes == 0
    assert policy.parsed_json_actions == 0
    assert policy.errors == {}
    assert policy.pending_action is False
    assert [message["role"] for message in policy.history] == ["system", "user"]
    assert policy.output_records == [{
        "call_index": 0,
        "canonical_action": None,
        "raw_response_sha256": None,
        "raw_response_byte_length": 0,
        "usage": None,
        "protocol_error": None,
    }]


def test_main_transport_failure_preserves_exact_prefix_as_unfinished(monkeypatch, tmp_path: Path):
    prefix = [{"episode_id": "already-complete"}]
    original_prefix = deepcopy(prefix)
    writes = []

    monkeypatch.setattr(v8.v5, "assert_release_and_sample_frozen", lambda: None)
    monkeypatch.setattr(v8, "load_checkpoint", lambda path: prefix)
    monkeypatch.setattr(v8, "ChatClient", lambda base_url, model: object())
    monkeypatch.setattr(v8, "evaluate_one", lambda client, index, release_dir: (_ for _ in ()).throw(v8.APIError("reset")))

    def fake_build_report(results, *, execution_finished):
        return {"execution_finished": execution_finished, "episodes": deepcopy(results)}

    monkeypatch.setattr(v8, "build_report", fake_build_report)
    monkeypatch.setattr(v8, "atomic_write_report", lambda path, report: writes.append(deepcopy(report)))

    with pytest.raises(v8.APIError, match="reset"):
        v8.main(["--output", str(tmp_path / "report.json")])
    assert prefix == original_prefix
    assert writes == [{"execution_finished": False, "episodes": original_prefix}]


def test_main_normal_completion_still_writes_all_fifteen(monkeypatch, tmp_path: Path):
    writes = []
    monkeypatch.setattr(v8.v5, "assert_release_and_sample_frozen", lambda: None)
    monkeypatch.setattr(v8, "load_checkpoint", lambda path: [])
    monkeypatch.setattr(v8, "ChatClient", lambda base_url, model: object())
    monkeypatch.setattr(
        v8, "evaluate_one",
        lambda client, index, release_dir: {"episode_id": v8.SAMPLE_EPISODE_IDS[v8.SAMPLE_INDICES.index(index)]},
    )

    def fake_build_report(results, *, execution_finished):
        return {"execution_finished": execution_finished, "episodes": deepcopy(results)}

    monkeypatch.setattr(v8, "build_report", fake_build_report)
    monkeypatch.setattr(v8, "atomic_write_report", lambda path, report: writes.append(deepcopy(report)))
    report = v8.main(["--output", str(tmp_path / "report.json")])
    assert len(report["episodes"]) == 15
    assert report["execution_finished"] is True
    assert len(writes) == 16
    assert all(write["execution_finished"] is False for write in writes[:-1])
    assert writes[-1] == report


def test_system_contains_envelope_device_and_event_interfaces():
    client = FakeClient([envelope(ACTION1)])
    policy = v8.JsonEnvelopeHistoryPolicy(client)
    policy.decide(view())
    document = json.loads(client.requests[0][0]["content"])
    assert document["response_envelope"]["format"].startswith("<answer>")
    assert "device_interfaces" in document
    assert "observable_event_interface" in document


def test_prompt_template_hash_is_deterministic_and_v8_specific():
    assert v8.prompt_template_sha256() == v8.prompt_template_sha256()
    assert v8.prompt_template_sha256() != v8.v6.prompt_template_sha256()


def test_fixed_sample_is_exactly_inherited_from_v5():
    assert v8.SAMPLE_INDICES is v8.v5.SAMPLE_INDICES
    assert v8.SAMPLE_EPISODE_IDS is v8.v5.SAMPLE_EPISODE_IDS
    assert len(v8.SAMPLE_INDICES) == 15


def test_default_output_is_new_v8_path():
    assert "v8" in str(v8.DEFAULT_OUTPUT)
    assert "v7" not in str(v8.DEFAULT_OUTPUT)


def test_migration_manifest_explicitly_disables_native_tools():
    assert v8.MIGRATION_MANIFEST["native_tools"] == "not sent and not required"
    assert "api_output_tokens" in v8.MIGRATION_MANIFEST["token_metric"]
