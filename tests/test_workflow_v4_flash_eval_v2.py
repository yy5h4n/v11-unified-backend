import json
from copy import deepcopy

from evaluate_harness_v2_v4_flash import _parse_strict_json
import evaluate_workflow_v4_flash as v1
from evaluate_workflow_v4_flash_v2 import (
    ACTION_EXAMPLES,
    BASELINE_REPORT_PATH,
    BASELINE_REPORT_SHA256,
    FIXED_INDICES,
    GRAMMAR,
    PROTOCOL_RULES,
    REQUEST_BODY_FIELDS,
    SYSTEM_PROMPT,
    action_protocol,
    aggregate_results,
    assert_v1_baseline_frozen,
    build_messages,
    build_report,
    evaluate_one,
    load_episode,
    paired_outcomes,
    prompt_template_sha256,
)
from harness_v2.core import AGENT_ACTIONS, validate_agent_action


class FakeClient:
    model = "fake-v4"

    def __init__(self, content):
        self.content = content

    def complete(self, messages):
        return {"content": self.content, "usage": {"total_tokens": 5}, "latency_ms": 1.0}


def _row(**overrides):
    row = {
        "episode_id": "wf_x",
        "model": "fake-v4",
        "run_status": "completed",
        "responsibility_success": False,
        "action_cost": 0,
        "calls": 1,
        "api_successes": 1,
        "parsed_json_actions": 1,
        "harness_action_records": 1,
        "accepted_harness_actions": 1,
        "rejected_action_count": 0,
        "all_actions_accepted": True,
        "errors": {},
        "public_action_records": [{"type": "action", "accepted": True}],
        "protocol_error_count": 0,
        "backend_error_count": 0,
        "tokens": 1,
        "latency_ms": 1.0,
    }
    row.update(overrides)
    return row


def _fresh_view():
    _, _, spec, backend = load_episode(0)
    initial = backend.reset(spec).public_observation
    return {**spec.public_bootstrap, "observation": initial, "last_feedback": None, "allowed_actions": spec.public_bootstrap["allowed_action_kinds"]}


def test_prompt_forbids_wrappers_and_demands_one_next_action_with_reobservation():
    assert '"action", "action_protocol", or "response"' in SYSTEM_PROMPT
    assert "forbidden" in SYSTEM_PROMPT
    assert any('"action", "action_protocol", or "response"' in rule and "forbidden" in rule for rule in PROTOCOL_RULES)
    assert "exactly one" in SYSTEM_PROMPT and "next action" in SYSTEM_PROMPT
    assert "re-observe" in SYSTEM_PROMPT and any("re-observe" in rule for rule in PROTOCOL_RULES)
    assert "Think silently" in SYSTEM_PROMPT
    assert SYSTEM_PROMPT.endswith("re-observe before deciding the next step.")
    assert len(prompt_template_sha256()) == 64
    assert prompt_template_sha256() != v1.prompt_template_sha256()


def test_grammar_is_first_and_payload_places_protocol_before_inventory():
    assert all(f'"kind":"{kind}"' in line for kind, line in zip(["act", "wait", "wait", "wait", "ask", "cancel_rule", "install_rule"], GRAMMAR))
    for kind in ("act", "wait", "ask", "cancel_rule", "install_rule"):
        assert any(f'"kind":"{kind}"' in line for line in GRAMMAR)
    view = _fresh_view()
    protocol_wire = json.dumps(action_protocol(view))
    assert protocol_wire.index("grammar") < protocol_wire.index("device_interfaces")
    user_content = build_messages(view)[1]["content"]
    assert user_content.index('"action_protocol"') < user_content.index('"query"') < user_content.index('"current_observation"')
    assert '"grammar"' not in v1.build_messages(view)[1]["content"]


def test_examples_are_strict_plain_json_and_harness_valid():
    for name, example in ACTION_EXAMPLES.items():
        wire = json.dumps(example, ensure_ascii=False)
        assert _parse_strict_json(wire) == example
        validate_agent_action(deepcopy(example))
        assert example["kind"] in AGENT_ACTIONS


def test_examples_execute_on_frozen_workflow_backend():
    _, _, spec, backend = load_episode(0)
    backend.reset(spec)
    for name in ("act", "ask", "wait_for", "wait_until", "wait_until_event"):
        assert backend.execute_atomic(deepcopy(ACTION_EXAMPLES[name])).accepted is True
    install = deepcopy(ACTION_EXAMPLES["install_rule"])
    assert backend.execute_atomic(install).accepted is True
    cancel = deepcopy(ACTION_EXAMPLES["cancel_rule"])
    assert backend.execute_atomic(cancel).accepted is True


