from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import threading
import time

import pytest

import evaluate_workflow_v4_flash_v8 as v8
import evaluate_workflow_v4_flash_v9 as v9
import evaluate_workflow_v4_flash_v10 as v10


ACTION1 = {"kind": "wait", "mode": "for", "duration_seconds": 60}
ACTION2 = {"kind": "act", "commands": []}
ACTION3 = {"kind": "install_rule", "rule": {"id": "r1", "fire_at_step": 5, "commands": [{"device": "d1", "on": True}]}}


class FakeClient:
    model = v10.DEFAULT_MODEL

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


def envelope(action):
    return "silent reasoning must disappear\n<answer>" + v10.canonical_json(action) + "</answer>"


def observation(*, step=0, devices=None, events=None, rules=(), extra=None, inventory=None):
    doc = {
        "episode_id": "ep-test",
        "step": step,
        "time": f"2026-01-01T00:{step:02d}:00Z",
        "tick_seconds": 60,
        "device_states": deepcopy(devices or {}),
        "events": deepcopy(events or []),
        "active_rule_ids": list(rules),
        # the Episode inventory is fixed for the whole Episode, so the static
        # system prefix stays byte-identical across calls
        "inventory": {"devices": [
            {
                "device_id": device_id,
                "device_type": "generic",
                "capabilities": [],
                "interfaces": {},
            }
            for device_id in sorted(inventory if inventory is not None else (devices or {}).keys())
        ]},
        "metrics": {"energy_wh": step * 10},
        "workflow": {"status": "running"},
    }
    if extra:
        doc.update(deepcopy(extra))
    return doc


def view(*, feedback=None, obs=None):
    return {
        "query": "Keep watching the home.",
        "public_profile": {"name": "resident"},
        "observation": deepcopy(obs or observation()),
        "last_feedback": deepcopy(feedback),
    }


def env_payload(message):
    return json.loads(message["content"])


# ---------------------------------------------------------------- delta codec


def test_diff_apply_round_trip_on_nested_home_observations():
    before = observation(step=0, devices={"d1": {"on": False, "mode": "auto"}}, events=[{"type": "a"}], rules=[1, 2])
    after = observation(
        step=3,
        devices={"d1": {"on": True, "mode": "auto"}, "d2": {"level": 50}},
        events=[{"type": "a"}, {"type": "b", "payload": {"x": 1}}],
        rules=[2],
        extra={"terminal_reason": None, "future": {"nested": {"deep": [1, 2]}}},
    )
    delta = v10.observation_delta(before, after)
    assert v10.apply_observation_delta(before, delta) == after
    assert v10.canonical_json(v10.apply_observation_delta(before, delta)) == v10.canonical_json(after)


def test_identical_snapshots_get_explicit_none_op():
    before = observation(step=2)
    delta = v10.observation_delta(before, deepcopy(before))
    assert delta == {"op": "none"}
    assert v10.apply_observation_delta(before, delta) == before


def test_missing_is_distinguished_from_null():
    before = {"a": None, "b": 1}
    after = {"b": 1, "c": None}
    delta = v10.observation_delta(before, after)
    assert delta["op"] == "object"
    assert delta["removed"] == ["a"]
    assert delta["added"] == {"c": None}
    assert "changed" not in delta
    assert v10.apply_observation_delta(before, delta) == after
    null_to_value = v10.observation_delta({"a": None}, {"a": 1})
    assert null_to_value["changed"]["a"]["op"] == "replace"
    assert null_to_value["changed"]["a"]["before"] is None
    assert null_to_value["changed"]["a"]["after"] == 1
    assert v10.apply_observation_delta({"a": None}, null_to_value) == {"a": 1}


def test_nested_dict_list_and_scalar_changes():
    before = {"devices": {"d1": {"on": False}}, "events": [1, 2, 3], "workflow": {"phase": "idle"}}
    after = {"devices": {"d1": {"on": True}}, "events": [1, 9, 3], "workflow": {"phase": "done"}}
    delta = v10.observation_delta(before, after)
    assert delta["changed"]["devices"]["changed"]["d1"]["changed"]["on"]["after"] is True
    assert delta["changed"]["events"]["op"] == "array"
    assert delta["changed"]["events"]["changed"] == {"1": {"op": "replace", "before": 2, "after": 9}}
    assert v10.apply_observation_delta(before, delta) == after


def test_list_length_change_and_type_change_become_exact_replaces():
    delta = v10.observation_delta({"ids": [1, 2]}, {"ids": [1, 2, 3]})
    assert delta["changed"]["ids"] == {"op": "replace", "before": [1, 2], "after": [1, 2, 3]}
    delta = v10.observation_delta({"x": {"a": 1}}, {"x": [1]})
    assert delta["changed"]["x"] == {"op": "replace", "before": {"a": 1}, "after": [1]}
    assert v10.apply_observation_delta({"x": {"a": 1}}, delta) == {"x": [1]}


def test_step_and_clock_changes_are_explicit_before_after_replaces():
    before = observation(step=4)
    after = observation(step=5)
    delta = v10.observation_delta(before, after)
    assert delta["changed"]["step"] == {"op": "replace", "before": 4, "after": 5}
    assert delta["changed"]["time"]["before"] == "2026-01-01T00:04:00Z"
    assert delta["changed"]["time"]["after"] == "2026-01-01T00:05:00Z"


