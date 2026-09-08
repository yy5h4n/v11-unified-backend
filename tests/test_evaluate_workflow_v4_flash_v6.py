from __future__ import annotations

import json
from pathlib import Path

import pytest

import evaluate_workflow_v4_flash_v4 as v4
import evaluate_workflow_v4_flash_v5 as v5
import evaluate_workflow_v4_flash_v6 as v6
from evaluate_harness_v2_v4_flash import DEFAULT_MODEL


def result(episode_id: str, *, calls: int = 1, prompt_tokens: int = 100, completion_tokens: int = 20) -> dict:
    position = v6.SAMPLE_EPISODE_IDS.index(episode_id)
    public_rows, _ = v6.v5.assert_release_and_sample_frozen()
    prompt_hash, selected = v6.expected_episode_prompt(v6.SAMPLE_INDICES[position])
    total = prompt_tokens + completion_tokens
    return {
        "episode_id": episode_id,
        "query": public_rows[position]["query"],
        "model": DEFAULT_MODEL,
        "responsibility_success": False,
        "run_status": "completed",
        "device_command_count": 2,
        "count_unit": "device_command",
        "calls": calls,
        "api_successes": calls,
        "parsed_json_actions": calls,
        "rejected_action_count": 0,
        "prompt_tokens": prompt_tokens * calls,
        "completion_tokens": completion_tokens * calls,
        "total_tokens": total * calls,
        "provider_total_tokens_present_calls": calls,
        "provider_total_tokens_mismatch_calls": 0,
        "latency_ms": 1.0,
        "harness_action_records": calls,
        "accepted_harness_actions": calls,
        "protocol_error_count": 0,
        "backend_error_count": 0,
        "max_consecutive_identical_rejected_action": 0,
        "system_prompt_sha256": prompt_hash,
        "selected_event_types": list(selected),
        "errors": {},
    }


def test_frozen_release_and_sample_are_valid() -> None:
    public_rows, private_rows = v5.assert_release_and_sample_frozen()
    assert len(public_rows) == len(private_rows) == 15
    assert [row["episode_id"] for row in public_rows] == v6.SAMPLE_EPISODE_IDS
    assert v6.SAMPLE_INDICES == v5.SAMPLE_INDICES
    assert len({row["responsibility_id"] for row in private_rows}) == 15
    assert not any(row["contract"]["required_actions"] == ["notification.send"] for row in private_rows)


def test_v6_prompt_teaches_answer_envelope_and_is_episode_static() -> None:
    assert "<answer>" in v6.V6_SYSTEM_PROMPT and "</answer>" in v6.V6_SYSTEM_PROMPT
    assert all(item.startswith("<answer>") and item.endswith("</answer>") for item in v6.V6_GRAMMAR)
    assert all(value.startswith("<answer>{") and value.endswith("}</answer>") for value in v6.V6_ACTION_EXAMPLES.values())
    assert any("envelope" in rule for rule in v6.V6_PROTOCOL_RULES)
    prompt_hash, selected = v6.expected_episode_prompt(v6.SAMPLE_INDICES[0])
    assert prompt_hash != v5.expected_episode_prompt(v6.SAMPLE_INDICES[0])[0]
    assert v6.prompt_template_sha256() != v4.prompt_template_sha256()
    lowered = json.dumps(json.loads(_system_content(v6.SAMPLE_INDICES[0])).get("response_envelope")).lower()
    assert "nested" in lowered and "malformed" in lowered


def _system_content(index: int) -> str:
    public, _, spec, backend = v4.load_temporal_episode(index, v6.RELEASE_DIR)
    observation = backend.reset(spec).public_observation
    selected, _ = v4.select_event_types_for_query(public["query"])
    return v6.build_system_content(
        public["query"], v4.device_interfaces_from_observation(observation), selected
    )


def test_private_fields_never_enter_v6_prompt() -> None:
    system = _system_content(v6.SAMPLE_INDICES[2]).lower()
    for banned in ("scenario_type", "required_actions", "reference_policy", "episodes_private", "contract"):
        assert banned not in system


@pytest.mark.parametrize(
    ("content", "expected_action", "expected_error"),
    [
        ('<answer>{"kind":"wait","mode":"for","duration_seconds":300}</answer>', {"kind": "wait", "mode": "for", "duration_seconds": 300}, None),
        ('Let me think... <answer>{"kind":"wait","mode":"for","duration_seconds":300}</answer> done.', {"kind": "wait", "mode": "for", "duration_seconds": 300}, None),
        ('no envelope at all', None, "missing_answer_envelope"),
        ('<answer>{"kind":"wait"}</answer> and <answer>{"kind":"wait"}</answer>', None, "multiple_answer_envelopes"),
        ('<answer><answer>{"kind":"wait"}</answer></answer>', None, "multiple_answer_envelopes"),
        ('<answer>{"kind":"wait"}', None, "malformed_answer_envelope"),
        ('</answer>{"kind":"wait"}<answer>', None, "malformed_answer_envelope"),
        ('<answer>   </answer>', None, "empty_answer_envelope"),
        ('<answer></>answer>', None, "malformed_answer_envelope"),
        ('<answer>{"kind":"wait","kind":"act"}</answer>', None, "duplicate_json_key"),
        ('<answer>{"kind":"wait","duration_seconds":NaN}</answer>', None, "nonfinite_json_constant"),
        ('<answer>[1,2]</answer>', None, "response_not_plain_json_object"),
        (12345, None, "response_not_text"),
    ],
)
def test_parse_answer_envelope(content, expected_action, expected_error) -> None:
    if expected_error is None:
        assert v6.parse_answer_envelope(content) == expected_action
    else:
        with pytest.raises(ValueError, match=expected_error):
            v6.parse_answer_envelope(content)


