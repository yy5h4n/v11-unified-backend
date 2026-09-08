import ast
import hashlib
import inspect
import json
from pathlib import Path

import evaluate_workflow_v4_flash_v3 as v3
import evaluate_workflow_v4_flash_v4 as v4
from harness_v2.temporal_home_backend import TemporalHomeBackend


ROOT = Path(v4.__file__).resolve().parent


class _FakeClient:
    model = "fake-model"

    def __init__(self, content='{"kind":"wait","mode":"for","duration_seconds":60}'):
        self.content = content
        self.calls: list[list[dict[str, str]]] = []

    def complete(self, messages):
        self.calls.append(messages)
        return {"content": self.content, "usage": {"total_tokens": 1}, "latency_ms": 1.0}


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
    return values - {"array", "integer", "number", "object", "string"}


def _interfaces():
    return [{
        "capability": "notification.send",
        "operation": "send",
        "parameters_schema": {"type": "object"},
    }]


def _view(query="Keep monitoring the home.", step=2):
    return {
        "query": query,
        "public_profile": {"local_timezone": "UTC"},
        "allowed_actions": sorted(v4.PUBLIC_ACTION_KINDS),
        "last_feedback": {"status": "accepted"},
        "observation": {
            "step": step,
            "time": f"2026-01-01T18:{step:02d}:00Z",
            "tick_seconds": 60,
            "active_rule_ids": [],
            "inventory": {"complete": True, "devices": [{
                "device_id": "notification.service",
                "device_type": "notification",
                "capabilities": ["notification.send"],
                "interfaces": _interfaces(),
                "availability": "available",
            }]},
            "events": [],
        },
    }


def _policy_after_calls(query="Keep monitoring the home.", calls=2):
    client = _FakeClient()
    policy = v4.WorkflowLLMPolicy(client)
    for step in range(calls):
        policy.decide(_view(query=query, step=step))
    return policy, client


def test_v4_never_imports_or_writes_v3_prompt_behavior():
    source = (ROOT / "evaluate_workflow_v4_flash_v4.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert not any(name.startswith("evaluate_workflow_v4_flash_v") for name in imported)
    assert "evaluate_workflow_v4_flash_v3" not in source.replace(
        "Historical v1/v2/v3 runners and reports remain frozen", ""
    )
    write_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in {"write_text", "write_bytes", "open"}
    ]
    assert all(isinstance(ctx, ast.Load) for node in write_calls for ctx in [node.ctx])


def test_event_catalog_matches_v3_and_backend_source_emissions():
    assert len(v4.EVENT_TYPES) == 85
    assert set(v4.EVENT_TYPES) == _source_emitted_event_types()
    assert set(v4.EVENT_TYPES) == set(v3.EVENT_TYPES)
    assert [row["event_type"] for row in v4.OBSERVABLE_EVENT_CATALOG] == list(v4.EVENT_TYPES)
    assert v4.observable_event_catalog() == v4.OBSERVABLE_EVENT_CATALOG
    assert len(v4.event_catalog_sha256()) == 64
    assert len(v4.prompt_template_sha256()) == 64


def test_fallback_groups_and_rule_events_are_valid_and_always_selected():
    for group in v4.FALLBACK_EVENT_GROUPS.values():
        assert group <= set(v4.EVENT_TYPES)
    assert set(v4.RULE_EVENT_TYPES) <= set(v4.EVENT_TYPES)
    assert {name for name in v4.EVENT_TYPES if name.startswith("rule_")} == set(v4.RULE_EVENT_TYPES)
    for keyword, tokens in v4.QUERY_KEYWORD_TOKENS.items():
        assert keyword == keyword.lower()
        for token in tokens:
            assert any(token in event for event in v4.EVENT_TYPES), (keyword, token)
    selected, matched = v4.select_event_types_for_query("")
    assert matched == ()
    for group in v4.FALLBACK_EVENT_GROUPS.values():
        assert set(group) <= set(selected)
    assert set(v4.RULE_EVENT_TYPES) <= set(selected)