def test_install_rule_examples_avoid_stale_fixed_fire_at_step():
    assert any("fire_at_step" in rule and "current observation step" in rule and "greater than" in rule for rule in PROTOCOL_RULES)
    example = ACTION_EXAMPLES["install_rule"]["rule"]
    assert example["fire_at_step"] == 1
    assert not any("observation step is 30" in rule or "fire_at_step 45" in rule for rule in PROTOCOL_RULES)
    _, _, spec, backend = load_episode(0)
    initial = backend.reset(spec).public_observation
    view = {**spec.public_bootstrap, "observation": initial, "last_feedback": None, "allowed_actions": spec.public_bootstrap["allowed_action_kinds"]}
    dynamic = action_protocol(view)["examples"]["install_rule"]
    assert dynamic["rule"]["fire_at_step"] == initial["step"] + 1
    assert backend.execute_atomic(deepcopy(dynamic)).accepted is True
    assert "cancel_rule" not in action_protocol(view)["examples"]
    later_view = deepcopy(view)
    later_view["observation"]["step"] = 12
    later_view["observation"]["time"] = "2026-01-01T18:12:00Z"
    later_view["observation"]["active_rule_ids"] = [dynamic["rule"]["rule_id"]]
    later = action_protocol(later_view)["examples"]
    assert later["install_rule"]["rule"]["fire_at_step"] == 13
    assert later["install_rule"]["rule"]["rule_id"] != dynamic["rule"]["rule_id"]
    assert later["cancel_rule"]["rule_id"] == dynamic["rule"]["rule_id"]
    assert later["wait_until"]["timestamp"] == "2026-01-01T18:17:00Z"
    stale = deepcopy(ACTION_EXAMPLES["install_rule"])
    stale["rule"]["rule_id"] = "rule.stale"
    stale["rule"]["fire_at_step"] = 0
    outcome = backend.execute_atomic(stale)
    assert outcome.accepted is False
    assert outcome.error_code == "INVALID_RULE"


def test_frozen_interface_transport_and_release_stack_are_reused():
    assert REQUEST_BODY_FIELDS == v1.REQUEST_BODY_FIELDS
    assert "response_format" not in REQUEST_BODY_FIELDS
    assert "tools" not in REQUEST_BODY_FIELDS
    import evaluate_harness_v2_v4_flash as api
    import evaluate_workflow_v4_flash_v2 as v2
    assert v2.ChatClient is api.ChatClient
    assert v2.DEFAULT_BASE == api.DEFAULT_BASE
    assert v2.DEFAULT_MODEL == api.DEFAULT_MODEL
    assert v2.load_episode is v1.load_episode
    assert v2.smoke_accepted is v1.smoke_accepted
    assert v2.DEFAULT_RELEASE == v1.DEFAULT_RELEASE


def test_fixed_indices_and_baseline_frozen_assertions():
    assert FIXED_INDICES == [offset * 10 for offset in range(20)]
    assert len(FIXED_INDICES) == 20 and FIXED_INDICES[0] == 0 and FIXED_INDICES[-1] == 190
    baseline = assert_v1_baseline_frozen()
    assert [row["episode_id"] for row in baseline["episodes"]] == [
        load_episode(index)[0]["episode_id"] for index in FIXED_INDICES
    ]
    assert baseline["experiment_config"]["sample_indices"] == FIXED_INDICES
    assert BASELINE_REPORT_SHA256 == "74a10ddb0865fc0146ee2e2c99a6be15a9e36932dbf3ad33a6b81692a38e017a"
    assert BASELINE_REPORT_PATH.name == "report.json"


def test_rate_denominator_edge_cases():
    empty = aggregate_results([])
    assert empty["protocol_valid_action_rate"] is None
    assert empty["backend_accepted_action_rate"] is None
    assert empty["harness_action_total"] == 0
    zero_calls = aggregate_results([_row(calls=0, api_successes=0, parsed_json_actions=0, harness_action_records=0, accepted_harness_actions=0)])
    assert zero_calls["protocol_valid_action_rate"] is None
    assert zero_calls["backend_accepted_action_rate"] is None
    no_harness_actions = aggregate_results([_row(calls=2, parsed_json_actions=2, harness_action_records=0, accepted_harness_actions=0)])
    assert no_harness_actions["protocol_valid_action_rate"] == 0.0
    assert no_harness_actions["backend_accepted_action_rate"] is None
    mixed = aggregate_results([_row(calls=2, harness_action_records=1, accepted_harness_actions=1)])
    assert mixed["protocol_valid_action_rate"] == 0.5
    assert mixed["backend_accepted_action_rate"] == 1.0