def test_bool_and_int_are_not_conflated():
    delta = v10.observation_delta({"flag": 1}, {"flag": True})
    assert delta["changed"]["flag"] == {"op": "replace", "before": 1, "after": True}
    assert v10.apply_observation_delta({"flag": 1}, delta) == {"flag": True}


def test_delta_is_deterministic_canonical_json():
    before = {"b": 1, "a": 1, "z": {"y": 2, "x": 1}}
    after = {"a": 2, "b": 1, "z": {"x": 1, "y": 3}, "new": 0}
    first = v10.canonical_json(v10.observation_delta(before, after))
    second = v10.canonical_json(v10.observation_delta(deepcopy(before), deepcopy(after)))
    assert first == second
    delta = v10.observation_delta({"k2": 1, "k1": 2}, {})
    assert delta["removed"] == ["k1", "k2"]


def test_apply_is_strict_about_every_expectation():
    with pytest.raises(RuntimeError, match="before-value"):
        v10.apply_observation_delta(5, {"op": "replace", "before": 4, "after": 6})
    with pytest.raises(RuntimeError, match="missing key"):
        v10.apply_observation_delta({}, {"op": "object", "removed": ["x"]})
    with pytest.raises(RuntimeError, match="existing key"):
        v10.apply_observation_delta({"x": 1}, {"op": "object", "added": {"x": 2}})
    with pytest.raises(RuntimeError, match="missing key"):
        v10.apply_observation_delta({}, {"op": "object", "changed": {"x": {"op": "none"}}})
    with pytest.raises(RuntimeError, match="removes a key twice"):
        v10.apply_observation_delta({"x": 1}, {"op": "object", "removed": ["x", "x"]})
    with pytest.raises(RuntimeError, match="removed key"):
        v10.apply_observation_delta({"x": 1}, {"op": "object", "removed": ["x"], "added": {"x": 2}})
    with pytest.raises(RuntimeError, match="dict snapshot"):
        v10.apply_observation_delta([], {"op": "object", "added": {"x": 1}})
    with pytest.raises(RuntimeError, match="list snapshot"):
        v10.apply_observation_delta({}, {"op": "array", "changed": {"0": {"op": "none"}}})
    with pytest.raises(RuntimeError, match="out of range"):
        v10.apply_observation_delta([1], {"op": "array", "changed": {"3": {"op": "none"}}})
    with pytest.raises(RuntimeError, match="unknown observation delta op"):
        v10.apply_observation_delta({}, {"op": "merge"})
    with pytest.raises(RuntimeError, match="must be empty"):
        v10.apply_observation_delta({}, {"op": "none", "added": {"x": 1}})
    with pytest.raises(RuntimeError, match="op-tagged"):
        v10.apply_observation_delta({}, {"before": 1})


# ------------------------------------------------------- sent request layout


def run_policy_over_four_decisions(client):
    policy = v10.CompactDeltaObservationPolicy(client)
    observations = [
        observation(step=0, devices={"d1": {"on": False}}),
        observation(step=1, devices={"d1": {"on": True}}, events=[{"type": "device_done"}]),
        observation(step=2, devices={"d1": {"on": True}, "d2": {"level": 3}}, events=[{"type": "device_done"}]),
        observation(step=3, devices={"d2": {"level": 3}}, rules=[7], extra={"terminal_reason": None}),
    ]
    for obs in observations:
        obs["inventory"] = deepcopy(observations[0]["inventory"])
    feedbacks = [
        {"accepted": True, "error_code": None},
        {"accepted": True, "error_code": None},
        {"accepted": False, "error_code": "E_UNKNOWN_KIND"},
    ]
    policy.decide(view(obs=observations[0]))
    policy.decide(view(feedback=feedbacks[0], obs=observations[1]))
    policy.decide(view(feedback=feedbacks[1], obs=observations[2]))
    policy.decide(view(feedback=feedbacks[2], obs=observations[3]))
    trace = tuple(
        [{"type": "observation", "index": i, "value": deepcopy(obs)} for i, obs in enumerate(observations)]
        + [{"type": "action", "index": 3, "action": deepcopy(ACTION3), "accepted": True, "feedback": {"accepted": True}}]
        + [{"type": "observation", "index": 4, "value": observation(step=4, devices={"d2": {"level": 3}}, rules=[7])}]
    )
    policy.finalize(trace)
    return policy, observations, feedbacks


def test_first_request_has_only_static_system_and_initial_full_observation():
    client = FakeClient([envelope(ACTION1)])
    v10.CompactDeltaObservationPolicy(client).decide(view())
    request = client.requests[0]
    assert [message["role"] for message in request] == ["system", "user"]
    payload = env_payload(request[1])
    assert payload["message_type"] == "initial_request"
    assert set(payload) == {"message_type", "original_query", "public_preferences", "initial_observation"}


def test_append_only_message_sequence_over_four_actions_is_never_rebuilt():
    client = FakeClient([envelope(ACTION1), envelope(ACTION2), envelope(ACTION3), envelope(ACTION2)])
    policy, observations, feedbacks = run_policy_over_four_decisions(client)
    assert len(client.requests) == 4
    expected_roles = [
        ["system", "user"],
        ["system", "user", "assistant", "user"],
        ["system", "user", "assistant", "user", "assistant", "user"],
        ["system", "user", "assistant", "user", "assistant", "user", "assistant", "user"],
    ]
    for request, roles in zip(client.requests, expected_roles):
        assert [message["role"] for message in request] == roles
    # strictly append-only: every later request extends the earlier one verbatim
    for earlier, later in zip(client.requests, client.requests[1:]):
        assert later[:len(earlier)] == earlier
    # the persisted canonical conversation is the final sent request plus the
    # finalized environment turn for the last action
    final_history = policy.build_request()
    assert final_history[:len(client.requests[3])] == client.requests[3]
    assert len(final_history) == len(client.requests[3]) + 2
    assert [message["role"] for message in policy.history] == expected_roles[3] + ["assistant", "user"]