def test_selection_is_deterministic_and_uses_only_public_query_text():
    assert inspect.signature(v4.select_event_types_for_query).parameters.keys() == {"query"}
    generic, _ = v4.select_event_types_for_query("Keep monitoring the home.")
    laundry, laundry_keywords = v4.select_event_types_for_query("Notify me when the laundry finishes.")
    fridge, _ = v4.select_event_types_for_query("Alert me if the fridge door stays open.")
    visitor, _ = v4.select_event_types_for_query("Greet the visitor and secure the garage.")
    assert laundry == v4.select_event_types_for_query("Notify me when the laundry finishes.")[0]
    assert "laundry" in laundry_keywords and "notif" in laundry_keywords
    assert set(laundry) >= {"laundry_cycle_finished", "laundry_loaded"} | set(v4.FALLBACK_DEVICE_COMPLETION_EVENTS)
    assert set(visitor) >= {"visitor_rang", "garage_door_opened"} | set(v4.FALLBACK_EXOGENOUS_TRIGGER_EVENTS)
    assert "fridge_door_closed" in set(fridge) - set(generic)
    assert "notification_sent" in set(laundry) - set(generic)
    assert len(laundry) > len(generic)
    assert len(fridge) > len(generic)
    assert len(visitor) > len(generic)


def test_system_content_contains_four_actions_interfaces_and_selected_events():
    query = "Notify me when the laundry finishes."
    selected, _ = v4.select_event_types_for_query(query)
    content = v4.build_system_content(query, v4.device_interfaces_from_observation(_view(query)["observation"]), selected)
    document = json.loads(content)
    assert document["role_directive"] == v4.SYSTEM_PROMPT
    assert document["action_grammar"] == list(v4.GRAMMAR)
    assert set(document["action_examples"]) == {"act", "wait_for", "wait_until", "wait_until_event", "cancel_rule", "install_rule"}
    assert document["protocol_rules"] == list(v4.PROTOCOL_RULES)
    interfaces = document["device_interfaces"]
    assert interfaces == v4.device_interfaces_from_observation(_view(query)["observation"])
    assert interfaces[0]["device_id"] == "notification.service"
    assert interfaces[0]["interfaces"] == _interfaces()
    event_interface = document["observable_event_interface"]
    assert event_interface["selected_event_types"] == list(selected)
    assert [row["event_type"] for row in event_interface["event_catalog"]] == list(selected)
    laundry_row = next(row for row in event_interface["event_catalog"] if row["event_type"] == "laundry_cycle_finished")
    assert {"name": "type", "type": "string", "required": True} in laundry_row["filterable_scalar_fields"]


def test_system_content_has_no_ask_or_private_leakage():
    selected, _ = v4.select_event_types_for_query("Watch for emergencies and intruders.")
    content = v4.build_system_content("Watch for emergencies and intruders.", [], selected)
    serialized = content.lower()
    assert '"kind": "ask"' not in serialized and '"kind":"ask"' not in serialized
    for banned in ("scenario_type", "required_actions", "reference_policy", "episodes_private", "contract"):
        assert banned not in serialized
    catalog = json.dumps(v4.OBSERVABLE_EVENT_CATALOG, sort_keys=True).lower()
    for banned in ("fire_at_step", "release_at_step", "scenario_type", "contract", "required_actions", "evaluator", "reference_policy"):
        assert banned not in catalog


def test_user_payload_excludes_interface_and_event_catalogs():
    client = _FakeClient()
    policy = v4.WorkflowLLMPolicy(client)
    view = _view()
    previous = {"kind": "wait", "mode": "for", "duration_seconds": 60}
    policy.decide(view)
    payload = json.loads(client.calls[0][1]["content"])
    assert set(payload) == {"query", "user_preferences", "current_observation", "previous_action", "previous_action_result"}
    assert payload["query"] == view["query"]
    assert payload["user_preferences"] == view["public_profile"]
    assert payload["current_observation"] == view["observation"]
    assert payload["previous_action_result"] == view["last_feedback"]
    second = _view(step=3)
    policy.decide(second)
    payload = json.loads(client.calls[1][1]["content"])
    assert payload["previous_action"] == {"kind": "wait", "mode": "for", "duration_seconds": 60}
    assert payload["previous_action_result"] == second["last_feedback"]
    serialized = client.calls[0][1]["content"].lower()
    for banned in ("device_interfaces", "observable_event_interface", "filterable_scalar_fields", "action_grammar", "action_examples", "protocol_rules"):
        assert banned not in serialized


def test_system_prefix_is_byte_identical_across_calls_and_hashed():
    policy, client = _policy_after_calls(query="Keep monitoring the home.", calls=3)
    assert len(client.calls) == 3
    system_messages = [call[0]["content"] for call in client.calls]
    assert all(call[0]["role"] == "system" for call in client.calls)
    assert len(set(system_messages)) == 1
    assert policy.system_content == system_messages[0]
    assert policy.system_prompt_sha256 == hashlib.sha256(system_messages[0].encode()).hexdigest()
    assert policy.system_prompt_sha256 == v4.system_prompt_sha256(policy.system_content)
    assert all(call[1]["role"] == "user" for call in client.calls)


