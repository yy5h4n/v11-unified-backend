from copy import deepcopy

import pytest

from harness_v2.capability_protocol import (
    CapabilityProtocolViolation,
    build_action_protocol,
    validate_action_against_manifest,
)


@pytest.fixture
def ev_manifest():
    return {
        "backend_id": "fake_mobility_backend",
        "action_kinds": ["act", "wait", "install_rule", "cancel_rule", "ask"],
        "reply_provider_available": True,
        "devices": [
            {"device_id": "vehicle.alpha", "availability": "available", "capabilities": ["mobility.energy"]},
            {"device_id": "vehicle.offline", "availability": "offline", "capabilities": ["mobility.energy"]},
        ],
        "capabilities": [{
            "capability_id": "mobility.energy",
            "operations": [{
                "name": "set_flow",
                "parameters": [
                    {"name": "power_kw", "type": "number", "required": True, "minimum": -7.0, "maximum": 7.0},
                    {"name": "direction", "type": "string", "required": True, "allowed_values": ["in", "out", "idle"]},
                    {"name": "priority", "type": "integer", "required": False, "minimum": 0, "maximum": 3},
                ],
            }],
        }],
    }


@pytest.fixture
def observation():
    return {"devices": {"primary": {"device_id": "vehicle.alpha", "availability": "available"}}}


def command(power=3.5):
    return {
        "device_id": "vehicle.alpha",
        "capability": "mobility.energy",
        "operation": "set_flow",
        "parameters": {"power_kw": power, "direction": "in"},
    }


def validate(action, manifest, observation, **kwargs):
    return validate_action_against_manifest(
        action,
        manifest,
        observation,
        track=kwargs.pop("track", "explicit_profile_control"),
        budgets=kwargs.pop("budgets", {"max_questions": 0}),
        **kwargs,
    )


def test_fake_ev_manifest_builds_protocol_without_family_specific_fields(ev_manifest, observation):
    protocol = build_action_protocol(ev_manifest, observation, "explicit_profile_control", {"max_questions": 0})
    assert protocol["allowed_actions"] == ["act", "install_rule", "cancel_rule", "wait"]
    wire = repr(protocol)
    assert "mobility.energy" in wire and "power_kw" in wire
    assert "thermal" not in wire and "target_c" not in wire
    validate({"kind": "act", "commands": [command()]}, ev_manifest, observation)


@pytest.mark.parametrize("action", [
    {"kind": "wait", "mode": "for", "duration_seconds": 900},
    {"kind": "wait", "mode": "until", "timestamp": "2026-01-01T19:00:00Z"},
    {"kind": "wait", "mode": "until_event", "event_filter": {"type": "laundry.finished"}, "timeout_seconds": 7200},
])
def test_agent_controls_wait_boundary(ev_manifest, observation, action):
    validate(action, ev_manifest, observation)


@pytest.mark.parametrize("action", [
    {"kind": "wait"},
    {"kind": "wait", "mode": "for", "duration_seconds": 0},
    {"kind": "wait", "mode": "until_event", "event_filter": {}, "timeout_seconds": 60},
    {"kind": "wait", "mode": "until_event", "event_filter": {"nested": {}}, "timeout_seconds": 60},
])
def test_ambiguous_or_forged_wait_fails_closed(ev_manifest, observation, action):
    with pytest.raises(CapabilityProtocolViolation):
        validate(action, ev_manifest, observation)


def test_track_and_question_budget_filter_ask(ev_manifest, observation):
    explicit = build_action_protocol(ev_manifest, observation, "explicit_profile_control", {"max_questions": 5})
    interactive = build_action_protocol(ev_manifest, observation, "interactive_clarification", {"max_questions": 2, "questions_used": 1})
    exhausted = build_action_protocol(ev_manifest, observation, "interactive_clarification", {"max_questions": 1, "questions_used": 1})
    assert "ask" not in explicit["allowed_actions"]
    assert "ask" in interactive["allowed_actions"]
    assert "ask" not in exhausted["allowed_actions"]
    validate_action_against_manifest({"kind": "ask", "question": "Which departure?"}, ev_manifest, observation, "interactive_clarification", {"max_questions": 2})
    with pytest.raises(CapabilityProtocolViolation):
        validate_action_against_manifest({"kind": "ask", "question": "Leak?"}, ev_manifest, observation, "explicit_profile_control", {"max_questions": 2})


def test_manifest_can_hide_rules_per_track(ev_manifest, observation):
    manifest = deepcopy(ev_manifest)
    manifest["track_action_kinds"] = {
        "explicit_profile_control": ["act", "wait"],
        "interactive_clarification": ["act", "wait", "ask"],
    }
    protocol = build_action_protocol(manifest, observation, "explicit_profile_control", {"max_questions": 0})
    assert protocol["allowed_actions"] == ["act", "wait"]
    with pytest.raises(CapabilityProtocolViolation):
        validate_action_against_manifest(
            {"kind": "cancel_rule", "rule_id": "rule.x"}, manifest, observation,
            "explicit_profile_control", {"max_questions": 0},
        )


