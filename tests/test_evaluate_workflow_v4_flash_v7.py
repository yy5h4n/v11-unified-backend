from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from urllib.error import HTTPError, URLError

import pytest

import evaluate_workflow_v4_flash_v4 as v4
import evaluate_workflow_v4_flash_v5 as v5
import evaluate_workflow_v4_flash_v6 as v6
import evaluate_workflow_v4_flash_v7 as v7
import probe_v4_flash_native_tools as probe
from evaluate_harness_v2_v4_flash import DEFAULT_BASE, DEFAULT_MODEL
from harness_v2.core import ActionOutcome, BackendStep, Harness


def fake_view(*, step: int = 0, feedback=None) -> dict:
    return {
        "query": "Keep the fridge from being left open.",
        "public_profile": {"resident": {"id": "primary"}, "limits": {"alarm_seconds": 180}},
        "observation": {
            "episode_id": "ep", "step": step, "time": f"2026-01-01T18:{step:02d}:00Z",
            "tick_seconds": 60,
            "inventory": {"complete": True, "devices": [{
                "device_id": "fridge_door.main", "device_type": "fridge_door",
                "capabilities": ["fridge.door"], "interfaces": [{
                    "capability": "fridge.door", "operation": "close",
                    "parameters_schema": {"type": "object", "maxProperties": 0},
                }], "availability": "available",
            }]},
            "devices": {"fridge_door.main": {"state": "open" if step else "closed"}},
            "public_context": {}, "household": {}, "semantic_observations": [], "workflow": {},
            "events": [] if step == 0 else [{"type": "fridge_door_opened", "step": 1, "device_id": "fridge_door.main"}],
            "active_rule_ids": [], "metrics": {"device_command_count": 0}, "terminal_reason": None,
        },
        "last_feedback": deepcopy(feedback),
    }


def native_response(call_id: str, action: dict, *, content=None, usage=None) -> dict:
    return {
        "message": {"role": "assistant", "content": content, "tool_calls": [{
            "id": call_id, "type": "function",
            "function": {"name": v7.tool_name_for_action(action), "arguments": json.dumps(action, separators=(",", ":"))},
        }]},
        "usage": usage or {"prompt_tokens": 10, "completion_tokens": 3, "total_tokens": 13},
        "latency_ms": 1,
    }


class FakeClient:
    model = DEFAULT_MODEL

    def __init__(self, responses: list[dict]):
        self.responses = list(responses)
        self.requests: list[tuple[list[dict], list[dict], dict]] = []

    def complete(self, messages, *, tools, tool_choice):
        self.requests.append((deepcopy(messages), deepcopy(tools), deepcopy(tool_choice)))
        return self.responses.pop(0)


def test_frozen_v5_sample_is_exactly_migrated() -> None:
    public, private = v5.assert_release_and_sample_frozen()
    assert v7.SAMPLE_INDICES == v5.SAMPLE_INDICES
    assert v7.SAMPLE_EPISODE_IDS == v5.SAMPLE_EPISODE_IDS
    assert [x["episode_id"] for x in public] == v7.SAMPLE_EPISODE_IDS
    assert len(public) == len(private) == 15


def test_exact_initial_and_full_native_tool_history_sequence() -> None:
    action1 = {"kind": "wait", "mode": "for", "duration_seconds": 60}
    action2 = {"kind": "act", "commands": []}
    client = FakeClient([native_response("call_1", action1), native_response("call_2", action2)])
    policy = v7.NativeToolHistoryPolicy(client, native_tools_verified=True)

    assert policy.decide(fake_view()) == action1
    assert policy.decide(fake_view(step=1, feedback={"status": "accepted", "elapsed_seconds": 60})) == action2
    first, second = client.requests[0][0], client.requests[1][0]
    assert [x["role"] for x in first] == ["system", "user"]
    assert [x["role"] for x in second] == ["system", "user", "assistant", "tool"]
    assistant, tool = second[-2:]
    assert assistant == v7.canonical_assistant_tool_call("call_1", action1)
    assert tool["tool_call_id"] == assistant["tool_calls"][0]["id"] == "call_1"
    assert json.loads(tool["content"]) == {
        "action_result": {"status": "accepted", "elapsed_seconds": 60},
        "current_observation": fake_view(step=1)["observation"],
    }
    # The second action is retained for the next turn/report, not prematurely paired.
    assert policy.history[-1] == v7.canonical_assistant_tool_call("call_2", action2)