def test_no_full_observation_snapshot_after_the_initial_user_message():
    client = FakeClient([envelope(ACTION1), envelope(ACTION2), envelope(ACTION3), envelope(ACTION2)])
    policy, observations, feedbacks = run_policy_over_four_decisions(client)
    for request in client.requests:
        assert '"current_observation"' not in json.dumps(request)
    for request in client.requests[1:]:
        for message in request[3::2]:
            payload = env_payload(message)
            assert set(payload) == {"message_type", "action_result", "observation_delta"}
            assert payload["message_type"] == "environment_observation"
    # the latest full observation stays in process memory only, and the full
    # snapshot is never duplicated anywhere in the persisted history
    assert policy._last_observation["step"] == 4
    assert sum(message["content"].count('"episode_id":"ep-test"') for message in policy.history) == 1


def test_every_environment_delta_is_relative_to_the_immediately_preceding_observation():
    client = FakeClient([envelope(ACTION1), envelope(ACTION2), envelope(ACTION3), envelope(ACTION2)])
    policy, observations, feedbacks = run_policy_over_four_decisions(client)
    final_request = client.requests[3]
    env_turns = [env_payload(message) for message in final_request[3::2]]
    prior = env_payload(final_request[1])["initial_observation"]
    expected_feedbacks = feedbacks + [{"accepted": True}]
    for position, payload in enumerate(env_turns):
        assert payload["action_result"] == expected_feedbacks[position]
        assert payload["observation_delta"] == v10.observation_delta(prior, observations[position + 1])
        prior = v10.apply_observation_delta(prior, payload["observation_delta"])
        assert prior == observations[position + 1]
    # a device that disappears again surfaces as an explicit removal in the
    # delta computed against the immediately preceding observation
    assert "d1" in observations[2]["device_states"] and "d1" not in observations[3]["device_states"]
    assert env_turns[2]["observation_delta"]["changed"]["device_states"]["removed"] == ["d1"]
    assert env_turns[1]["observation_delta"]["changed"]["device_states"]["added"] == {"d2": {"level": 3}}


def test_all_canonical_actions_and_results_are_retained_in_order():
    client = FakeClient([envelope(ACTION1), envelope(ACTION2), envelope(ACTION3), envelope(ACTION2)])
    policy, _, feedbacks = run_policy_over_four_decisions(client)
    final_history = policy.build_request()
    sent_actions = [v8.v6.parse_answer_envelope(message["content"]) for message in final_history[2::2]]
    assert sent_actions == [ACTION1, ACTION2, ACTION3, ACTION2]
    assert client.requests[3][2] == v8.canonical_assistant_action(ACTION1)
    env_turns = [env_payload(message) for message in final_history[3::2]]
    assert [payload["action_result"] for payload in env_turns] == feedbacks + [{"accepted": True}]


def test_no_thinking_or_raw_model_content_is_replayed_or_persisted():
    sentinel = "PRIVATE_THINKING_SENTINEL"
    client = FakeClient([
        sentinel + "<answer>" + v10.canonical_json(ACTION1) + "</answer>",
        envelope(ACTION2),
    ])
    policy = v10.CompactDeltaObservationPolicy(client)
    policy.decide(view())
    policy.decide(view(feedback={"accepted": True}, obs=observation(step=1)))
    assert sentinel not in json.dumps(policy.build_request())
    assert sentinel not in json.dumps(policy.output_records)
    assert sentinel not in json.dumps(policy.history)
    assert policy.output_records[0]["raw_response_byte_length"] == len(sentinel.encode()) + len(
        ("<answer>" + v10.canonical_json(ACTION1) + "</answer>").encode()
    )
    assert policy.output_records[0]["raw_response_sha256"]


def test_finalize_appends_last_feedback_and_delta_turn():
    client = FakeClient([envelope(ACTION1)])
    policy = v10.CompactDeltaObservationPolicy(client)
    policy.decide(view())
    trace = (
        {"type": "observation", "index": 0, "value": observation(step=0)},
        {"type": "action", "index": 0, "action": ACTION1, "accepted": True, "feedback": {"ok": True}},
        {"type": "observation", "index": 1, "value": observation(step=1)},
    )
    policy.finalize(trace)
    final = env_payload(policy.build_request()[-1])
    assert final["action_result"] == {"ok": True}
    assert final["observation_delta"] == v10.observation_delta(observation(step=0), observation(step=1))
    assert v10.apply_observation_delta(observation(step=0), final["observation_delta"]) == observation(step=1)


