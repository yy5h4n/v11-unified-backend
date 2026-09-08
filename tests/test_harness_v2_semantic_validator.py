from copy import deepcopy
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from harness_v2.semantic_validator import (
    ConformanceError,
    validate_agent_view,
    validate_callback,
    validate_episode_configuration,
    validate_evaluator_manifest,
    validate_scenario_group,
    validate_score,
    validate_sealed_session,
)


SHA = "a" * 64
STATE = {"backend": SHA, "rules": SHA, "subscriptions": SHA, "wake_queue": SHA, "accounting": SHA}
PAYLOAD = {"media_type": "application/json;profile=rfc8785", "canonical_bytes_base64": "e30=", "sha256": SHA}
SCHEMA_DIR = Path(__file__).parents[1] / "harness_v2"


def schema_validator(schema_name, definition):
    schemas = [json.loads(path.read_text()) for path in SCHEMA_DIR.glob("*.json")]
    registry = Registry().with_resources((schema["$id"], Resource.from_contents(schema)) for schema in schemas)
    target = next(schema for schema in schemas if schema["$id"].endswith(schema_name))
    wrapper = {"$schema": "https://json-schema.org/draft/2020-12/schema", "$ref": f'{target["$id"]}#/$defs/{definition}'}
    return Draft202012Validator(wrapper, registry=registry, format_checker=FormatChecker())


def config():
    return {
        "track": "explicit_profile_control",
        "clocks": {"episode_start": "2026-01-01T00:00:00+00:00", "episode_termination": "2026-01-01T01:00:00+00:00"},
        "budgets": {"clarification_questions": 0},
        "clarification_policy": {"enabled": False},
    }


def view():
    return {
        "query": {"text": "Keep the kitchen warm in the evening.", "language": "en", "context": {"room_refs": ["kitchen"], "named_period_refs": ["evening"]}},
        "track": "explicit_profile_control",
        "profile": {"preferences": [{"name": "comfort", "value": 22, "provenance": "user_stated", "evidence_id": "ev1"}]},
        "inventory": {"devices": [{"device_id": "hvac1", "capabilities": ["thermal"]}]},
        "capability_catalog": [{"capability_id": "thermal", "operations": [{"name": "set", "parameters": []}]}],
        "rooms": [{"room_id": "kitchen", "device_ids": ["hvac1"]}],
        "observation_catalog": [],
        "observations": [],
        "events": [],
        "event_catalog": {"event_types": [], "filters": []},
        "named_periods": [{"name": "evening", "start": "2026-01-01T17:00:00+00:00", "end": "2026-01-01T23:00:00+00:00"}],
        "costs_budgets": {"action_costs": []},
        "tool_schema": {"allowed_actions": ["inspect", "act_now"]},
    }


def audit():
    return {"evidence_records": [{"evidence_id": "ev1", "source_class": "user_stated"}]}


def callback():
    return {"reasons": ["requested_wake_time"], "events": [], "workflow_results": [], "user_reply": None, "termination": False}


def session():
    records = []
    for i, kind in enumerate(("bootstrap_delivered", "termination_notice")):
        records.append({"sequence": i, "timestamp": f"2026-01-01T0{i}:00:00+00:00", "kind": kind, "pre_state": STATE, "post_state": STATE})
    return {"records": records, "turns": []}


def manifest():
    component = {"component_id": "comfort", "weight": "1", "integration": "left_endpoint", "active_selector": {"mode": "all_intervals", "binding": None}}
    return {"loss_components": [component], "safety_thresholds": [{"threshold_id": "safe"}]}


def group():
    base_axes = {key: {"kind": "literal", "value": key} for key in ("beneficiary", "spatial_scope", "activation_condition", "suspension_condition", "persistence", "deadline", "release_condition", "priority", "authorized_action")}
    a_axes = deepcopy(base_axes)
    b_axes = deepcopy(base_axes)
    b_axes["spatial_scope"] = {"kind": "literal", "value": "whole_home"}
    ev = {"family_id": "thermal", "manifest_version": "2", "implementation_hash": SHA}
    return {"contrast_axis": "spatial_scope", "arms": [{"arm_id": "A", "canonical_contract": {"axes": a_axes}, "evaluator_manifest": ev}, {"arm_id": "B", "canonical_contract": {"axes": b_axes}, "evaluator_manifest": deepcopy(ev)}]}


def test_positive_semantic_vectors():
    validate_episode_configuration(config())
    validate_agent_view(view(), audit())
    validate_callback(callback())
    validate_sealed_session(session())
    validate_evaluator_manifest(manifest())
    validate_scenario_group(group())


def test_rejects_termination_before_start():
    item = config()
    item["clocks"]["episode_termination"] = item["clocks"]["episode_start"]
    with pytest.raises(ConformanceError):
        validate_episode_configuration(item)


