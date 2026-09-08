"""Stable LLM conversation assembly for the V10 compact-observation protocol.

The native backend deliberately returns complete public observations.  This
module is the LLM-facing serialization layer: it sends the complete initial
observation once, then appends lossless ``observation_delta`` messages after
each action.  Keeping this layer above :mod:`agent_interface` lets native
routes remain truthful and lets callers reconstruct every public state from
the sent conversation without exposing private backend state.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import hashlib
import json
from collections.abc import Mapping
from typing import Any


class ConversationProtocolError(ValueError):
    """The compact conversation is malformed or cannot be reconstructed."""


OBSERVATION_DELTA_PROTOCOL_KEY = "observation_delta_protocol"
DELTA_OPS = {
    "none": "prior and next snapshots are canonically identical",
    "replace": "scalar/type/list-shape change; carries exact before and after values",
    "object": "recursive dict change with added values, removed key list, and changed sub-deltas",
    "array": "same-length list change with per-index sub-deltas",
}
OBSERVATION_DELTA_INSTRUCTIONS = (
    "The initial user message contains initial_observation: that is the full "
    "baseline observation for the whole Episode.",
    "Every later environment_observation user message contains the exact "
    "action_result plus an observation_delta relative to the immediately "
    "preceding reconstructed observation, never a full snapshot.",
    "Apply the observation deltas sequentially, starting from initial_observation, "
    "to reconstruct the current full observation before deciding.",
    "Delta ops: op=none means no change; op=replace carries before and after, "
    "use the after value; op=object removes keys listed in removed, adds values "
    "in added, and recursively applies changed; op=array recursively applies "
    "changed entries keyed by decimal index.",
    "Never treat omitted unchanged fields as absent: a key missing from a delta is "
    "unchanged, and a missing key differs from an explicit null, which is a value.",
)


def canonical_json(value: Any) -> str:
    """Serialize JSON deterministically and reject non-finite numbers."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _canonically_equal(left: Any, right: Any) -> bool:
    return canonical_json(left) == canonical_json(right)


def diff_observation(old: Any, new: Any) -> dict[str, Any] | None:
    """Build the deterministic, lossless recursive V10 observation diff."""

    if _canonically_equal(old, new):
        return None
    if isinstance(old, dict) and isinstance(new, dict):
        added = {key: deepcopy(new[key]) for key in sorted(set(new) - set(old))}
        removed = sorted(set(old) - set(new))
        changed: dict[str, Any] = {}
        for key in sorted(set(old) & set(new)):
            sub = diff_observation(old[key], new[key])
            if sub is not None:
                changed[key] = sub
        node: dict[str, Any] = {"op": "object"}
        if added:
            node["added"] = added
        if removed:
            node["removed"] = removed
        if changed:
            node["changed"] = changed
        return node
    if isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
        changed = {}
        for index, (old_item, new_item) in enumerate(zip(old, new)):
            sub = diff_observation(old_item, new_item)
            if sub is not None:
                changed[str(index)] = sub
        return {"op": "array", "changed": changed} if changed else None
    return {"op": "replace", "before": deepcopy(old), "after": deepcopy(new)}


def observation_delta(old: Any, new: Any) -> dict[str, Any]:
    """Return an explicit ``none`` operation for identical observations."""

    return diff_observation(old, new) or {"op": "none"}