def test_finalize_without_pending_action_adds_nothing_and_invalid_output_leaves_no_transition():
    client = FakeClient(["totally malformed SECRET"])
    policy = v10.CompactDeltaObservationPolicy(client)
    assert policy.decide(view()) == {"kind": "invalid_model_output"}
    assert policy.pending_action is False
    policy.finalize(({"type": "observation", "index": 0, "value": observation()},))
    assert policy.build_request() == client.requests[0]
    record = policy.output_records[0]
    assert set(record) == {
        "call_index", "canonical_action", "raw_response_sha256", "raw_response_byte_length", "usage", "protocol_error"
    }
    assert record["canonical_action"] is None and record["protocol_error"].startswith("ValueError:")
    assert "SECRET" not in json.dumps(policy.output_records)


def test_api_error_aborts_without_canonical_action_or_protocol_error():
    class FailingClient:
        model = v10.DEFAULT_MODEL

        def complete(self, messages):
            raise v10.APIError("urlerror")

    policy = v10.CompactDeltaObservationPolicy(FailingClient())
    with pytest.raises(v10.APIError, match="urlerror"):
        policy.decide(view())
    assert policy.calls == 1 and policy.api_successes == 0
    assert policy.errors == {}
    assert policy.pending_action is False
    assert [message["role"] for message in policy.history] == ["system", "user"]


# ------------------------------------------------- v10 system prompt extension


V10_QUERY = "Keep watching the home."


def build_reference_system(obs, query=V10_QUERY):
    interfaces = v10.v8.v4.device_interfaces_from_observation(obs)
    selected, _ = v10.v8.v4.select_event_types_for_query(query)
    return v10.build_v10_system_content(query, interfaces, selected)


def test_build_v10_system_content_extends_v6_document_with_instructions_once():
    obs = observation(step=0, devices={"d1": {"on": False}})
    v6_content = v10.v8.v6.build_system_content(
        V10_QUERY,
        v10.v8.v4.device_interfaces_from_observation(obs),
        v10.v8.v4.select_event_types_for_query(V10_QUERY)[0],
    )
    content = build_reference_system(obs)
    document = json.loads(content)
    v6_document = json.loads(v6_content)
    assert set(document) == set(v6_document) | {v10.OBSERVATION_DELTA_PROTOCOL_KEY}
    for key, value in v6_document.items():
        assert document[key] == value
    instructions = document[v10.OBSERVATION_DELTA_PROTOCOL_KEY]["instructions"]
    assert list(instructions) == list(v10.OBSERVATION_DELTA_INSTRUCTIONS)
    # every required instruction statement occurs exactly once in the document
    phrases = [
        "full baseline",
        "immediately preceding reconstructed observation",
        "Apply the observation deltas sequentially",
        "op=none",
        "op=replace",
        "op=object",
        "op=array",
        "keyed by decimal index",
        "Never treat omitted unchanged fields as absent",
        "a missing key differs from an explicit null",
    ]
    for phrase in phrases:
        assert content.count(phrase) == 1, phrase
    # no internal evaluator/private data is exposed beyond the v6 document
    assert "last_observation" not in content
    assert "_last_observation" not in content
    assert content != v6_content


def test_build_v10_system_content_is_deterministic():
    obs = observation(step=0, devices={"d1": {"on": False}, "d2": {"level": 4}})
    assert build_reference_system(obs) == build_reference_system(deepcopy(obs))
    # the device-interface section of the v6 document drives the content
    assert build_reference_system(obs) != build_reference_system(
        observation(step=0, devices={"d9": {"on": False}})
    )


def test_policy_sends_v10_system_with_delta_instructions_and_keeps_it_byte_identical():
    client = FakeClient([envelope(ACTION1), envelope(ACTION2), envelope(ACTION3), envelope(ACTION2)])
    policy, observations, _ = run_policy_over_four_decisions(client)
    expected = build_reference_system(observations[0])
    for request in client.requests:
        assert request[0]["role"] == "system"
        assert request[0]["content"] == policy.system_content == expected
    # byte-identical system across every call, and exactly one protocol block
    assert client.requests[0][0]["content"] == client.requests[3][0]["content"]
    assert json.dumps(client.requests[3]).count(v10.OBSERVATION_DELTA_PROTOCOL_KEY) == 1
    assert policy.system_prompt_sha256 == v10.v8.v6.system_prompt_sha256(policy.system_content)
    # the extension is v10-specific: the v6-only content would differ
    assert policy.system_prompt_sha256 != v10.v8.v6.system_prompt_sha256(
        v10.v8.v6.build_system_content(
            V10_QUERY,
            v10.v8.v4.device_interfaces_from_observation(observations[0]),
            v10.v8.v4.select_event_types_for_query(V10_QUERY)[0],
        )
    )


def test_policy_history_stays_append_only_delta_only_with_v10_system():
    client = FakeClient([envelope(ACTION1), envelope(ACTION2), envelope(ACTION3), envelope(ACTION2)])
    policy, observations, feedbacks = run_policy_over_four_decisions(client)
    # append-only: every sent request extends the previous one verbatim
    for earlier, later in zip(client.requests, client.requests[1:]):
        assert later[:len(earlier)] == earlier
    # delta-only: no environment turn carries a full snapshot after obs0
    for request in client.requests[1:]:
        for message in request[3::2]:
            payload = env_payload(message)
            assert set(payload) == {"message_type", "action_result", "observation_delta"}
            assert "current_observation" not in payload
    prior = env_payload(client.requests[3][1])["initial_observation"]
    for position, payload in enumerate(env_payload(message) for message in client.requests[3][3::2]):
        prior = v10.apply_observation_delta(prior, payload["observation_delta"])
        assert prior == observations[position + 1]
    # the finalized history is the last sent request plus the closing delta turn
    final = policy.build_request()
    assert final[:len(client.requests[3])] == client.requests[3]
    assert [message["role"] for message in final] == [
        "system", "user", "assistant", "user", "assistant", "user", "assistant", "user", "assistant", "user",
    ]


