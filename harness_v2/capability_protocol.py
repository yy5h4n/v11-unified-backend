"""Manifest-driven public action protocol for Harness V2.

This module is deliberately independent of any simulator or capability family.
The manifest is the authority for devices, capability/operation pairs, parameter
schemas, and supported runtime action kinds.  Observations may narrow device
availability, but can never grant authority absent from the manifest.
"""

from __future__ import annotations

from copy import deepcopy
from math import isfinite
from typing import Any, Iterable, Mapping


WIRE_KINDS = ("act", "install_rule", "cancel_rule", "ask", "wait")


class CapabilityProtocolViolation(ValueError):
    """Raised when a manifest or an agent action fails closed."""


def _fail(message: str) -> None:
    raise CapabilityProtocolViolation(message)


def _catalog(manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = manifest.get("capability_catalog", manifest.get("capabilities"))
    if not isinstance(value, list) or not value:
        _fail("manifest must contain a non-empty capability catalog")
    return value


def _devices(manifest: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    inventory = manifest.get("inventory")
    value = inventory.get("devices") if isinstance(inventory, Mapping) else manifest.get("devices")
    if not isinstance(value, list) or not value:
        _fail("manifest must contain a non-empty device inventory")
    return value


def _supported_kinds(manifest: Mapping[str, Any]) -> set[str]:
    value = manifest.get("action_kinds", manifest.get("allowed_actions", manifest.get("kinds")))
    if not isinstance(value, list) or not value:
        _fail("manifest must declare action_kinds")
    if any(not isinstance(item, str) or item not in WIRE_KINDS for item in value):
        _fail("manifest declares an unknown action kind")
    if len(value) != len(set(value)):
        _fail("manifest action_kinds must be unique")
    return set(value)


def _remaining_questions(budgets: Mapping[str, Any] | None) -> int:
    if not isinstance(budgets, Mapping):
        return 0
    for key in ("remaining_questions", "questions_remaining", "clarification_questions_remaining"):
        if key in budgets:
            value = budgets[key]
            return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0
    maximum = budgets.get("max_questions", 0)
    used = budgets.get("questions_used", budgets.get("clarification_questions_attempted", 0))
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in (maximum, used)):
        return 0
    return max(0, maximum - used)


def _allowed_kinds(manifest: Mapping[str, Any], track: str, budgets: Mapping[str, Any] | None) -> list[str]:
    if track not in {"explicit_profile_control", "interactive_clarification"}:
        _fail(f"unknown track: {track!r}")
    supported = _supported_kinds(manifest)
    track_kinds = manifest.get("track_action_kinds", manifest.get("actions_by_track"))
    if track_kinds is not None:
        if not isinstance(track_kinds, Mapping) or track not in track_kinds:
            _fail("manifest track action policy is incomplete")
        selected = track_kinds[track]
        if isinstance(selected, Mapping):
            selected = selected.get("action_kinds")
        if not isinstance(selected, list) or any(item not in WIRE_KINDS for item in selected):
            _fail("manifest track action policy is invalid")
        supported &= set(selected)
    if track == "explicit_profile_control":
        supported.discard("ask")
    elif not (manifest.get("reply_provider_available") is True and _remaining_questions(budgets) > 0):
        supported.discard("ask")
    return [kind for kind in WIRE_KINDS if kind in supported]


def _operation_name(operation: Mapping[str, Any]) -> str:
    value = operation.get("name", operation.get("operation"))
    if not isinstance(value, str) or not value:
        _fail("every operation must have a non-empty name")
    return value


def _parameters(operation: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = operation.get("parameters", [])
    if isinstance(value, Mapping):
        result = []
        for name, spec in value.items():
            if not isinstance(spec, Mapping):
                _fail("parameter schemas must be objects")
            result.append({"name": name, **spec})
        value = result
    if not isinstance(value, list):
        _fail("operation parameters must be a list or object")
    names: list[str] = []
    for parameter in value:
        if not isinstance(parameter, Mapping) or not isinstance(parameter.get("name"), str):
            _fail("every parameter must have a name")
        names.append(parameter["name"])
    if len(names) != len(set(names)):
        _fail("parameter names must be unique within an operation")
    return value


def _indices(manifest: Mapping[str, Any]) -> tuple[dict[str, Mapping[str, Any]], dict[tuple[str, str], Mapping[str, Any]]]:
    operations: dict[tuple[str, str], Mapping[str, Any]] = {}
    capability_ids: set[str] = set()
    for capability in _catalog(manifest):
        if not isinstance(capability, Mapping):
            _fail("capabilities must be objects")
        capability_id = capability.get("capability_id", capability.get("id"))
        if not isinstance(capability_id, str) or not capability_id or capability_id in capability_ids:
            _fail("capability ids must be non-empty and unique")
        capability_ids.add(capability_id)
        declared_operations = capability.get("operations")
        if not isinstance(declared_operations, list) or not declared_operations:
            _fail("every capability must declare operations")
        for operation in declared_operations:
            if not isinstance(operation, Mapping):
                _fail("operations must be objects")
            name = _operation_name(operation)
            key = (capability_id, name)
            if key in operations:
                _fail("operation names must be unique within a capability")
            _parameters(operation)
            operations[key] = operation

    devices: dict[str, Mapping[str, Any]] = {}
    for device in _devices(manifest):
        if not isinstance(device, Mapping):
            _fail("devices must be objects")
        device_id = device.get("device_id")
        capabilities = device.get("capabilities")
        if not isinstance(device_id, str) or not device_id or device_id in devices:
            _fail("device ids must be non-empty and unique")
        if not isinstance(capabilities, list) or any(item not in capability_ids for item in capabilities):
            _fail("device references an unknown capability")
        devices[device_id] = device
    return devices, operations


def _observed_availability(observation: Mapping[str, Any] | None) -> dict[str, str]:
    if not isinstance(observation, Mapping):
        return {}
    inventory = observation.get("inventory")
    raw = inventory.get("devices") if isinstance(inventory, Mapping) else observation.get("devices")
    result: dict[str, str] = {}
    if isinstance(raw, list):
        candidates: Iterable[Any] = raw
    elif isinstance(raw, Mapping):
        candidates = raw.values()
    else:
        return result
    for item in candidates:
        if isinstance(item, Mapping) and isinstance(item.get("device_id"), str):
            availability = item.get("availability")
            if isinstance(availability, str):
                result[item["device_id"]] = availability
    return result


def _json_type(parameter: Mapping[str, Any]) -> dict[str, Any]:
    value_type = parameter.get("type")
    if value_type not in {"number", "integer", "string", "boolean"}:
        _fail(f"unsupported parameter type: {value_type!r}")
    schema: dict[str, Any] = {"type": value_type}
    if "minimum" in parameter:
        schema["minimum"] = parameter["minimum"]
    elif "min" in parameter:
        schema["minimum"] = parameter["min"]
    if "maximum" in parameter:
        schema["maximum"] = parameter["maximum"]
    elif "max" in parameter:
        schema["maximum"] = parameter["max"]
    allowed = parameter.get("allowed_values", parameter.get("enum"))
    if allowed is not None:
        if not isinstance(allowed, list) or not allowed:
            _fail("allowed_values must be a non-empty list")
        schema["enum"] = deepcopy(allowed)
    return schema


def _command_schema(manifest: Mapping[str, Any], observation: Mapping[str, Any] | None) -> dict[str, Any]:
    devices, operations = _indices(manifest)
    observed = _observed_availability(observation)
    branches = []
    for device_id, device in devices.items():
        if device.get("availability", "available") != "available" or observed.get(device_id, "available") != "available":
            continue
        for capability_id in device["capabilities"]:
            for (candidate_capability, operation_name), operation in operations.items():
                if candidate_capability != capability_id:
                    continue
                parameters = _parameters(operation)
                required = [item["name"] for item in parameters if item.get("required") is True]
                branches.append({
                    "type": "object",
                    "required": ["device_id", "capability", "operation", "parameters"],
                    "additionalProperties": False,
                    "properties": {
                        "device_id": {"const": device_id},
                        "capability": {"const": capability_id},
                        "operation": {"const": operation_name},
                        "parameters": {
                            "type": "object",
                            "required": required,
                            "additionalProperties": False,
                            "properties": {item["name"]: _json_type(item) for item in parameters},
                        },
                    },
                })
    if not branches:
        _fail("manifest exposes no executable device operation")
    return {"oneOf": branches}


def build_action_protocol(
    manifest: Mapping[str, Any],
    observation: Mapping[str, Any],
    track: str,
    budgets: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Build the public runtime action schema from backend authority only."""

    allowed = _allowed_kinds(manifest, track, budgets)
    command = _command_schema(manifest, observation)
    branches: list[dict[str, Any]] = []
    for kind in allowed:
        if kind == "wait":
            branches.extend(_wait_schema_branches())
        elif kind == "act":
            branches.append({"type": "object", "required": ["kind", "commands"], "additionalProperties": False, "properties": {"kind": {"const": "act"}, "commands": {"type": "array", "minItems": 1, "items": command}}})
        elif kind == "cancel_rule":
            branches.append({"type": "object", "required": ["kind", "rule_id"], "additionalProperties": False, "properties": {"kind": {"const": "cancel_rule"}, "rule_id": {"type": "string", "minLength": 1}}})
        elif kind == "ask":
            branches.append({"type": "object", "required": ["kind", "question"], "additionalProperties": False, "properties": {"kind": {"const": "ask"}, "question": {"type": "string", "minLength": 1}}})
        else:
            rule = {
                "type": "object",
                "required": ["rule_id", "fire_at_step", "release_at_step", "commands", "release_commands"],
                "additionalProperties": False,
                "properties": {
                    "rule_id": {"type": "string", "minLength": 1},
                    "fire_at_step": {"type": "integer", "minimum": 0},
                    "release_at_step": {"type": "integer", "minimum": 1},
                    "commands": {"type": "array", "minItems": 1, "items": command},
                    "release_commands": {"type": "array", "minItems": 1, "items": command},
                },
            }
            branches.append({"type": "object", "required": ["kind", "rule"], "additionalProperties": False, "properties": {"kind": {"const": "install_rule"}, "rule": rule}})
    return {
        "allowed_actions": allowed,
        "json_schema": {"oneOf": branches},
        "authority": "backend_capability_manifest",
        "observed_device_ids": sorted(_observed_availability(observation)),
    }


def _wait_schema_branches() -> list[dict[str, Any]]:
    return [
        {"type": "object", "required": ["kind", "mode", "duration_seconds"], "additionalProperties": False, "properties": {"kind": {"const": "wait"}, "mode": {"const": "for"}, "duration_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": 604800}}},
        {"type": "object", "required": ["kind", "mode", "timestamp"], "additionalProperties": False, "properties": {"kind": {"const": "wait"}, "mode": {"const": "until"}, "timestamp": {"type": "string", "minLength": 1}}},
        {"type": "object", "required": ["kind", "mode", "event_filter", "timeout_seconds"], "additionalProperties": False, "properties": {"kind": {"const": "wait"}, "mode": {"const": "until_event"}, "event_filter": {"type": "object", "minProperties": 1, "additionalProperties": {"type": ["string", "number", "integer", "boolean", "null"]}}, "timeout_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": 604800}}},
    ]


def _strict_value(value: Any, parameter: Mapping[str, Any], path: str) -> None:
    value_type = parameter.get("type")
    valid = {
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "string": lambda item: isinstance(item, str),
        "boolean": lambda item: isinstance(item, bool),
    }.get(value_type)
    if valid is None or not valid(value):
        _fail(f"{path} has wrong type")
    if value_type == "number" and not isfinite(value):
        _fail(f"{path} must be finite")
    minimum = parameter.get("minimum", parameter.get("min"))
    maximum = parameter.get("maximum", parameter.get("max"))
    if minimum is not None and value < minimum:
        _fail(f"{path} is below minimum")
    if maximum is not None and value > maximum:
        _fail(f"{path} is above maximum")
    allowed = parameter.get("allowed_values", parameter.get("enum"))
    if allowed is not None and not any(type(value) is type(candidate) and value == candidate for candidate in allowed):
        _fail(f"{path} is not an allowed value")


def _validate_commands(commands: Any, manifest: Mapping[str, Any], observation: Mapping[str, Any] | None, path: str) -> None:
    if not isinstance(commands, list) or not commands:
        _fail(f"{path} must be a non-empty list")
    devices, operations = _indices(manifest)
    observed = _observed_availability(observation)
    seen_devices: set[str] = set()
    for index, command in enumerate(commands):
        command_path = f"{path}[{index}]"
        if not isinstance(command, Mapping) or set(command) != {"device_id", "capability", "operation", "parameters"}:
            _fail(f"{command_path} has invalid fields")
        device_id = command["device_id"]
        if not isinstance(device_id, str) or device_id not in devices:
            _fail(f"{command_path} references an unknown device")
        if device_id in seen_devices:
            _fail(f"{path} contains duplicate device commands")
        seen_devices.add(device_id)
        device = devices[device_id]
        if device.get("availability", "available") != "available" or observed.get(device_id, "available") != "available":
            _fail(f"{command_path} references an unavailable device")
        capability = command["capability"]
        operation_name = command["operation"]
        if not isinstance(capability, str) or capability not in device["capabilities"]:
            _fail(f"{command_path} forges device capability authority")
        operation = operations.get((capability, operation_name))
        if operation is None:
            _fail(f"{command_path} references an unknown operation")
        supplied = command["parameters"]
        if not isinstance(supplied, Mapping):
            _fail(f"{command_path}.parameters must be an object")
        declared = {item["name"]: item for item in _parameters(operation)}
        required = {name for name, item in declared.items() if item.get("required") is True}
        if not required <= set(supplied) or not set(supplied) <= set(declared):
            _fail(f"{command_path}.parameters has missing or forged fields")
        for name, value in supplied.items():
            _strict_value(value, declared[name], f"{command_path}.parameters.{name}")


def validate_action_against_manifest(
    action: Any,
    manifest: Mapping[str, Any],
    observation: Mapping[str, Any] | None = None,
    track: str = "explicit_profile_control",
    budgets: Mapping[str, Any] | None = None,
    current_step: int | None = None,
    horizon: int | None = None,
) -> None:
    """Fail closed unless *action* is authorized and well typed by manifest."""

    if not isinstance(action, Mapping) or not isinstance(action.get("kind"), str):
        _fail("action must be an object with a kind")
    kind = action["kind"]
    if kind not in _allowed_kinds(manifest, track, budgets):
        _fail(f"action kind {kind!r} is not exposed on this track")
    required_fields = {
        "act": {"kind", "commands"},
        "wait": None,
        "install_rule": {"kind", "rule"},
        "cancel_rule": {"kind", "rule_id"},
        "ask": {"kind", "question"},
    }[kind]
    if kind != "wait" and set(action) != required_fields:
        _fail(f"{kind} has invalid fields")
    if kind == "act":
        _validate_commands(action["commands"], manifest, observation, "commands")
    elif kind == "cancel_rule":
        if not isinstance(action["rule_id"], str) or not action["rule_id"].strip():
            _fail("cancel_rule.rule_id must be a non-empty string")
    elif kind == "ask":
        if not isinstance(action["question"], str) or not action["question"].strip():
            _fail("ask.question must be a non-empty string")
    elif kind == "wait":
        mode = action.get("mode")
        expected = {
            "for": {"kind", "mode", "duration_seconds"},
            "until": {"kind", "mode", "timestamp"},
            "until_event": {"kind", "mode", "event_filter", "timeout_seconds"},
        }.get(mode)
        if expected is None or set(action) != expected:
            _fail("wait must select exactly one of for, until, or until_event")
        if mode == "for":
            value = action["duration_seconds"]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value) or not 0 < value <= 604800:
                _fail("wait.duration_seconds is invalid")
        elif mode == "until":
            if not isinstance(action["timestamp"], str) or not action["timestamp"].strip():
                _fail("wait.timestamp is invalid")
        else:
            event_filter, timeout = action["event_filter"], action["timeout_seconds"]
            if not isinstance(event_filter, Mapping) or not event_filter or any(not isinstance(key, str) or not key or isinstance(value, (Mapping, list)) for key, value in event_filter.items()):
                _fail("wait.event_filter is invalid")
            if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not isfinite(timeout) or not 0 < timeout <= 604800:
                _fail("wait.timeout_seconds is invalid")
    elif kind == "install_rule":
        rule = action["rule"]
        expected = {"rule_id", "fire_at_step", "release_at_step", "commands", "release_commands"}
        if not isinstance(rule, Mapping) or set(rule) != expected:
            _fail("install_rule.rule has invalid fields")
        if not isinstance(rule["rule_id"], str) or not rule["rule_id"].strip():
            _fail("rule_id must be a non-empty string")
        fire = rule["fire_at_step"]
        release = rule["release_at_step"]
        if any(not isinstance(value, int) or isinstance(value, bool) for value in (fire, release, current_step, horizon)):
            _fail("rule validation requires integer fire/release/current_step/horizon")
        if current_step < 0 or horizon <= current_step or not (current_step < fire < release <= horizon):
            _fail("rule fire/release must be ordered strictly inside the remaining horizon")
        _validate_commands(rule["commands"], manifest, observation, "rule.commands")
        _validate_commands(rule["release_commands"], manifest, observation, "rule.release_commands")