def test_system_prefix_changes_between_episodes_and_guards_mid_episode_drift():
    policy_a, _ = _policy_after_calls(query="Keep monitoring the home.", calls=1)
    policy_b, _ = _policy_after_calls(query="Alert me if the fridge door stays open.", calls=1)
    assert policy_a.system_prompt_sha256 != policy_b.system_prompt_sha256
    assert policy_a.selected_event_types != policy_b.selected_event_types
    assert policy_b.selection_matched_keywords == ("alert", "door", "fridge")
    policy_c = v4.WorkflowLLMPolicy(_FakeClient())
    policy_c.decide(_view())
    drifted = _view(step=3)
    drifted["observation"]["inventory"]["devices"][0]["interfaces"] = []
    try:
        policy_c.decide(drifted)
    except RuntimeError as exc:
        assert "byte-identical" in str(exc)
    else:
        raise AssertionError("mid-episode prefix drift must raise RuntimeError")


def test_loader_uses_temporal_backend_and_four_action_bootstrap():
    _, _, spec, backend = v4.load_temporal_episode(0)
    assert isinstance(backend, TemporalHomeBackend)
    assert set(spec.public_bootstrap["allowed_action_kinds"]) == {"act", "wait", "install_rule", "cancel_rule"}


def test_metrics_and_report_are_v3_compatible_plus_selection_fields():
    row = {
        "episode_id": "a", "responsibility_success": True, "run_status": "completed",
        "device_command_count": 2, "count_unit": "device_command", "calls": 3,
        "api_successes": 3, "parsed_json_actions": 3, "rejected_action_count": 0,
        "tokens": 123, "latency_ms": 30, "harness_action_records": 3,
        "accepted_harness_actions": 3, "protocol_error_count": 0, "backend_error_count": 0,
    }
    metrics = v4.aggregate_results([dict(row)])
    assert metrics == v3.aggregate_results([dict(row)])
    assert metrics["count_unit"] == "device_command"
    assert metrics["total_tokens"] == 123
    assert "action_cost" not in json.dumps(metrics) and "cost_unit" not in json.dumps(metrics)
    policy, _ = _policy_after_calls(query="Keep monitoring the home.", calls=1)
    episode = {
        "episode_id": "a", "model": "fake-model", "responsibility_success": True, "run_status": "completed",
        "device_command_count": 0, "count_unit": "device_command", "calls": 1,
        "api_successes": 1, "parsed_json_actions": 1, "rejected_action_count": 0,
        "system_prompt_sha256": policy.system_prompt_sha256,
        "selected_event_types": list(policy.selected_event_types),
        "selection_matched_keywords": list(policy.selection_matched_keywords),
        "tokens": 1, "latency_ms": 1, "harness_action_records": 1,
        "accepted_harness_actions": 1, "protocol_error_count": 0, "backend_error_count": 0,
        "max_consecutive_identical_rejected_action": 0,
    }
    baseline = {"episodes": [dict(episode, model="x")]}
    report = v4.build_report([episode], [0], execution_finished=True, baseline=baseline)
    assert report["schema_version"] == "temporal-home-v4-flash-query-scoped-system-prefix-v4"
    config = report["experiment_config"]
    assert config["system_prefix_policy"] == "episode_static_byte_identical_across_calls"
    assert config["event_selection"]["basis"] == "public_query_text_only"
    assert config["event_selection"]["always_included_groups"] == ["device_completion", "exogenous_trigger", "rule_events"]
    assert config["event_selection"]["fallback_groups"]["device_completion"] == sorted(v4.FALLBACK_DEVICE_COMPLETION_EVENTS)
    assert report["episodes"][0]["system_prompt_sha256"] == policy.system_prompt_sha256
    assert report["episodes"][0]["selected_event_types"] == list(policy.selected_event_types)
    assert report["paired_vs_v2"]["responsibility_success"]["paired_episode_count"] == 1
    assert report["rejected_action_loop_diagnostics"] == {"a": 0}


def test_rejected_action_loop_metric_uses_canonical_action_identity():
    action = {"kind": "wait", "duration_seconds": 60, "mode": "for"}
    reordered = {"mode": "for", "kind": "wait", "duration_seconds": 60}
    records = [
        {"type": "action", "accepted": False, "action": action},
        {"type": "action", "accepted": False, "action": reordered},
        {"type": "action", "accepted": True, "action": action},
    ]
    assert v4.max_consecutive_identical_rejected_action(records) == 2