def test_expected_episode_prompt_uses_v10_system_hash(monkeypatch):
    obs0 = observation(step=0, devices={"d1": {"on": False}})

    class Handle:
        public_observation = obs0

    class Backend:
        def reset(self, spec):
            return Handle()

    monkeypatch.setattr(
        v10.v8.v4, "load_temporal_episode",
        lambda index, release_dir: ({"query": V10_QUERY}, {}, None, Backend()),
    )
    prompt_hash, selected = v10.expected_episode_prompt(v10.SAMPLE_INDICES[0])
    expected_selected, _ = v10.v8.v4.select_event_types_for_query(V10_QUERY)
    assert selected == expected_selected
    assert prompt_hash == v10.v8.v6.system_prompt_sha256(
        v10.build_v10_system_content(
            V10_QUERY, v10.v8.v4.device_interfaces_from_observation(obs0), expected_selected
        )
    )
    assert prompt_hash != v10.v8.v6.system_prompt_sha256(
        v10.v8.v6.build_system_content(
            V10_QUERY, v10.v8.v4.device_interfaces_from_observation(obs0), expected_selected
        )
    )


def test_prompt_template_hash_binds_the_system_extension(monkeypatch):
    baseline = v10.prompt_template_sha256()
    monkeypatch.setattr(
        v10, "OBSERVATION_DELTA_INSTRUCTIONS", v10.OBSERVATION_DELTA_INSTRUCTIONS[:-1]
    )
    assert v10.prompt_template_sha256() != baseline


# ----------------------------------------------------------- token accounting


def run_policy_over_two(client):
    policy = v10.CompactDeltaObservationPolicy(client)
    policy.decide(view(obs=observation(step=0, devices={"d1": {"on": False}})))
    policy.decide(view(feedback={"accepted": True}, obs=observation(step=1, devices={"d1": {"on": True}})))
    return policy


def test_api_output_tokens_are_primary_and_provider_total_is_diagnostic():
    client = FakeClient([envelope(ACTION1), envelope(ACTION2)])
    policy = run_policy_over_two(client)
    assert policy.completion_tokens == policy.output_tokens == 4
    assert policy.prompt_tokens == 20
    assert policy.provider_total_tokens == 24
    assert policy.provider_total_tokens_present_calls == 2
    usage = policy.output_records[0]["usage"]
    assert usage["api_output_tokens"] == usage["completion_tokens"] == usage["output_tokens"] == 2
    assert usage["diagnostic_prompt_tokens"] == 10
    assert usage["diagnostic_provider_total_tokens"] == 12
    assert "total_tokens" not in usage


def test_missing_provider_total_is_not_synthesized():
    class MissingTotalClient(FakeClient):
        def complete(self, messages):
            self.requests.append(deepcopy(messages))
            return {"content": self.contents.pop(0), "usage": {"prompt_tokens": 10, "completion_tokens": 2}}

    policy = v10.CompactDeltaObservationPolicy(MissingTotalClient([envelope(ACTION1)]))
    policy.decide(view())
    assert policy.provider_total_tokens == 0
    assert policy.provider_total_tokens_present_calls == 0


def test_metrics_delegate_to_v8_with_api_output_primary():
    row = {
        "responsibility_success": True, "device_command_count": 1, "run_status": "completed",
        "api_successes": 1, "parsed_json_actions": 1, "rejected_action_count": 0, "calls": 1,
        "latency_ms": 3.0, "harness_action_records": 1, "accepted_harness_actions": 1,
        "protocol_error_count": 0, "backend_error_count": 0, "prompt_tokens": 10,
        "completion_tokens": 2, "output_tokens": 2, "provider_total_tokens": 12,
        "provider_total_tokens_present_calls": 1, "provider_total_tokens_mismatch_calls": 0,
    }
    metrics = v10.metrics_with_device_total([row])
    assert metrics["primary_token_metric"] == "api_output_tokens"
    assert metrics["api_output_tokens"] == 2
    assert "total_tokens" not in metrics


# ------------------------------------------------- v10 conversation validation


def build_valid_conversation():
    obs0 = observation(step=0, devices={"d1": {"on": False}})
    obs1 = observation(step=1, devices={"d1": {"on": True}}, events=[{"type": "device_done"}])
    obs2 = observation(step=2, devices={"d2": {"level": 3}}, rules=[7])
    obs3 = observation(step=3, devices={"d2": {"level": 3}}, rules=[7], extra={"terminal_reason": None})
    f1 = {"accepted": True}
    f2 = {"accepted": False, "error_code": "E"}
    f3 = {"accepted": True, "error_code": None}
    deltas = [v10.observation_delta(obs0, obs1), v10.observation_delta(obs1, obs2), v10.observation_delta(obs2, obs3)]
    conversation = [
        {"role": "system", "content": "rules"},
        v8.build_initial_user_message(view(obs=obs0)),
        v8.canonical_assistant_action(ACTION1),
        v10.build_environment_delta_message(f1, deltas[0]),
        v8.canonical_assistant_action(ACTION2),
        v10.build_environment_delta_message(f2, deltas[1]),
        v8.canonical_assistant_action(ACTION3),
        v10.build_environment_delta_message(f3, deltas[2]),
    ]
    return conversation, [obs0, obs1, obs2, obs3], [f1, f2, f3], deltas