def test_episode_pairing_gained_lost_tied_by_episode_id():
    baseline = [
        {"episode_id": "a", "responsibility_success": False, "run_status": "protocol_invalid"},
        {"episode_id": "b", "responsibility_success": True, "run_status": "completed"},
        {"episode_id": "c", "responsibility_success": True, "run_status": "completed"},
        {"episode_id": "d", "responsibility_success": False, "run_status": "protocol_invalid"},
    ]
    current = [
        _row(episode_id="a", responsibility_success=True, run_status="completed"),
        _row(episode_id="b", responsibility_success=False, run_status="protocol_invalid"),
        _row(episode_id="c", responsibility_success=True, run_status="completed"),
        _row(episode_id="d", responsibility_success=False, run_status="protocol_invalid"),
    ]
    paired = paired_outcomes(baseline, current)
    for name in ("responsibility_success", "run_completed"):
        assert paired[name]["gained"] == ["a"]
        assert paired[name]["lost"] == ["b"]
        assert paired[name]["tied"] == ["c", "d"]
        assert paired[name]["paired_episode_count"] == 4
        assert (paired[name]["gained_count"], paired[name]["lost_count"], paired[name]["tied_count"]) == (1, 1, 2)


def test_pairing_fails_closed_on_missing_extra_or_duplicate_ids():
    import pytest
    baseline = [
        {"episode_id": "a", "responsibility_success": False, "run_status": "protocol_invalid"},
        {"episode_id": "b", "responsibility_success": False, "run_status": "protocol_invalid"},
    ]
    with pytest.raises(RuntimeError):
        paired_outcomes(baseline, [_row(episode_id="b")])
    with pytest.raises(RuntimeError):
        paired_outcomes(baseline, [_row(episode_id="a"), _row(episode_id="x")])
    with pytest.raises(RuntimeError):
        paired_outcomes(baseline, [_row(episode_id="a"), _row(episode_id="a")])


def test_v1_style_wrapper_output_still_fails_closed_without_repair():
    wrapped = json.dumps({"action_protocol": ACTION_EXAMPLES["wait_for"]})
    result = evaluate_one(FakeClient(wrapped), 0)
    assert result["run_status"] == "protocol_invalid"
    assert result["parsed_json_actions"] == 1
    assert result["harness_action_records"] == 0
    assert result["responsibility_success"] is False


def test_valid_long_wait_completes_v2():
    result = evaluate_one(FakeClient('{"kind":"wait","mode":"for","duration_seconds":3600}'), 0)
    assert result["run_status"] == "completed"
    assert result["calls"] >= 1
    assert result["harness_action_records"] == result["calls"]
    assert result["accepted_harness_actions"] == result["calls"]
    assert result["responsibility_success"] is False


def test_build_report_freezes_config_and_pairs_against_baseline():
    baseline = {
        "experiment_config": {"sample_indices": FIXED_INDICES},
        "episodes": [
            {"episode_id": "a", "responsibility_success": False, "run_status": "protocol_invalid"},
            {"episode_id": "b", "responsibility_success": True, "run_status": "completed"},
        ],
    }
    results = [
        _row(episode_id="a", responsibility_success=True, harness_action_records=1, accepted_harness_actions=1),
        _row(episode_id="b", responsibility_success=False, run_status="protocol_invalid", harness_action_records=0, accepted_harness_actions=0),
    ]
    report = build_report(results, [0, 10], execution_finished=False, baseline=baseline)
    assert report["schema_version"] == "workflow-v4-flash-complete-prompt-v2"
    config = report["experiment_config"]
    assert config["structured_output_enabled"] is False
    assert config["base_url"] == "https://aigc.sankuai.com/v1/openai/native"
    assert config["transport_retries"] == 2
    assert config["model_protocol_failure_retries"] == 0
    assert config["baseline_report_sha256"] == BASELINE_REPORT_SHA256
    assert config["prompt_template_sha256"] == prompt_template_sha256()
    assert report["paired_vs_v1"]["responsibility_success"]["gained"] == ["a"]
    assert report["paired_vs_v1"]["responsibility_success"]["lost"] == ["b"]
    assert report["metrics_all"]["protocol_valid_action_rate"] == 0.5
    assert report["metrics_all"]["backend_accepted_action_rate"] == 1.0
    assert report["metrics_all"]["responsibility_success_rate"] == 0.5
    assert "metrics_held_out_after_prompt_tuning" not in report
