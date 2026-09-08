import ast
import json
from pathlib import Path

import evaluate_workflow_v4_flash_v3 as v3
from harness_v2.temporal_home_backend import TemporalHomeBackend


ROOT = Path(v3.__file__).resolve().parent


def _source_emitted_event_types():
    tree = ast.parse((ROOT / "harness_v2/workflow_backend.py").read_text(encoding="utf-8"))
    values = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if not isinstance(key, ast.Constant) or key.value != "type":
                continue
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                values.add(value.value)
            elif isinstance(value, ast.IfExp):
                for branch in (value.body, value.orelse):
                    if isinstance(branch, ast.Constant) and isinstance(branch.value, str):
                        values.add(branch.value)
    # Parameter JSON-schema literals in this module are not emitted events.
    return values - {"array", "integer", "number", "object", "string"}


def _view():
    return {
        "query": "Keep monitoring the home.",
        "public_profile": {"local_timezone": "UTC"},
        "allowed_actions": sorted(v3.PUBLIC_ACTION_KINDS),
        "last_feedback": {"status": "accepted"},
        "observation": {
            "step": 2,
            "time": "2026-01-01T18:02:00Z",
            "tick_seconds": 60,
            "active_rule_ids": [],
            "inventory": {"devices": [{"device_id": "notification.service", "interfaces": []}]},
            "events": [],
        },
    }


def test_event_catalog_exactly_matches_backend_source_emissions():
    assert len(v3.EVENT_TYPES) == 85
    assert set(v3.EVENT_TYPES) == _source_emitted_event_types()
    assert [row["event_type"] for row in v3.OBSERVABLE_EVENT_CATALOG] == list(v3.EVENT_TYPES)


def test_event_catalog_is_deterministic_and_has_filter_schemas():
    assert v3.observable_event_catalog() == v3.OBSERVABLE_EVENT_CATALOG
    assert len(v3.event_catalog_sha256()) == 64
    for row in v3.OBSERVABLE_EVENT_CATALOG:
        names = [field["name"] for field in row["filterable_scalar_fields"]]
        assert "type" in names
        assert len(names) == len(set(names))
        assert all(field["type"] in {"string", "integer", "number", "boolean"} for field in row["filterable_scalar_fields"])
    notification = next(row for row in v3.OBSERVABLE_EVENT_CATALOG if row["event_type"] == "notification_sent")
    assert notification["public_nonfilterable_fields"] == [{"name": "recipients", "type": "list[string]", "required": True}]


def test_prompt_exposes_exact_four_actions_and_no_ask():
    protocol = v3.action_protocol(_view())
    serialized = json.dumps({"system": v3.SYSTEM_PROMPT, "protocol": protocol}, ensure_ascii=False).lower()
    assert protocol["allowed_actions"] == ["act", "cancel_rule", "install_rule", "wait"]
    assert set(v3.ACTION_EXAMPLES) == {"act", "wait_for", "wait_until", "wait_until_event", "cancel_rule", "install_rule"}
    assert '"kind": "ask"' not in serialized
    assert '"ask"' not in serialized


def test_payload_has_previous_action_and_result():
    previous = {"kind": "wait", "mode": "for", "duration_seconds": 60}
    messages = v3.build_messages(_view(), previous)
    payload = json.loads(messages[1]["content"])
    assert payload["previous_action"] == previous
    assert payload["previous_action_result"] == _view()["last_feedback"]


def test_catalog_has_no_episode_schedule_or_evaluator_leakage():
    serialized = json.dumps(v3.OBSERVABLE_EVENT_CATALOG, sort_keys=True).lower()
    for banned in ("fire_at_step", "release_at_step", "scenario_type", "contract", "required_actions", "evaluator", "reference_policy"):
        assert banned not in serialized


def test_loader_switches_to_temporal_backend_and_exact_bootstrap():
    _, _, spec, backend = v3.load_temporal_episode(0)
    assert isinstance(backend, TemporalHomeBackend)
    assert set(spec.public_bootstrap["allowed_action_kinds"]) == {"act", "wait", "install_rule", "cancel_rule"}


def test_report_vocabulary_and_token_totals_are_temporal_home_only():
    rows = [{
        "episode_id": "a", "responsibility_success": True, "run_status": "completed",
        "device_command_count": 2, "count_unit": "device_command", "calls": 3,
        "api_successes": 3, "parsed_json_actions": 3, "rejected_action_count": 0,
        "tokens": 123, "latency_ms": 30, "harness_action_records": 3,
        "accepted_harness_actions": 3, "protocol_error_count": 0, "backend_error_count": 0,
    }]
    metrics = v3.aggregate_results(rows)
    serialized = json.dumps(metrics)
    assert metrics["total_tokens"] == 123
    assert metrics["success_conditioned_device_command_count"] == 2
    assert metrics["count_unit"] == "device_command"
    assert "action_cost" not in serialized
    assert "cost_unit" not in serialized


def test_rejected_action_loop_metric_uses_canonical_action_identity():
    action = {"kind": "wait", "duration_seconds": 60, "mode": "for"}
    reordered = {"mode": "for", "kind": "wait", "duration_seconds": 60}
    records = [
        {"type": "action", "accepted": False, "action": action},
        {"type": "action", "accepted": False, "action": reordered},
        {"type": "action", "accepted": True, "action": action},
    ]
    assert v3.max_consecutive_identical_rejected_action(records) == 2