def test_markdown_and_code_fences_outside_envelope_are_ignored_and_accepted() -> None:
    action = {"kind": "wait", "mode": "for", "duration_seconds": 300}
    payload = json.dumps(action, separators=(",", ":"))
    for content in (
        f"```json\n<answer>{payload}</answer>\n```",
        f"Reasoning:\n```\ncode fence here\n```\n<answer>{payload}</answer>\nDone.",
        f"**bold markdown**\n\n<answer>{payload}</answer>",
    ):
        assert v6.parse_answer_envelope(content) == action


def test_code_fence_inside_envelope_is_rejected_by_strict_json_parsing() -> None:
    with pytest.raises(ValueError, match="response_not_plain_json_object"):
        v6.parse_answer_envelope('<answer>```json\n{"kind":"wait"}\n```</answer>')
    with pytest.raises(ValueError, match="response_not_plain_json_object"):
        v6.parse_answer_envelope('<answer>`{"kind":"wait"}`</answer>')


class FakeClient:
    model = DEFAULT_MODEL

    def __init__(self, responses: list[dict]):
        self.responses = list(responses)

    def complete(self, messages: list[dict[str, str]]) -> dict:
        return self.responses.pop(0)


def fake_view() -> dict:
    return {
        "query": "Notify me when the laundry cycle finishes.",
        "public_profile": {"name": "resident"},
        "observation": {
            "step": 0,
            "tick_seconds": 60,
            "inventory": {"devices": [{
                "device_id": "notification.service",
                "device_type": "notification",
                "capabilities": ["notification.send"],
                "interfaces": {},
            }]},
        },
        "last_feedback": None,
    }


def test_policy_records_split_tokens_and_rejects_bad_envelope() -> None:
    client = FakeClient([
        {
            "content": 'reasoning <answer>{"kind":"wait","mode":"for","duration_seconds":300}</answer>',
            "usage": {"prompt_tokens": 500, "completion_tokens": 25, "total_tokens": 525},
            "latency_ms": 10.0,
        },
        {"content": "oops no envelope", "usage": {"prompt_tokens": 600, "completion_tokens": 8}, "latency_ms": 5.0},
    ])
    policy = v6.EnvelopeWorkflowLLMPolicy(client)
    action = policy.decide(fake_view())
    assert action == {"kind": "wait", "mode": "for", "duration_seconds": 300}
    bad = policy.decide(fake_view())
    assert bad == {"kind": "invalid_model_output"}
    assert policy.prompt_tokens == 1100
    assert policy.completion_tokens == 33
    assert policy.total_tokens == 525
    assert policy.provider_total_tokens_present_calls == 1
    assert policy.provider_total_tokens_mismatch_calls == 0
    assert policy.errors and "missing_answer_envelope" in next(iter(policy.errors))
    usage = policy.output_records[0]["usage"]
    assert usage["prompt_tokens"] == 500
    assert usage["completion_tokens"] == 25
    assert usage["total_tokens"] == 525
    assert usage["provider_total_tokens_present"] is True
    assert usage["provider_total_tokens_matches_sum"] is True
    bad_usage = policy.output_records[1]["usage"]
    assert bad_usage["prompt_tokens"] == 600
    assert bad_usage["completion_tokens"] == 8
    assert bad_usage["total_tokens"] == 0
    assert bad_usage["provider_total_tokens_present"] is False
    assert bad_usage["provider_total_tokens_matches_sum"] is None
    assert policy.output_records[1]["error"].endswith("missing_answer_envelope")


def test_missing_provider_total_stays_zero_and_never_synthesized() -> None:
    client = FakeClient([
        {
            "content": '<answer>{"kind":"wait","mode":"for","duration_seconds":300}</answer>',
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            "latency_ms": 1.0,
        },
        {
            "content": '<answer>{"kind":"wait","mode":"for","duration_seconds":300}</answer>',
            "usage": None,
            "latency_ms": 1.0,
        },
    ])
    policy = v6.EnvelopeWorkflowLLMPolicy(client)
    policy.decide(fake_view())
    policy.decide(fake_view())
    assert policy.prompt_tokens == 10
    assert policy.completion_tokens == 5
    assert policy.total_tokens == 0
    assert policy.provider_total_tokens_present_calls == 0
    assert policy.provider_total_tokens_mismatch_calls == 0
    first = policy.output_records[0]["usage"]
    assert first["total_tokens"] == 0
    assert first["provider_total_tokens_present"] is False
    assert first["provider_total_tokens_matches_sum"] is None
    second = policy.output_records[1]["usage"]
    assert second["total_tokens"] == 0
    assert second["provider_total_tokens_present"] is False