def test_canonical_conversation_validator_accepts_three_action_delta_history():
    conversation, observations, feedbacks, _ = build_valid_conversation()
    actions = v10.validate_canonical_conversation(
        conversation, expected_query="Keep watching the home.", expected_episode_id="ep-test"
    )
    assert actions == [ACTION1, ACTION2, ACTION3]


def test_validator_rejects_any_post_initial_full_observation_snapshot():
    conversation, observations, feedbacks, _ = build_valid_conversation()
    broken = deepcopy(conversation)
    broken[3] = v8.build_environment_message(feedbacks[0], observations[1])
    with pytest.raises(RuntimeError, match="full observation snapshot"):
        v10.validate_canonical_conversation(broken)
    broken = deepcopy(conversation)
    payload = env_payload(broken[5])
    payload["current_observation"] = observations[2]
    broken[5]["content"] = v10.canonical_json(payload)
    with pytest.raises(RuntimeError, match="full observation snapshot"):
        v10.validate_canonical_conversation(broken)


def test_validator_rejects_wrong_message_type_fields_and_serialization():
    conversation, _, _, deltas = build_valid_conversation()

    broken = deepcopy(conversation)
    payload = env_payload(broken[3])
    payload["message_type"] = "environment_delta"
    broken[3]["content"] = v10.canonical_json(payload)
    with pytest.raises(RuntimeError, match="environment fields invalid"):
        v10.validate_canonical_conversation(broken)

    broken = deepcopy(conversation)
    payload = env_payload(broken[3])
    del payload["observation_delta"]
    broken[3]["content"] = v10.canonical_json(payload)
    with pytest.raises(RuntimeError, match="environment fields invalid"):
        v10.validate_canonical_conversation(broken)

    broken = deepcopy(conversation)
    payload = env_payload(broken[3])
    payload["observation_delta"] = {"op": "replace", "before": 0, "after": 1}
    broken[3]["content"] = v10.canonical_json(payload)
    with pytest.raises(RuntimeError, match="does not apply"):
        v10.validate_canonical_conversation(broken)

    broken = deepcopy(conversation)
    broken[5].update({"role": "tool"})
    with pytest.raises(RuntimeError, match="environment message invalid"):
        v10.validate_canonical_conversation(broken)


def test_validator_rejects_delta_chain_that_does_not_apply():
    conversation, observations, feedbacks, deltas = build_valid_conversation()

    # a replace whose recorded before no longer matches the reconstructed prior
    broken = deepcopy(conversation)
    payload = env_payload(broken[3])
    payload["observation_delta"]["changed"]["step"]["before"] = 99
    broken[3]["content"] = v10.canonical_json(payload)
    with pytest.raises(RuntimeError, match="before-value mismatch"):
        v10.validate_canonical_conversation(broken)

    # a non-op-tagged delta cannot be applied at all
    broken = deepcopy(conversation)
    payload = env_payload(broken[5])
    payload["observation_delta"] = {"step": 5}
    broken[5]["content"] = v10.canonical_json(payload)
    with pytest.raises(RuntimeError, match="does not apply"):
        v10.validate_canonical_conversation(broken)

    # a structurally valid but non-canonical delta breaks the next turn's chain
    broken = deepcopy(conversation)
    payload = env_payload(broken[3])
    payload["observation_delta"]["changed"]["step"]["after"] = 42
    broken[3]["content"] = v10.canonical_json(payload)
    with pytest.raises(RuntimeError, match="before-value mismatch"):
        v10.validate_canonical_conversation(broken)


@pytest.mark.parametrize("mutation", [
    lambda c: c.append({"role": "assistant", "content": "<answer>{}</answer>"}),
    lambda c: c[4].update({"content": "thinking <answer>{\"kind\":\"act\"}</answer>"}),
    lambda c: c.pop(1),
])
def test_canonical_conversation_validator_rejects_noncanonical_history(mutation):
    conversation, _, _, _ = build_valid_conversation()
    mutation(conversation)
    with pytest.raises(RuntimeError):
        v10.validate_canonical_conversation(conversation)


def test_validator_rejects_conversation_shape_fundamentals():
    conversation, _, _, _ = build_valid_conversation()
    with pytest.raises(RuntimeError, match="incomplete"):
        v10.validate_canonical_conversation([])
    with pytest.raises(RuntimeError, match="unpaired action"):
        v10.validate_canonical_conversation(deepcopy(conversation)[:-1])
    broken = deepcopy(conversation)
    broken[1] = {"role": "user", "content": "not json"}
    with pytest.raises(RuntimeError, match="initial request invalid"):
        v10.validate_canonical_conversation(broken)


# ------------------------------------------------------ report/checkpoint gate


def fake_result(index: int) -> dict:
    position = v10.SAMPLE_INDICES.index(index)
    return {"episode_id": v10.SAMPLE_EPISODE_IDS[position], "index": index}


def lightweight_report(results, *, execution_finished, requested_workers):
    return {
        "schema_version": v10.SCHEMA_VERSION,
        "execution_finished": execution_finished,
        "workers": requested_workers,
        "episodes": deepcopy(results),
    }