def apply_observation_delta(before: Any, delta: Any) -> Any:
    """Strictly apply one delta and reject tampered or ambiguous payloads."""

    if not isinstance(delta, dict) or set(delta) - {"op", "before", "after", "added", "removed", "changed"}:
        raise ConversationProtocolError("observation delta must be an op-tagged object")
    op = delta.get("op")
    if op == "none":
        if set(delta) != {"op"}:
            raise ConversationProtocolError("none delta fields invalid")
        return deepcopy(before)
    if op == "replace":
        if set(delta) != {"op", "before", "after"}:
            raise ConversationProtocolError("replace delta fields invalid")
        if not _canonically_equal(before, delta["before"]):
            raise ConversationProtocolError("replace delta before-value mismatch")
        return deepcopy(delta["after"])
    if op == "object":
        if set(delta) - {"op", "added", "removed", "changed"} or set(delta) == {"op"}:
            raise ConversationProtocolError("object delta fields invalid")
        if not isinstance(before, dict):
            raise ConversationProtocolError("object delta requires a dict snapshot")
        removed = delta.get("removed", [])
        added = delta.get("added", {})
        changed = delta.get("changed", {})
        if not isinstance(removed, list) or not isinstance(added, dict) or not isinstance(changed, dict):
            raise ConversationProtocolError("object delta collections invalid")
        if any(not isinstance(key, str) for key in removed) or len(set(removed)) != len(removed):
            raise ConversationProtocolError("object delta removed keys invalid")
        if any(not isinstance(key, str) for key in added) or any(not isinstance(key, str) for key in changed):
            raise ConversationProtocolError("object delta changed keys invalid")
        overlap = set(removed) & (set(added) | set(changed))
        if overlap:
            raise ConversationProtocolError("object delta adds or changes a removed key")
        result = deepcopy(before)
        for key in removed:
            if key not in result:
                raise ConversationProtocolError("object delta removes a missing key")
            del result[key]
        for key, value in added.items():
            if key in result:
                raise ConversationProtocolError("object delta adds an existing key")
            result[key] = deepcopy(value)
        for key, sub in changed.items():
            if key not in result:
                raise ConversationProtocolError("object delta changes a missing key")
            result[key] = apply_observation_delta(result[key], sub)
        return result
    if op == "array":
        if set(delta) != {"op", "changed"} or not isinstance(delta["changed"], dict):
            raise ConversationProtocolError("array delta fields invalid")
        if not isinstance(before, list):
            raise ConversationProtocolError("array delta requires a list snapshot")
        result = deepcopy(before)
        for index, sub in delta["changed"].items():
            if not isinstance(index, str) or not index.isdigit():
                raise ConversationProtocolError("array delta index invalid")
            position = int(index)
            if position >= len(result):
                raise ConversationProtocolError("array delta index out of range")
            result[position] = apply_observation_delta(result[position], sub)
        return result
    raise ConversationProtocolError("unknown observation delta op")


def extend_system_content(
    base_system_content: str | Mapping[str, Any],
    *,
    public_action_schema: Mapping[str, Any] | None = None,
) -> str:
    """Add V10 delta instructions and, when supplied, the public action schema."""

    if isinstance(base_system_content, str):
        try:
            document = json.loads(base_system_content)
        except json.JSONDecodeError as exc:
            raise ConversationProtocolError("base system content must be JSON") from exc
    elif isinstance(base_system_content, Mapping):
        document = deepcopy(dict(base_system_content))
    else:
        raise ConversationProtocolError("base system content must be a JSON object")
    if not isinstance(document, dict):
        raise ConversationProtocolError("base system content must be a JSON object")
    document[OBSERVATION_DELTA_PROTOCOL_KEY] = {
        "instructions": list(OBSERVATION_DELTA_INSTRUCTIONS),
        "ops": deepcopy(DELTA_OPS),
    }
    if public_action_schema is not None:
        if not isinstance(public_action_schema, Mapping):
            raise ConversationProtocolError("public action schema must be an object")
        document["episode_action_interface"] = {
            "legal_actions": deepcopy(dict(public_action_schema)),
            "instruction": "Emit only one action matching this published public schema; never invent device channels.",
        }
    return json.dumps(document, ensure_ascii=False, sort_keys=True, indent=1, allow_nan=False)


def system_prompt_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def prompt_template_sha256() -> str:
    payload = {
        "protocol": "v10.compact_observation.v1",
        "delta_ops": DELTA_OPS,
        "instructions": OBSERVATION_DELTA_INSTRUCTIONS,
        "message_fields": {
            "initial": ["message_type", "original_query", "public_preferences", "initial_observation"],
            "environment": ["message_type", "action_result", "observation_delta"],
        },
    }
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def build_initial_user_message(
    query: str, public_preferences: Mapping[str, Any], initial_observation: Mapping[str, Any]
) -> dict[str, str]:
    payload = {
        "message_type": "initial_request",
        "original_query": query,
        "public_preferences": deepcopy(dict(public_preferences)),
        "initial_observation": deepcopy(dict(initial_observation)),
    }
    return {"role": "user", "content": canonical_json(payload)}


