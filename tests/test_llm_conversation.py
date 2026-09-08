"""Tests for the migrated V10 LLM-facing compact observation protocol."""

from __future__ import annotations

import json

import pytest

from unified_compiler.llm_conversation import (
    CompactObservationConversation,
    ConversationProtocolError,
    apply_observation_delta,
    extend_system_content,
    observation_delta,
    public_action_result_from_receipt,
    prompt_template_sha256,
    validate_canonical_conversation,
)


def test_delta_round_trip_preserves_missing_vs_null_and_nested_arrays() -> None:
    before = {"step": 0, "rooms": [{"temperature": 20.0, "humidity": None}], "removed_later": True}
    after = {"step": 1, "rooms": [{"temperature": 21.0, "humidity": None}], "added": "value"}

    delta = observation_delta(before, after)
    assert delta["op"] == "object"
    assert apply_observation_delta(before, delta) == after
    assert observation_delta(after, after) == {"op": "none"}


def test_compact_conversation_sends_full_initial_observation_then_only_deltas() -> None:
    initial = {"step": 0, "room": {"temperature": 20.0, "optional": None}}
    next_observation = {"step": 1, "room": {"temperature": 21.0, "optional": None}}
    conversation = CompactObservationConversation(
        system_content='{"role_directive":"test"}',
        query="keep the room comfortable",
        public_preferences={"quiet": True},
        initial_observation=initial,
    )

    assert len(conversation.messages()) == 2
    conversation.append_action({"kind": "act", "commands": []})
    delta = conversation.append_environment({"status": "accepted"}, next_observation)
    messages = conversation.messages()
    assert len(messages) == 4
    initial_payload = json.loads(messages[1]["content"])
    environment_payload = json.loads(messages[3]["content"])
    assert initial_payload["initial_observation"] == initial
    assert "current_observation" not in environment_payload
    assert environment_payload["observation_delta"] == delta
    assert conversation.latest_observation == next_observation
    assert conversation.validate() == [initial, next_observation]


def test_canonical_validator_rejects_tampered_delta() -> None:
    conversation = CompactObservationConversation(
        system_content='{"role_directive":"test"}',
        query="query",
        public_preferences={},
        initial_observation={"step": 0, "value": 1},
    )
    conversation.append_action({"kind": "wait"})
    conversation.append_environment({"status": "accepted"}, {"step": 1, "value": 2})
    messages = conversation.messages()
    payload = json.loads(messages[-1]["content"])
    payload["observation_delta"]["changed"]["value"]["before"] = 999
    messages[-1] = {"role": "user", "content": json.dumps(payload, separators=(",", ":"))}

    with pytest.raises(ConversationProtocolError, match="before-value mismatch"):
        validate_canonical_conversation(messages)


def test_system_extension_and_protocol_hash_are_deterministic() -> None:
    base = {"role_directive": "act safely", "device_interfaces": []}
    first = extend_system_content(base)
    second = extend_system_content(json.dumps(base))
    assert first == second
    document = json.loads(first)
    assert document["observation_delta_protocol"]["ops"]["array"]
    assert len(prompt_template_sha256()) == 64


def test_conversation_rejects_action_without_environment_feedback() -> None:
    conversation = CompactObservationConversation(
        system_content='{"role_directive":"test"}',
        query="query",
        public_preferences={},
        initial_observation={"step": 0},
    )
    conversation.append_action({"kind": "wait"})
    with pytest.raises(ConversationProtocolError, match="environment feedback"):
        conversation.append_action({"kind": "wait"})


def test_receipt_projection_never_sends_full_observation_as_feedback() -> None:
    result = public_action_result_from_receipt(
        {
            "time_seconds": 60.0,
            "delta_t_seconds": 60.0,
            "done": False,
            "terminated": False,
            "truncated": False,
            "observation": {"private": "must stay in the delta source"},
            "action": {"kind": "wait"},
            "info": {"accepted": True},
        }
    )
    assert "observation" not in result
    assert "action" not in result
    assert result["info"] == {"accepted": True}