def test_rejects_duplicate_conflicting_device():
    item = view()
    item["inventory"]["devices"].append({"device_id": "hvac1", "capabilities": []})
    with pytest.raises(ConformanceError):
        validate_agent_view(item, audit())


@pytest.mark.parametrize("leak", ["ORACLE target", "Active Mask", "Gold-answer"])
def test_rejects_casefolded_private_canary(leak):
    item = view()
    item["profile"]["preferences"][0]["value"] = leak
    with pytest.raises(ConformanceError):
        validate_agent_view(item, audit())


def test_rejects_self_asserted_preference_provenance():
    item = audit()
    item["evidence_records"][0]["source_class"] = "system_configuration"
    with pytest.raises(ConformanceError):
        validate_agent_view(view(), item)


def test_rejects_query_context_room_not_in_public_inventory():
    item = view()
    item["query"]["context"]["room_refs"] = ["private_bedroom"]
    with pytest.raises(ConformanceError, match="unknown rooms"):
        validate_agent_view(item, audit())


def test_rejects_query_context_period_not_in_public_schedule():
    item = view()
    item["query"]["context"]["named_period_refs"] = ["late_evening"]
    with pytest.raises(ConformanceError, match="unknown named periods"):
        validate_agent_view(item, audit())


def test_rejects_event_reason_without_event():
    item = callback()
    item["reasons"] = ["subscribed_public_event"]
    with pytest.raises(ConformanceError):
        validate_callback(item)


def test_rejects_noncontiguous_or_incomplete_transcript():
    item = session()
    item["records"][1]["sequence"] = 3
    with pytest.raises(ConformanceError):
        validate_sealed_session(item)


def test_rejects_all_zero_loss_weights():
    item = manifest()
    item["loss_components"][0]["weight"] = "0"
    with pytest.raises(ConformanceError):
        validate_evaluator_manifest(item)


def test_rejects_identical_contract_arms():
    item = group()
    item["arms"][1]["canonical_contract"] = deepcopy(item["arms"][0]["canonical_contract"])
    with pytest.raises(ConformanceError):
        validate_scenario_group(item)


def test_rejects_evaluator_drift_across_arms():
    item = group()
    item["arms"][1]["evaluator_manifest"]["implementation_hash"] = "b" * 64
    with pytest.raises(ConformanceError):
        validate_scenario_group(item)


def test_schema_rejects_fresh_null_observation():
    validator = schema_validator("public_private_schema_v2.json", "observation")
    item = {"name": "rooms.kitchen.temperature", "value": None, "unit": "C", "quality": "fresh", "observed_at": "2026-01-01T00:00:00+00:00"}
    assert not validator.is_valid(item)


def test_schema_rejects_event_reason_without_payload():
    validator = schema_validator("public_private_schema_v2.json", "callback_envelope")
    item = {"callback_id": "cb1", "timestamp": "2026-01-01T00:00:00+00:00", "reasons": ["subscribed_public_event"], "observation_deltas": [], "events": [], "workflow_results": [], "execution_feedback": [], "user_reply": None, "termination": False}
    assert not validator.is_valid(item)


@pytest.mark.parametrize(
    "event",
    [
        {"event_id":"e1","timestamp":"2026-01-01T00:00:00+00:00","type":"question_answered","question_id":"q1","error_code":"ERROR"},
        {"event_id":"e1","timestamp":"2026-01-01T00:00:00+00:00","type":"question_rejected","question_id":"q1","error_code":None},
    ],
)
def test_schema_rejects_inconsistent_question_outcome(event):
    validator = schema_validator("evaluator_and_group_protocol_v2.json", "protocolEvent")
    assert not validator.is_valid(event)


def test_shared_types_reject_whitespace_id_and_uppercase_digest():
    identifier = schema_validator("shared_types_v2.json", "identifier")
    digest = schema_validator("shared_types_v2.json", "sha256")
    assert not identifier.is_valid("bad id with spaces")
    assert not digest.is_valid("A" * 64)


def test_recomputes_normalized_gain_and_rejects_forgery():
    evaluator = {"opportunity_gap_threshold": "1", "main_gain_threshold": "0.5"}
    main = {
        "agent": {"loss": "3", "status":"eligible"}, "noop": {"loss": "5", "status":"eligible"}, "oracle": {"loss": "1", "status":"eligible"},
        "score": {
            "protocol_status": "valid", "safety_status": "safe", "oracle_status": "ok",
            "agent_loss": "3", "noop_loss": "5", "oracle_loss": "1", "denominator": "4",
            "gain_status": "eligible", "raw_gain": "0.5", "raw_normalized_regret": "0.5",
            "display_clipped_gain": "0.5", "main_gain_pass": True,
        },
    }
    validate_score(evaluator, main)
    main["score"]["raw_gain"] = "0.9"
    with pytest.raises(ConformanceError):
        validate_score(evaluator, main)
