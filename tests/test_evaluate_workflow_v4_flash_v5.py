from __future__ import annotations

import json
from pathlib import Path

import pytest

import evaluate_workflow_v4_flash_v4 as v4
import evaluate_workflow_v4_flash_v5 as v5


def result(episode_id: str, *, calls: int = 1, tokens: int = 10) -> dict:
    position = v5.SAMPLE_EPISODE_IDS.index(episode_id)
    public_rows, _ = v5.assert_release_and_sample_frozen()
    prompt_hash, selected = v5.expected_episode_prompt(v5.SAMPLE_INDICES[position])
    return {
        "episode_id": episode_id,
        "query": public_rows[position]["query"],
        "model": v5.DEFAULT_MODEL,
        "responsibility_success": False,
        "run_status": "completed",
        "device_command_count": 2,
        "count_unit": "device_command",
        "calls": calls,
        "api_successes": calls,
        "parsed_json_actions": calls,
        "rejected_action_count": 0,
        "tokens": tokens,
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
    assert [row["episode_id"] for row in public_rows] == v5.SAMPLE_EPISODE_IDS
    assert len({row["responsibility_id"] for row in private_rows}) == 15
    assert not any(row["contract"]["required_actions"] == ["notification.send"] for row in private_rows)
    assert v4.prompt_template_sha256() == v5.V4_PROMPT_TEMPLATE_SHA256


def test_report_records_planned_and_completed_prefix() -> None:
    rows = [result(v5.SAMPLE_EPISODE_IDS[0]), result(v5.SAMPLE_EPISODE_IDS[1])]
    report = v5.build_report(rows, execution_finished=False)
    assert report["experiment_config"]["planned_indices"] == v5.SAMPLE_INDICES
    assert report["experiment_config"]["completed_indices"] == v5.SAMPLE_INDICES[:2]
    assert report["metrics_all"]["total_device_command_count"] == 4
    assert report["experiment_config"]["baseline_comparison"].startswith("omitted")
    with pytest.raises(RuntimeError, match="all 15"):
        v5.build_report(rows, execution_finished=True)


def test_atomic_checkpoint_and_completed_report_protection(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    partial = v5.build_report([result(v5.SAMPLE_EPISODE_IDS[0])], execution_finished=False)
    v5.atomic_write_report(path, partial)
    assert not path.with_suffix(".json.tmp").exists()
    assert len(v5.load_checkpoint(path)) == 1
    finished = v5.build_report(
        [result(episode_id) for episode_id in v5.SAMPLE_EPISODE_IDS], execution_finished=True
    )
    v5.atomic_write_report(path, finished)
    with pytest.raises(RuntimeError, match="refusing to overwrite"):
        v5.load_checkpoint(path)


def test_tampered_checkpoint_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "report.json"
    partial = v5.build_report([result(v5.SAMPLE_EPISODE_IDS[0])], execution_finished=False)
    partial["experiment_config"]["completed_indices"] = []
    v5.atomic_write_report(path, partial)
    with pytest.raises(RuntimeError, match="metadata or metrics mismatch"):
        v5.load_checkpoint(path)
    partial = v5.build_report([result(v5.SAMPLE_EPISODE_IDS[0])], execution_finished=False)
    partial["episodes"][0]["system_prompt_sha256"] = "0" * 64
    v5.atomic_write_report(path, partial)
    with pytest.raises(RuntimeError, match="system prompt SHA256 mismatch"):
        v5.load_checkpoint(path)


def test_v5_reuses_v4_prompt_and_private_fields_never_enter_prompt() -> None:
    public_rows, private_rows = v5.assert_release_and_sample_frozen()
    public, private = public_rows[2], private_rows[2]
    _, _, spec, backend = v4.load_temporal_episode(v5.SAMPLE_INDICES[2], v5.RELEASE_DIR)
    observation = backend.reset(spec).public_observation
    selected, _ = v4.select_event_types_for_query(public["query"])
    system = v4.build_system_content(
        public["query"], v4.device_interfaces_from_observation(observation), selected
    )
    lowered = system.lower()
    for banned in ("scenario_type", "required_actions", "reference_policy", "episodes_private", "contract"):
        assert banned not in lowered


def test_api_error_is_preserved_in_report_metrics() -> None:
    row = result(v5.SAMPLE_EPISODE_IDS[0], calls=1)
    row["api_successes"] = 0
    row["parsed_json_actions"] = 0
    row["errors"] = {"RuntimeError:network": 1}
    report = v5.build_report([row], execution_finished=False)
    assert report["episodes"][0]["errors"] == {"RuntimeError:network": 1}
    assert report["metrics_all"]["decision_call_api_success_rate_after_transport_retries"] == 0.0