def test_initial_only_query_profile_obs0_and_no_duplicate_previous_fields() -> None:
    client = FakeClient([native_response("one", {"kind": "wait", "mode": "for", "duration_seconds": 60}),
                         native_response("two", {"kind": "act", "commands": []})])
    policy = v7.NativeToolHistoryPolicy(client, native_tools_verified=True)
    initial = fake_view()
    policy.decide(initial)
    policy.decide(fake_view(step=1, feedback={"status": "accepted"}))
    messages = client.requests[1][0]
    assert sum(x["role"] == "user" for x in messages) == 1
    user = json.loads(messages[1]["content"])
    assert user == {"original_query": initial["query"], "public_preferences": initial["public_profile"],
                    "initial_observation": initial["observation"]}
    serialized = json.dumps(messages)
    assert "previous_action" not in serialized and "previous_action_result" not in serialized
    assert initial["query"] not in messages[0]["content"]


def test_fresh_complete_observation_and_events_active_rules_metrics_are_in_tool_result() -> None:
    client = FakeClient([native_response("one", {"kind": "wait", "mode": "for", "duration_seconds": 60}),
                         native_response("two", {"kind": "act", "commands": []})])
    policy = v7.NativeToolHistoryPolicy(client, native_tools_verified=True)
    policy.decide(fake_view())
    current = fake_view(step=1, feedback={"status": "accepted"})
    current["observation"]["active_rule_ids"] = ["rule.live"]
    current["observation"]["metrics"]["device_command_count"] = 7
    policy.decide(current)
    observation = json.loads(client.requests[1][0][-1]["content"])["current_observation"]
    assert observation == current["observation"]
    assert observation["events"] and observation["active_rule_ids"] == ["rule.live"]
    assert observation["metrics"]["device_command_count"] == 7


def test_system_and_request_tools_are_static_and_complete() -> None:
    action = {"kind": "wait", "mode": "for", "duration_seconds": 60}
    client = FakeClient([native_response("a", action), native_response("b", action)])
    policy = v7.NativeToolHistoryPolicy(client, native_tools_verified=True)
    policy.decide(fake_view()); policy.decide(fake_view(step=1, feedback={"status": "accepted"}))
    assert client.requests[0][0][0] == client.requests[1][0][0]
    assert client.requests[0][1] == client.requests[1][1]
    assert client.requests[0][2] == client.requests[1][2] == v7.TOOL_CHOICE
    assert client.requests[0][0][0] == {"role": "system", "content": v7.V7_HOUSEHOLD_AGENT_RULES}
    system = client.requests[0][0][0]["content"]
    for forbidden in ("action_grammar", "action_examples", "device_interfaces", "event_catalog"):
        assert forbidden not in system
    assert {x["function"]["name"] for x in client.requests[0][1]} == set(v7.TOOL_NAMES.values())