def test_mismatched_provider_total_is_retained_exactly_and_counted() -> None:
    client = FakeClient([
        {
            "content": '<answer>{"kind":"wait","mode":"for","duration_seconds":300}</answer>',
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 999},
            "latency_ms": 1.0,
        },
        {
            "content": '<answer>{"kind":"wait","mode":"for","duration_seconds":300}</answer>',
            "usage": {"prompt_tokens": 50, "total_tokens": 77},
            "latency_ms": 1.0,
        },
    ])
    policy = v6.EnvelopeWorkflowLLMPolicy(client)
    policy.decide(fake_view())
    policy.decide(fake_view())
    assert policy.total_tokens == 999 + 77
    assert policy.provider_total_tokens_present_calls == 2
    assert policy.provider_total_tokens_mismatch_calls == 1
    first = policy.output_records[0]["usage"]
    assert first["total_tokens"] == 999
    assert first["provider_total_tokens_matches_sum"] is False
    second = policy.output_records[1]["usage"]
    assert second["completion_tokens"] == 0
    assert second["total_tokens"] == 77
    assert second["provider_total_tokens_matches_sum"] is None


def test_report_records_split_token_metrics_per_episode_and_aggregate() -> None:
    rows = [result(v6.SAMPLE_EPISODE_IDS[0], calls=2, prompt_tokens=100, completion_tokens=20)]
    report = v6.build_report(rows, execution_finished=False)
    episode = report["episodes"][0]
    assert episode["prompt_tokens"] == 200
    assert episode["completion_tokens"] == 40
    assert episode["total_tokens"] == 240
    metrics = report["metrics_all"]
    assert metrics["prompt_tokens"] == 200
    assert metrics["completion_tokens"] == 40
    assert metrics["total_tokens"] == 240
    assert metrics["provider_total_tokens_present_calls"] == 2
    assert metrics["provider_total_tokens_mismatch_calls"] == 0
    assert metrics["output_tokens"] == metrics["completion_tokens"] == 40
    assert metrics["output_token_metric"] == "completion_tokens"
    assert report["experiment_config"]["output_token_metric"] == "completion_tokens"
    assert report["experiment_config"]["token_accounting"]["provider_total_synthesized"] is False
    assert report["schema_version"] == v6.SCHEMA_VERSION


def test_generic_tokens_field_never_reaches_report_and_counters_aggregate() -> None:
    rows = [result(episode_id) for episode_id in v6.SAMPLE_EPISODE_IDS[:3]]
    rows[1]["provider_total_tokens_mismatch_calls"] = 1
    rows[2]["provider_total_tokens_present_calls"] = 0
    report = v6.build_report(rows, execution_finished=False)
    for episode in report["episodes"]:
        assert "tokens" not in episode
    metrics = report["metrics_all"]
    assert "tokens" not in metrics
    assert metrics["total_tokens"] == sum(row["total_tokens"] for row in rows)
    assert metrics["provider_total_tokens_present_calls"] == 2
    assert metrics["provider_total_tokens_mismatch_calls"] == 1


def test_checkpoint_prefix_and_completed_report_protection(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    rows = [result(v6.SAMPLE_EPISODE_IDS[0])]
    partial = v6.build_report(rows, execution_finished=False)
    v6.atomic_write_report(path, partial)
    assert not path.with_suffix(".json.tmp").exists()
    assert len(v6.load_checkpoint(path)) == 1
    with pytest.raises(RuntimeError, match="all 15"):
        v6.build_report(rows, execution_finished=True)
    finished = v6.build_report([result(episode_id) for episode_id in v6.SAMPLE_EPISODE_IDS], execution_finished=True)
    v6.atomic_write_report(path, finished)
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        v6.load_checkpoint(path)


def test_tampered_checkpoint_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    partial = v6.build_report([result(v6.SAMPLE_EPISODE_IDS[0])], execution_finished=False)
    partial["metrics_all"]["completion_tokens"] = 1
    v6.atomic_write_report(path, partial)
    with pytest.raises(RuntimeError, match="metadata or metrics mismatch"):
        v6.load_checkpoint(path)
    partial = v6.build_report([result(v6.SAMPLE_EPISODE_IDS[0])], execution_finished=False)
    partial["episodes"][0]["system_prompt_sha256"] = "0" * 64
    v6.atomic_write_report(path, partial)
    with pytest.raises(RuntimeError, match="system prompt SHA256 mismatch"):
        v6.load_checkpoint(path)


def test_v6_checkpoint_rejects_v5_schema(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    path.write_text(json.dumps({"schema_version": v5.SCHEMA_VERSION if hasattr(v5, "SCHEMA_VERSION") else "x"}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="not a v6 smoke checkpoint"):
        v6.load_checkpoint(path)