def test_existing_public_view_manifest_shape_is_supported(ev_manifest, observation):
    manifest = {
        "allowed_actions": ev_manifest["action_kinds"],
        "reply_provider_available": True,
        "inventory": {"complete": True, "devices": ev_manifest["devices"]},
        "capability_catalog": ev_manifest["capabilities"],
    }
    protocol = build_action_protocol(manifest, observation, "explicit_profile_control", {"max_questions": 0})
    assert "act" in protocol["allowed_actions"]
    validate_action_against_manifest(
        {"kind": "act", "commands": [command()]}, manifest, observation,
        "explicit_profile_control", {"max_questions": 0},
    )


def test_ask_requires_real_reply_provider(ev_manifest, observation):
    manifest = deepcopy(ev_manifest)
    manifest["reply_provider_available"] = False
    protocol = build_action_protocol(manifest, observation, "interactive_clarification", {"remaining_questions": 1})
    assert "ask" not in protocol["allowed_actions"]
    with pytest.raises(CapabilityProtocolViolation):
        validate_action_against_manifest({"kind": "ask", "question": "Anyone?"}, manifest, observation, "interactive_clarification", {"remaining_questions": 1})


@pytest.mark.parametrize(
    "mutation",
    [
        lambda c: c.update(extra=True),
        lambda c: c.update(device_id="forged.device"),
        lambda c: c.update(capability="forged.capability"),
        lambda c: c.update(operation="forged_operation"),
        lambda c: c["parameters"].update(forged=1),
        lambda c: c["parameters"].pop("direction"),
        lambda c: c["parameters"].update(direction="sideways"),
        lambda c: c["parameters"].update(power_kw=99),
        lambda c: c["parameters"].update(power_kw=True),
        lambda c: c["parameters"].update(power_kw=float("nan")),
        lambda c: c["parameters"].update(power_kw=float("inf")),
    ],
)
def test_commands_fail_closed_against_manifest(ev_manifest, observation, mutation):
    item = command()
    mutation(item)
    with pytest.raises(CapabilityProtocolViolation):
        validate({"kind": "act", "commands": [item]}, ev_manifest, observation)


def test_integer_rejects_bool(ev_manifest, observation):
    item = command()
    item["parameters"]["priority"] = True
    with pytest.raises(CapabilityProtocolViolation):
        validate({"kind": "act", "commands": [item]}, ev_manifest, observation)


def test_duplicate_device_commands_are_rejected(ev_manifest, observation):
    with pytest.raises(CapabilityProtocolViolation):
        validate({"kind": "act", "commands": [command(), command(2.0)]}, ev_manifest, observation)


def test_unavailable_device_is_rejected(ev_manifest, observation):
    item = command()
    item["device_id"] = "vehicle.offline"
    with pytest.raises(CapabilityProtocolViolation):
        validate({"kind": "act", "commands": [item]}, ev_manifest, observation)


def test_rule_uses_same_command_validator_and_horizon(ev_manifest, observation):
    action = {
        "kind": "install_rule",
        "rule": {
            "rule_id": "rule.departure",
            "fire_at_step": 3,
            "release_at_step": 8,
            "commands": [command(5.0)],
            "release_commands": [{**command(0.0), "parameters": {"power_kw": 0.0, "direction": "idle"}}],
        },
    }
    validate(action, ev_manifest, observation, current_step=2, horizon=8)
    for fire, release, horizon in ((2, 8, 8), (3, 3, 8), (3, 9, 8), (True, 8, 8)):
        invalid = deepcopy(action)
        invalid["rule"]["fire_at_step"] = fire
        invalid["rule"]["release_at_step"] = release
        with pytest.raises(CapabilityProtocolViolation):
            validate(invalid, ev_manifest, observation, current_step=2, horizon=horizon)


def test_rule_release_commands_cannot_forge_parameters(ev_manifest, observation):
    action = {
        "kind": "install_rule",
        "rule": {
            "rule_id": "rule.bad_release",
            "fire_at_step": 1,
            "release_at_step": 2,
            "commands": [command()],
            "release_commands": [{**command(), "parameters": {"power_kw": False, "direction": "idle"}}],
        },
    }
    with pytest.raises(CapabilityProtocolViolation):
        validate(action, ev_manifest, observation, current_step=0, horizon=2)


@pytest.mark.parametrize(
    "action",
    [
        {"kind": "wait", "extra": 1},
        {"kind": "cancel_rule", "rule_id": ""},
        {"kind": "act", "commands": []},
        {"kind": "unknown"},
    ],
)
def test_action_envelope_is_strict(ev_manifest, observation, action):
    with pytest.raises(CapabilityProtocolViolation):
        validate(action, ev_manifest, observation)