def patch_main(monkeypatch, *, evaluator, initial=None):
    monkeypatch.setattr(v10.v5, "assert_release_and_sample_frozen", lambda: ([], []))
    monkeypatch.setattr(v10, "load_checkpoint", lambda path, requested_workers: deepcopy(initial or []))
    monkeypatch.setattr(v10, "evaluate_index", evaluator)
    monkeypatch.setattr(v10, "build_report", lightweight_report)


def test_workers_default_30_and_effective_cap():
    assert v10.DEFAULT_WORKERS == 30
    assert len(v10.SAMPLE_INDICES) == 15
    assert v10.effective_worker_count(30, 15) == 15
    assert v10.effective_worker_count(30, 0) == 0
    with pytest.raises(Exception):
        v10.positive_int("0")


def test_actual_episode_parallelism(monkeypatch, tmp_path: Path):
    active = 0
    peak = 0
    lock = threading.Lock()
    barrier = threading.Barrier(len(v10.SAMPLE_INDICES))

    def evaluator(index, base_url, model):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        barrier.wait(timeout=3)
        with lock:
            active -= 1
        return fake_result(index)

    patch_main(monkeypatch, evaluator=evaluator)
    monkeypatch.setattr(v10, "atomic_write_report", lambda path, report: None)
    v10.main(["--workers", "30", "--output", str(tmp_path / "r.json")])
    assert peak == 15


def test_each_episode_owns_a_fresh_client_and_calls_v10_evaluate_one(monkeypatch):
    clients = []
    calls = []

    class Client:
        def __init__(self, base_url, model):
            self.serial = len(clients)
            clients.append(self)

    monkeypatch.setattr(v10, "ChatClient", Client)
    monkeypatch.setattr(v10, "evaluate_one", lambda client, index, release: calls.append((client, index)) or {})
    v10.evaluate_index(v10.SAMPLE_INDICES[0], v10.DEFAULT_BASE, v10.DEFAULT_MODEL)
    v10.evaluate_index(v10.SAMPLE_INDICES[1], v10.DEFAULT_BASE, v10.DEFAULT_MODEL)
    assert len(clients) == 2 and clients[0] is not clients[1]
    assert [client for client, _ in calls] == clients


def test_calls_within_one_episode_stay_serial(monkeypatch):
    active = 0
    peak = 0

    class Client:
        def __init__(self, base_url, model):
            pass

        def complete(self):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            time.sleep(0.001)
            active -= 1

    def sequential_episode(client, index, release):
        for _ in range(3):
            client.complete()
        return fake_result(index)

    monkeypatch.setattr(v10, "ChatClient", Client)
    monkeypatch.setattr(v10, "evaluate_one", sequential_episode)
    v10.evaluate_index(v10.SAMPLE_INDICES[0], v10.DEFAULT_BASE, v10.DEFAULT_MODEL)
    assert peak == 1


def test_out_of_order_futures_produce_frozen_final_order(monkeypatch, tmp_path: Path):
    def evaluator(index, base_url, model):
        position = v10.SAMPLE_INDICES.index(index)
        time.sleep((len(v10.SAMPLE_INDICES) - position) * 0.001)
        return fake_result(index)

    writes = []
    patch_main(monkeypatch, evaluator=evaluator)
    monkeypatch.setattr(v10, "atomic_write_report", lambda path, report: writes.append(deepcopy(report)))
    report = v10.main(["--workers", "15", "--output", str(tmp_path / "r.json")])
    assert [row["episode_id"] for row in report["episodes"]] == list(v10.SAMPLE_EPISODE_IDS)
    assert writes[-1]["execution_finished"] is True


def test_arbitrary_subset_checkpoint_resumes_only_missing(monkeypatch, tmp_path: Path):
    positions = [1, 5, 13]
    initial = [fake_result(v10.SAMPLE_INDICES[position]) for position in positions]
    executed = []

    def evaluator(index, base_url, model):
        executed.append(index)
        return fake_result(index)

    patch_main(monkeypatch, evaluator=evaluator, initial=initial)
    monkeypatch.setattr(v10, "atomic_write_report", lambda path, report: None)
    report = v10.main(["--output", str(tmp_path / "r.json")])
    assert set(executed) == set(v10.SAMPLE_INDICES) - {v10.SAMPLE_INDICES[p] for p in positions}
    assert [row["episode_id"] for row in report["episodes"]] == list(v10.SAMPLE_EPISODE_IDS)


def test_partial_api_failure_keeps_completed_results_unfinished(monkeypatch, tmp_path: Path):
    failed_index = v10.SAMPLE_INDICES[3]
    writes = []

    def evaluator(index, base_url, model):
        if index == failed_index:
            raise v10.APIError("connection_reset")
        return fake_result(index)

    patch_main(monkeypatch, evaluator=evaluator)
    monkeypatch.setattr(v10, "atomic_write_report", lambda path, report: writes.append(deepcopy(report)))
    with pytest.raises(v10.APIError, match="connection_reset"):
        v10.main(["--workers", "15", "--output", str(tmp_path / "r.json")])
    final_checkpoint = writes[-1]
    assert final_checkpoint["execution_finished"] is False
    assert len(final_checkpoint["episodes"]) == 14
    assert v10.SAMPLE_EPISODE_IDS[3] not in {row["episode_id"] for row in final_checkpoint["episodes"]}