def canonical_assistant_action(action: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(action, Mapping):
        raise ConversationProtocolError("assistant action must be an object")
    return {
        "role": "assistant",
        "content": "<answer>" + canonical_json(dict(action)) + "</answer>",
    }


def build_environment_delta_message(feedback: Any, delta: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(delta, Mapping):
        raise ConversationProtocolError("observation delta must be an object")
    payload = {
        "message_type": "environment_observation",
        "action_result": deepcopy(feedback),
        "observation_delta": deepcopy(dict(delta)),
    }
    return {"role": "user", "content": canonical_json(payload)}


def public_action_result_from_receipt(receipt: Mapping[str, Any]) -> dict[str, Any]:
    """Project a backend receipt into the public action-result envelope.

    The receipt's full ``observation`` is deliberately excluded. Callers can
    pass the returned object to :meth:`CompactObservationConversation.append_environment`
    and send the observation separately through the delta codec.
    """

    if not isinstance(receipt, Mapping):
        raise ConversationProtocolError("backend receipt must be an object")
    allowed = ("time_seconds", "delta_t_seconds", "done", "terminated", "truncated", "info")
    return {key: deepcopy(receipt[key]) for key in allowed if key in receipt}


def _parse_json_object(content: Any, error: str) -> dict[str, Any]:
    if not isinstance(content, str):
        raise ConversationProtocolError(error)

    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ConversationProtocolError(f"{error}: duplicate key {key}")
            result[key] = value
        return result

    def reject_constant(value: str) -> Any:
        raise ConversationProtocolError(f"{error}: non-finite JSON constant {value}")

    try:
        value = json.loads(content, object_pairs_hook=pairs, parse_constant=reject_constant)
    except ConversationProtocolError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ConversationProtocolError(error) from exc
    if not isinstance(value, dict):
        raise ConversationProtocolError(error)
    return value


def parse_assistant_action(message: Mapping[str, Any]) -> dict[str, Any]:
    if message.get("role") != "assistant" or not isinstance(message.get("content"), str):
        raise ConversationProtocolError("assistant action message invalid")
    content = message["content"]
    if content.count("<answer>") != 1 or content.count("</answer>") != 1:
        raise ConversationProtocolError("assistant action envelope count invalid")
    start = content.index("<answer>") + len("<answer>")
    end = content.index("</answer>")
    if end < start or content[start:end].strip() == "":
        raise ConversationProtocolError("assistant action envelope empty")
    return _parse_json_object(content[start:end].strip(), "assistant action JSON invalid")


def _parse_environment_message(message: Mapping[str, Any]) -> tuple[Any, dict[str, Any]]:
    if message.get("role") != "user":
        raise ConversationProtocolError("environment message role invalid")
    payload = _parse_json_object(message.get("content"), "environment message JSON invalid")
    if set(payload) != {"message_type", "action_result", "observation_delta"}:
        raise ConversationProtocolError("environment message fields invalid")
    if payload["message_type"] != "environment_observation":
        raise ConversationProtocolError("environment message type invalid")
    if not isinstance(payload["observation_delta"], dict):
        raise ConversationProtocolError("environment observation_delta invalid")
    return payload["action_result"], payload["observation_delta"]


def validate_canonical_conversation(
    conversation: Any,
    *,
    expected_query: str | None = None,
    expected_initial_observation: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Validate and reconstruct every public observation in a V10 history."""

    if not isinstance(conversation, list) or len(conversation) < 2:
        raise ConversationProtocolError("conversation is incomplete")
    if not isinstance(conversation[0], Mapping) or conversation[0].get("role") != "system":
        raise ConversationProtocolError("conversation system prefix invalid")
    initial = _parse_json_object(conversation[1].get("content"), "initial request JSON invalid")
    if conversation[1].get("role") != "user" or set(initial) != {
        "message_type", "original_query", "public_preferences", "initial_observation"
    }:
        raise ConversationProtocolError("initial request fields invalid")
    if initial["message_type"] != "initial_request":
        raise ConversationProtocolError("initial request type invalid")
    if expected_query is not None and initial["original_query"] != expected_query:
        raise ConversationProtocolError("initial query mismatch")
    if expected_initial_observation is not None and not _canonically_equal(
        initial["initial_observation"], dict(expected_initial_observation)
    ):
        raise ConversationProtocolError("initial observation mismatch")
    observations = [deepcopy(initial["initial_observation"])]
    suffix = conversation[2:]
    if len(suffix) % 2:
        raise ConversationProtocolError("conversation has an unpaired action")
    for offset in range(0, len(suffix), 2):
        parse_assistant_action(suffix[offset])
        _feedback, delta = _parse_environment_message(suffix[offset + 1])
        observations.append(apply_observation_delta(observations[-1], delta))
    return observations


@dataclass
class CompactObservationConversation:
    """Append-only V10 history builder for one Episode.

    The latest full observation is retained only in memory.  ``messages``
    contains the compact conversation that is safe to send to an LLM.
    """

    system_content: str
    query: str
    public_preferences: Mapping[str, Any]
    initial_observation: Mapping[str, Any]
    public_action_schema: Mapping[str, Any] | None = None
    _messages: list[dict[str, str]] = field(init=False, repr=False)
    _latest_observation: dict[str, Any] = field(init=False, repr=False)
    _pending_action: bool = field(default=False, init=False, repr=False)

    def __post_init__(self) -> None:
        if not isinstance(self.system_content, str) or not self.system_content:
            raise ConversationProtocolError("system content must be non-empty text")
        if not isinstance(self.public_preferences, Mapping) or not isinstance(self.initial_observation, Mapping):
            raise ConversationProtocolError("public preferences and initial observation must be objects")
        system_document = _parse_json_object(self.system_content, "system content JSON invalid")
        has_action_schema = any(
            key in system_document for key in ("episode_action_interface", "device_interfaces", "action_grammar")
        )
        if self.public_action_schema is not None:
            self.system_content = extend_system_content(
                self.system_content, public_action_schema=self.public_action_schema
            )
            has_action_schema = True
        if not has_action_schema:
            raise ConversationProtocolError(
                "system content must publish device_interfaces/action_grammar or public_action_schema"
            )
        self._messages = [
            {"role": "system", "content": self.system_content},
            build_initial_user_message(self.query, self.public_preferences, self.initial_observation),
        ]
        self._latest_observation = deepcopy(dict(self.initial_observation))

    @property
    def pending_action(self) -> bool:
        return self._pending_action

    @property
    def latest_observation(self) -> dict[str, Any]:
        return deepcopy(self._latest_observation)

    def append_action(self, action: Mapping[str, Any]) -> None:
        if self._pending_action:
            raise ConversationProtocolError("cannot append action before environment feedback")
        self._messages.append(canonical_assistant_action(action))
        self._pending_action = True

    def append_environment(self, feedback: Any, observation: Mapping[str, Any]) -> dict[str, Any]:
        if not self._pending_action:
            raise ConversationProtocolError("environment feedback has no pending action")
        if not isinstance(observation, Mapping):
            raise ConversationProtocolError("environment observation must be an object")
        next_observation = deepcopy(dict(observation))
        delta = observation_delta(self._latest_observation, next_observation)
        self._messages.append(build_environment_delta_message(feedback, delta))
        self._latest_observation = next_observation
        self._pending_action = False
        return deepcopy(delta)

    def messages(self) -> list[dict[str, str]]:
        return deepcopy(self._messages)

    def validate(self) -> list[dict[str, Any]]:
        if self._pending_action:
            raise ConversationProtocolError("cannot validate history with pending action")
        return validate_canonical_conversation(
            self._messages,
            expected_query=self.query,
            expected_initial_observation=self.initial_observation,
        )


__all__ = [
    "CompactObservationConversation",
    "ConversationProtocolError",
    "DELTA_OPS",
    "OBSERVATION_DELTA_INSTRUCTIONS",
    "OBSERVATION_DELTA_PROTOCOL_KEY",
    "apply_observation_delta",
    "build_environment_delta_message",
    "build_initial_user_message",
    "canonical_assistant_action",
    "canonical_json",
    "diff_observation",
    "extend_system_content",
    "observation_delta",
    "parse_assistant_action",
    "public_action_result_from_receipt",
    "prompt_template_sha256",
    "system_prompt_sha256",
    "validate_canonical_conversation",
]