def test_dynamic_tools_carry_exact_device_operations_and_query_scoped_event_fields() -> None:
    view = fake_view()
    selected, _ = v4.select_event_types_for_query(view["query"])
    interfaces = v4.device_interfaces_from_observation(view["observation"])
    tools = v7.build_native_tools(interfaces, selected)

    def assert_strict_objects(node) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node.get("additionalProperties") is False
                assert set(node.get("required", [])) == set(node.get("properties", {}))
            for value in node.values():
                assert_strict_objects(value)
        elif isinstance(node, list):
            for value in node:
                assert_strict_objects(value)

    assert_strict_objects(tools)
    act = next(x for x in tools if x["function"]["name"] == v7.TOOL_NAMES["act"])
    branches = act["function"]["parameters"]["properties"]["commands"]["items"]["anyOf"]
    assert len(branches) == 1
    props = branches[0]["properties"]
    assert props["device_id"]["enum"] == ["fridge_door.main"]
    assert props["capability"]["enum"] == ["fridge.door"]
    assert props["operation"]["enum"] == ["close"]
    assert props["parameters"] == {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
    event_tool = next(x for x in tools if x["function"]["name"] == v7.TOOL_NAMES["wait_until_event"])
    event_branches = event_tool["function"]["parameters"]["properties"]["event_filter"]["anyOf"]
    assert len(event_branches) == len(selected)
    assert {x["properties"]["type"]["enum"][0] for x in event_branches} == set(selected)


def test_all_frozen_episodes_publish_all_32_exact_operation_branches_and_run_context_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def forbid_network(*args, **kwargs):
        raise AssertionError("the 15-Episode structural test must not call a live API")

    monkeypatch.setattr(v7, "urlopen", forbid_network)

    class OneStepBackend:
        def __init__(self, initial):
            self.initial = deepcopy(initial)

        def reset(self, episode):
            return BackendStep(deepcopy(self.initial), {"private": "not observable"}, False)

        def state_digest(self):
            return "static"

        def execute_atomic(self, action):
            return ActionOutcome(True, {"status": "accepted"}, {"status": "accepted"})

        def advance(self, action):
            final = deepcopy(self.initial)
            final["step"] = int(final["step"]) + 1
            final["events"] = [{"type": "rule_fired", "step": final["step"], "rule_id": "probe"}]
            final["terminal_reason"] = "one_step_test_complete"
            return BackendStep(final, {"private": "not observable"}, True)

    for index in v7.SAMPLE_INDICES:
        public, _, spec, backend = v4.load_temporal_episode(index, v7.RELEASE_DIR)
        observation = backend.reset(spec).public_observation
        selected, _ = v4.select_event_types_for_query(public["query"])
        tools = v7.build_native_tools(v4.device_interfaces_from_observation(observation), selected)
        act = next(x for x in tools if x["function"]["name"] == v7.TOOL_NAMES["act"])
        branches = act["function"]["parameters"]["properties"]["commands"]["items"]["anyOf"]
        tuples = {(x["properties"]["device_id"]["enum"][0], x["properties"]["capability"]["enum"][0],
                   x["properties"]["operation"]["enum"][0]) for x in branches}
        assert len(branches) == len(tuples) == 32

        client = FakeClient([native_response("call_" + spec.episode_id, {
            "kind": "wait", "mode": "for", "duration_seconds": 60,
        })])
        policy = v7.NativeToolHistoryPolicy(client, native_tools_verified=True)
        run = Harness(OneStepBackend(observation)).run_one(spec, policy)
        policy.finalize(run.public_trace)
        assert run.status == "completed"
        assert [x["role"] for x in policy.history] == ["system", "user", "assistant", "tool"]
        assert json.loads(policy.history[1]["content"])["original_query"] == public["query"]
        terminal_tool = json.loads(policy.history[-1]["content"])
        assert terminal_tool["action_result"] == {"status": "accepted"}
        assert terminal_tool["current_observation"]["terminal_reason"] == "one_step_test_complete"
        assert "private" not in json.dumps(policy.history)


def test_raw_thinking_is_never_retained_but_response_is_fingerprinted() -> None:
    secret = "PRIVATE CHAIN OF THOUGHT: 12345"
    client = FakeClient([native_response("a", {"kind": "act", "commands": []}, content=secret)])
    policy = v7.NativeToolHistoryPolicy(client, native_tools_verified=True)
    policy.decide(fake_view())
    record = policy.output_records[0]
    assert record["canonical_action"] == {"kind": "act", "commands": []}
    assert len(record["raw_response_sha256"]) == 64 and record["raw_response_byte_length"] > 0
    assert secret not in json.dumps(policy.history) and secret not in json.dumps(policy.output_records)
    assert all("raw_content" not in x for x in policy.output_records)


@pytest.mark.parametrize("arguments,error", [
    ('{"kind":"wait","kind":"act"}', "duplicate_json_key"),
    ('{"kind":"wait","mode":"for","duration_seconds":NaN}', "nonfinite_json_constant"),
])
def test_strict_duplicate_and_nonfinite_tool_arguments(arguments: str, error: str) -> None:
    response = native_response("x", {"kind": "act", "commands": []})
    response["message"]["tool_calls"][0]["function"]["arguments"] = arguments
    policy = v7.NativeToolHistoryPolicy(FakeClient([response]), native_tools_verified=True)
    assert policy.decide(fake_view()) == {"kind": "invalid_model_output"}
    assert error in policy.output_records[0]["protocol_error"]


@pytest.mark.parametrize("message,error", [
    ({"role": "assistant", "content": "plain answer"}, "exactly_one_native_tool_call_required"),
    ({"role": "assistant", "tool_calls": []}, "exactly_one_native_tool_call_required"),
    ({"role": "assistant", "tool_calls": [{"id": "x", "type": "function", "function": {"name": "wrong", "arguments": "{}"}}]}, "unexpected_native_tool_name"),
    ({"role": "assistant", "tool_calls": [{"id": "x", "type": "function", "function": {"name": v7.TOOL_NAMES["act"], "arguments": "not-json"}}]}, "response_not_plain_json_object"),
])
def test_malformed_or_non_tool_responses_fail_closed(message: dict, error: str) -> None:
    policy = v7.NativeToolHistoryPolicy(FakeClient([{"message": message, "usage": {}}]), native_tools_verified=True)
    assert policy.decide(fake_view()) == {"kind": "invalid_model_output"}
    assert error in policy.output_records[0]["protocol_error"]
    assert policy.history[-1]["role"] == "user"


def test_native_support_is_a_hard_gate() -> None:
    with pytest.raises(RuntimeError, match="verified native-tools"):
        v7.NativeToolHistoryPolicy(FakeClient([]), native_tools_verified=False)


def test_split_token_accounting_has_no_generic_tokens_and_retains_provider_total() -> None:
    response = native_response("a", {"kind": "act", "commands": []},
                               usage={"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 999})
    policy = v7.NativeToolHistoryPolicy(FakeClient([response]), native_tools_verified=True)
    policy.decide(fake_view())
    usage = policy.output_records[0]["usage"]
    assert usage["prompt_tokens"] == 100 and usage["completion_tokens"] == usage["output_tokens"] == 20
    assert usage["provider_total_tokens"] == 999 and usage["provider_total_tokens_matches_sum"] is False
    assert "tokens" not in usage and policy.provider_total_tokens_mismatch_calls == 1
    missing = v7.usage_token_record({"prompt_tokens": 7})
    assert missing["provider_total_tokens"] == 0 and missing["provider_total_tokens_matches_sum"] is None


def minimal_result(episode_id: str) -> dict:
    position = v7.SAMPLE_EPISODE_IDS.index(episode_id)
    public, _ = v5.assert_release_and_sample_frozen()
    prompt_hash, selected = v7.expected_episode_prompt(v7.SAMPLE_INDICES[position])
    action = {"kind": "act", "commands": []}
    initial = {"query": public[position]["query"], "public_profile": {},
               "observation": {"episode_id": episode_id}, "last_feedback": None}
    conversation = [
        {"role": "system", "content": v7.build_system_content()},
        v7.build_initial_user_message(initial),
        v7.canonical_assistant_tool_call("fixture_call", action),
        v7.build_tool_observation("fixture_call", {"status": "accepted"}, {"episode_id": episode_id}),
    ]
    return {
        "episode_id": episode_id, "query": public[position]["query"], "model": DEFAULT_MODEL,
        "responsibility_success": False, "run_status": "completed", "device_command_count": 0,
        "count_unit": "device_command", "calls": 1, "api_successes": 1, "parsed_json_actions": 1,
        "rejected_action_count": 0, "prompt_tokens": 10, "completion_tokens": 3, "output_tokens": 3,
        "provider_total_tokens": 13, "provider_total_tokens_present_calls": 1,
        "provider_total_tokens_mismatch_calls": 0, "latency_ms": 1.0, "harness_action_records": 1,
        "accepted_harness_actions": 1, "protocol_error_count": 0, "backend_error_count": 0,
        "max_consecutive_identical_rejected_action": 0, "system_prompt_sha256": prompt_hash,
        "native_tools_sha256": v7.expected_episode_tools_sha256(v7.SAMPLE_INDICES[position]),
        "selected_event_types": list(selected), "errors": {}, "canonical_conversation": conversation,
    }


def evidence() -> dict:
    return {"verified_native_tools": True, "verdict": "supported", "http_status": 200,
            "base_url": DEFAULT_BASE, "model": DEFAULT_MODEL,
            "request_sha256": probe.probe_request_sha256(DEFAULT_MODEL),
            "tools_sha256": probe.probe_tools_sha256(), "probe_kind": probe.PROBE_KIND,
            "probe_schema_version": probe.PROBE_SCHEMA_VERSION,
            "returned_tool_name": v7.TOOL_NAMES["wait_for"],
            "returned_arguments": {"kind": "wait", "mode": "for", "duration_seconds": 60},
            "response_message_sha256": "1" * 64, "response_message_byte_length": 123}


def test_report_metrics_manifest_and_atomic_checkpoint_safety(tmp_path: Path) -> None:
    rows = [minimal_result(v7.SAMPLE_EPISODE_IDS[0])]
    report = v7.build_report(rows, execution_finished=False, native_tools_evidence=evidence())
    assert report["metrics_all"]["output_tokens"] == 3
    assert report["metrics_all"]["provider_total_tokens"] == 13
    assert "tokens" not in report["metrics_all"]
    assert report["experiment_config"]["migration_manifest"] == v7.MIGRATION_MANIFEST
    path = tmp_path / "report.json"
    v7.atomic_write_report(path, report)
    assert not path.with_suffix(".json.tmp").exists()
    assert v7.load_checkpoint(path, native_tools_evidence=evidence()) == rows
    tampered = deepcopy(report); tampered["metrics_all"]["output_tokens"] = 99
    v7.atomic_write_report(path, tampered)
    with pytest.raises(RuntimeError, match="metadata or metrics mismatch"):
        v7.load_checkpoint(path, native_tools_evidence=evidence())
    bad_pair = v7.build_report([minimal_result(v7.SAMPLE_EPISODE_IDS[0])], execution_finished=False,
                               native_tools_evidence=evidence())
    bad_pair["episodes"][0]["canonical_conversation"][-1]["tool_call_id"] = "forged"
    v7.atomic_write_report(path, bad_pair)
    with pytest.raises(RuntimeError, match="tool pairing"):
        v7.load_checkpoint(path, native_tools_evidence=evidence())
    bad_tokens = v7.build_report([minimal_result(v7.SAMPLE_EPISODE_IDS[0])], execution_finished=False,
                                 native_tools_evidence=evidence())
    del bad_tokens["episodes"][0]["output_tokens"]
    v7.atomic_write_report(path, bad_tokens)
    with pytest.raises(RuntimeError, match="token fields"):
        v7.load_checkpoint(path, native_tools_evidence=evidence())
    with pytest.raises(RuntimeError, match="all 15"):
        v7.build_report(rows, execution_finished=True, native_tools_evidence=evidence())


def test_completed_checkpoint_and_old_schema_are_refused(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    complete = v7.build_report([minimal_result(x) for x in v7.SAMPLE_EPISODE_IDS],
                               execution_finished=True, native_tools_evidence=evidence())
    v7.atomic_write_report(path, complete)
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        v7.load_checkpoint(path, native_tools_evidence=evidence())
    path.write_text(json.dumps({"schema_version": v6.SCHEMA_VERSION}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="not a v7"):
        v7.load_checkpoint(path, native_tools_evidence=evidence())


def test_migration_manifest_is_complete_and_private_material_cannot_enter_context() -> None:
    required = {"frozen_v5_sample", "household_agent_rules", "public_action_kinds", "action_schemas",
                "action_examples", "device_interfaces", "event_schemas", "public_profile", "state", "events",
                "active_rules", "metrics", "receipts", "checkpoint_invariants", "private_data_exclusion",
                "raw_thinking_exclusion", "native_tools_gate"}
    assert set(v7.MIGRATION_MANIFEST) == required
    context = v7.build_system_content()
    context += json.dumps(v7.build_initial_user_message(fake_view()))
    for banned in ("scenario_type", "required_actions", "reference_policy", "episodes_private", "contract",
                   "evaluator", "future_schedule"):
        assert banned not in context.lower()


def test_probe_response_parsing_is_machine_readable_and_content_free() -> None:
    payload = {"choices": [{"message": {"content": "hidden reasoning", "tool_calls": [{
        "id": "probe", "type": "function", "function": {
            "name": v7.TOOL_NAMES["wait_for"],
            "arguments": json.dumps({"kind": "wait", "mode": "for", "duration_seconds": 60}),
        },
    }]}}]}
    verdict = probe.parse_probe_response(payload)
    assert verdict["verified_native_tools"] is True and verdict["verdict"] == "supported"
    assert verdict["returned_tool_name"] == v7.TOOL_NAMES["wait_for"]
    assert verdict["returned_arguments"] == {"kind": "wait", "mode": "for", "duration_seconds": 60}
    assert len(verdict["response_message_sha256"]) == 64
    assert verdict["response_message_byte_length"] > 0
    assert "hidden reasoning" not in json.dumps(verdict)
    bad = probe.parse_probe_response({"choices": [{"message": {"content": "no tool"}}]})
    assert bad["verified_native_tools"] is False


class FakeHTTPResponse:
    status = 200

    def __init__(self, payload: dict):
        self.raw = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return self.raw


def successful_probe_response() -> FakeHTTPResponse:
    return FakeHTTPResponse({"choices": [{"message": {"content": None, "tool_calls": [{
        "id": "probe-call", "type": "function", "function": {
            "name": v7.TOOL_NAMES["wait_for"],
            "arguments": json.dumps({"kind": "wait", "mode": "for", "duration_seconds": 60}),
        },
    }]}}]})


def test_probe_retries_transient_url_error_then_succeeds_without_changing_request_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    sleeps = []

    def fake_urlopen(request, timeout):
        calls.append((request.data, timeout))
        if len(calls) == 1:
            raise URLError(ConnectionResetError())
        return successful_probe_response()

    monkeypatch.setattr(probe, "urlopen", fake_urlopen)
    monkeypatch.setattr(probe.time, "sleep", sleeps.append)
    before = probe.probe_request_sha256(DEFAULT_MODEL)
    result = probe.probe(DEFAULT_BASE, DEFAULT_MODEL, api_key="test-key", retries=2)
    assert result["verified_native_tools"] is True
    assert len(calls) == 2 and calls[0][0] == calls[1][0] == probe.probe_request_bytes(DEFAULT_MODEL)
    assert sleeps == [0.5]
    assert result["request_sha256"] == before == probe.probe_request_sha256(DEFAULT_MODEL)


def test_probe_exhausts_transient_errors_with_safe_diagnostic(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    sleeps = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        raise URLError(ConnectionResetError("must-not-leak"))

    monkeypatch.setattr(probe, "urlopen", fake_urlopen)
    monkeypatch.setattr(probe.time, "sleep", sleeps.append)
    credentialed_url = "https://url-user:url-password@aigc.sankuai.com/v1/openai/native?token=url-token"
    result = probe.probe(credentialed_url, DEFAULT_MODEL, api_key="secret-key", retries=2)
    assert len(calls) == 3 and sleeps == [0.5, 1.0]
    assert result["verified_native_tools"] is False
    assert result["verdict"] == "transport_error" and result["reason"] == "connection_reset"
    serialized = json.dumps(result)
    for secret in ("must-not-leak", "secret-key", "url-user", "url-password", "url-token"):
        assert secret not in serialized


def test_probe_does_not_retry_nonretryable_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    sleeps = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        raise HTTPError(request.full_url, 401, "must-not-leak", hdrs=None, fp=None)

    monkeypatch.setattr(probe, "urlopen", fake_urlopen)
    monkeypatch.setattr(probe.time, "sleep", sleeps.append)
    result = probe.probe(DEFAULT_BASE, DEFAULT_MODEL, api_key="secret-key", retries=2)
    assert len(calls) == 1 and sleeps == []
    assert result["verdict"] == "http_error" and result["reason"] == "http_401"
    assert "must-not-leak" not in json.dumps(result)


def test_probe_retries_retryable_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        if len(calls) == 1:
            raise HTTPError(request.full_url, 503, "temporary", hdrs=None, fp=None)
        return successful_probe_response()

    monkeypatch.setattr(probe, "urlopen", fake_urlopen)
    monkeypatch.setattr(probe.time, "sleep", lambda _: None)
    result = probe.probe(DEFAULT_BASE, DEFAULT_MODEL, api_key="test-key", retries=2)
    assert len(calls) == 2 and result["verified_native_tools"] is True


def test_verified_evidence_must_match_frozen_endpoint_and_model(tmp_path: Path) -> None:
    path = tmp_path / "probe.json"
    path.write_text(json.dumps(evidence()), encoding="utf-8")
    assert v7.load_verified_evidence(path)["verified_native_tools"] is True
    for field, value in (("model", "other"), ("verdict", "maybe"), ("http_status", 201),
                         ("probe_kind", "weak_probe"), ("request_sha256", "a" * 64),
                         ("tools_sha256", "b" * 64), ("returned_tool_name", "submit_act"),
                         ("returned_arguments", {"kind": "act", "commands": []}),
                         ("response_message_sha256", "not-a-sha"),
                         ("response_message_byte_length", 0)):
        wrong = evidence(); wrong[field] = value
        path.write_text(json.dumps(wrong), encoding="utf-8")
        with pytest.raises(RuntimeError, match="does not verify"):
            v7.load_verified_evidence(path)


def test_legacy_or_handwritten_weak_evidence_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "probe.json"
    weak = {"verified_native_tools": True, "verdict": "supported", "http_status": 200}
    path.write_text(json.dumps(weak), encoding="utf-8")
    with pytest.raises(RuntimeError, match="does not verify"):
        v7.load_verified_evidence(path)

    for missing in ("returned_tool_name", "returned_arguments", "response_message_sha256",
                    "response_message_byte_length"):
        incomplete = evidence(); del incomplete[missing]
        path.write_text(json.dumps(incomplete), encoding="utf-8")
        with pytest.raises(RuntimeError, match="does not verify"):
            v7.load_verified_evidence(path)


@pytest.mark.parametrize("mutation", ["swapped", "wrong_role", "duplicate_id", "missing_tool"])
def test_checkpoint_rejects_conversation_pairing_tampering(mutation: str) -> None:
    row = minimal_result(v7.SAMPLE_EPISODE_IDS[0])
    first_assistant = row["canonical_conversation"][2]
    first_tool = row["canonical_conversation"][3]
    if mutation == "swapped":
        row["canonical_conversation"][2:4] = [first_tool, first_assistant]
    elif mutation == "wrong_role":
        first_tool["role"] = "user"
    elif mutation == "duplicate_id":
        row["canonical_conversation"].extend([deepcopy(first_assistant), deepcopy(first_tool)])
    else:
        row["canonical_conversation"].pop()
    with pytest.raises(RuntimeError, match="canonical|pairing|unpaired"):
        v7.validate_result_prefix([row])