def test_subset_validator_rejects_duplicate_unknown_and_noncanonical_order(monkeypatch):
    monkeypatch.setattr(v10.v5, "assert_release_and_sample_frozen", lambda: ([], []))
    monkeypatch.setattr(v10, "_validate_episode_row", lambda row, position, public_rows: None)
    first = {"episode_id": v10.SAMPLE_EPISODE_IDS[0]}
    second = {"episode_id": v10.SAMPLE_EPISODE_IDS[1]}
    with pytest.raises(RuntimeError, match="duplicate"):
        v10.validate_result_subset([first, first])
    with pytest.raises(RuntimeError, match="unknown"):
        v10.validate_result_subset([{"episode_id": "unknown"}])
    with pytest.raises(RuntimeError, match="order"):
        v10.validate_result_subset([second, first])


def test_load_checkpoint_refuses_completed_report(tmp_path: Path):
    path = tmp_path / "done.json"
    path.write_text(json.dumps({"schema_version": v10.SCHEMA_VERSION, "execution_finished": True}))
    with pytest.raises(RuntimeError, match="already completed"):
        v10.load_checkpoint(path, requested_workers=30)


def test_load_checkpoint_rejects_metadata_tampering(monkeypatch, tmp_path: Path):
    path = tmp_path / "checkpoint.json"
    payload = lightweight_report([], execution_finished=False, requested_workers=30)
    payload["workers"] = 29
    path.write_text(json.dumps(payload))
    monkeypatch.setattr(v10, "build_report", lightweight_report)
    with pytest.raises(RuntimeError, match="metadata"):
        v10.load_checkpoint(path, requested_workers=30)


def test_v10_report_metadata_describes_append_only_delta_history(monkeypatch):
    monkeypatch.setattr(v10, "validate_result_subset", lambda results: None)
    monkeypatch.setattr(v10, "metrics_with_device_total", lambda results: {
        "api_output_tokens": 7, "completion_tokens": 7, "output_tokens": 7,
        "primary_token_metric": "api_output_tokens",
        "token_diagnostics": {"prompt_tokens": 100, "provider_total_tokens": 107},
    })
    report = v10.build_report([], execution_finished=False, requested_workers=30)
    config = report["experiment_config"]
    assert report["schema_version"] == v10.SCHEMA_VERSION
    assert "v10" in report["schema_version"]
    assert config["prompt_implementation"] == "evaluate_workflow_v4_flash_v10"
    assert config["primary_token_metric"] == "api_output_tokens"
    assert config["token_accounting"]["prompt_and_provider_total_location"] == "metrics_all.token_diagnostics"
    assert config["concurrency"]["unit"] == "Episode"
    assert config["concurrency"]["requested_workers"] == 30
    assert config["native_tools"] == "not_used"
    assert config["response_envelope_protocol"]["format"].startswith("<answer>")
    assert "append-only" in config["conversation_policy"]
    observation_protocol = config["observation_protocol"]
    assert "initial user message only" in observation_protocol["initial_observation"]
    assert "observation_delta" in observation_protocol["environment_turns"]
    assert "ever appended, sent, or persisted" in observation_protocol["full_snapshot_policy"]
    assert "process memory only" in observation_protocol["latest_full_observation"]
    assert "apply_observation_delta" in observation_protocol["delta_apply"]
    assert "never conflated" in observation_protocol["missing_vs_null"]
    assert "observation_delta_protocol" in observation_protocol["system_instructions"]
    assert "observation_delta_protocol" in v10.MIGRATION_MANIFEST["system_prompt_extension"]
    assert "total_tokens" not in report["metrics_all"]


def test_v10_identities_are_v10_specific():
    assert "v10" in str(v10.DEFAULT_OUTPUT)
    assert "v9" not in str(v10.DEFAULT_OUTPUT)
    assert v10.prompt_template_sha256() == v10.prompt_template_sha256()
    assert v10.prompt_template_sha256() != v8.prompt_template_sha256()
    assert v10.SAMPLE_INDICES is v8.SAMPLE_INDICES
    assert v10.DEFAULT_WORKERS == v9.DEFAULT_WORKERS == 30
    assert v10.MIGRATION_MANIFEST["native_tools"] == "not sent and not required"
    assert "api_output_tokens" in v10.MIGRATION_MANIFEST["token_metric"]
    assert "never rebuilt" in v10.MIGRATION_MANIFEST["conversation"]
    assert "observation_delta" in v10.MIGRATION_MANIFEST["environment_turns"]
    assert "never appended, sent, or persisted again" in v10.MIGRATION_MANIFEST["full_snapshots"]
    assert set(v10.DELTA_OPS) == {"none", "replace", "object", "array"}


# ------------------------------------------------------------- v8/v9 regression


def test_v8_and_v9_semantics_are_untouched_by_v10():
    # v8 still builds full-snapshot environment turns; v10 never reuses them
    message = v8.build_environment_message({"ok": True}, {"step": 1})
    payload = json.loads(message["content"])
    assert payload["message_type"] == "environment_observation"
    assert set(payload) == {"message_type", "action_result", "current_observation"}
    assert v9.DEFAULT_OUTPUT != v10.DEFAULT_OUTPUT
    assert v9.SCHEMA_VERSION != v10.SCHEMA_VERSION
    assert v8.SCHEMA_VERSION != v10.SCHEMA_VERSION
    assert v9.positive_int("5") == 5 and v8.response_fingerprint("abc")[0] == 3
