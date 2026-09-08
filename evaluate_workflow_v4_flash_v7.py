#!/usr/bin/env python3
"""V7 native-tool conversation runner for the frozen v5 15-Episode smoke set.

V7 keeps the household agent's complete *canonical* public conversation:
system rules, one initial user request, then alternating assistant tool calls and
tool observations.  Provider prose/reasoning is never retained.  Native tool
support is a hard precondition; this module has no text/envelope fallback.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import evaluate_workflow_v4_flash_v4 as v4
import evaluate_workflow_v4_flash_v5 as v5
from evaluate_harness_v2_v4_flash import APIError, DEFAULT_BASE, DEFAULT_MODEL, _parse_strict_json
from harness_v2.core import Harness
from harness_v2.temporal_home_evaluator import evaluate_temporal_home


ROOT = Path(__file__).resolve().parent
RELEASE_DIR = v5.RELEASE_DIR
DEFAULT_OUTPUT = ROOT / "runs/workflow_v4_flash_non_notification_15_v7/report.json"
SAMPLE_INDICES = v5.SAMPLE_INDICES
SAMPLE_EPISODE_IDS = v5.SAMPLE_EPISODE_IDS
SCHEMA_VERSION = "temporal-home-v4-flash-notification-only-filtered-smoke15-v7-native-tools-history"
TOOL_NAMES = {
    "act": "submit_act",
    "wait_for": "submit_wait_for",
    "wait_until": "submit_wait_until",
    "wait_until_event": "submit_wait_until_event",
    "cancel_rule": "submit_cancel_rule",
    "install_rule": "submit_install_rule",
}

# Explicit checklist used by review/tests so a context rewrite cannot silently
# strand a public v6 contract.  The value names the concrete v7 destination.
MIGRATION_MANIFEST = {
    "frozen_v5_sample": "SAMPLE_INDICES,SAMPLE_EPISODE_IDS,v5.assert_release_and_sample_frozen",
    "household_agent_rules": "V7_HOUSEHOLD_AGENT_RULES,system.role_directive",
    "public_action_kinds": "six request tools covering act,wait,cancel_rule,install_rule",
    "action_schemas": "build_native_tools strict per-branch function parameter schemas",
    "action_examples": "request tool descriptions (transport examples removed from system)",
    "device_interfaces": "operation-specific command anyOf branches in request tools",
    "event_schemas": "query-scoped typed event_filter schema in submit_wait_until_event",
    "public_profile": "initial user.public_preferences",
    "state": "initial user.initial_observation and every tool.current_observation",
    "events": "current_observation.events",
    "active_rules": "current_observation.active_rule_ids",
    "metrics": "episode and aggregate split token/action metrics",
    "receipts": "tool.action_result and public_action_records",
    "checkpoint_invariants": "validate_result_prefix,exact rebuild,atomic replace,completed refusal",
    "private_data_exclusion": "no contract/scenario/reference/evaluator/future schedule in request context",
    "raw_thinking_exclusion": "response hash/byte length only; canonical action retained",
    "native_tools_gate": "verified probe evidence with deterministic request hash required; no fallback",
}

V7_HOUSEHOLD_AGENT_RULES = (
    "You are responsible for carrying out the user's standing household request. "
    "Observe public events and device state, act only when justified, and keep monitoring until the episode ends. "
    "Submit exactly one next action by calling exactly one provided household action tool. "
    "Do not answer in prose. Use only the device operations and event fields represented by the tools. "
    "After every action, re-observe the fresh complete public observation supplied by the tool before deciding again. "
    "After a rejected action, do not repeat the identical action. An empty act command list is legal but changes nothing. "
    "For install_rule, fire_at_step is an absolute step strictly greater than the current step; compute it from the current "
    "step and tick_seconds. release_at_step, when set, must be later than fire_at_step and requires non-empty release_commands. "
    "For wait, choose a bounded duration; until uses a future RFC3339 timestamp and until_event uses only filterable scalar "
    "fields from the observable event interface. Examples are illustrative; never copy their times or steps."
)

TOOL_CHOICE = "required"


def _strict_parameters_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Normalize a published object schema to OpenAI strict-tool requirements."""
    result = deepcopy(schema)
    if result.get("type") != "object":
        raise ValueError("device parameters_schema must be an object")
    properties = result.setdefault("properties", {})
    result.pop("maxProperties", None)
    result["additionalProperties"] = False
    result["required"] = sorted(properties)
    return result


def _command_schema(device_interfaces: list[dict[str, Any]]) -> dict[str, Any]:
    branches = []
    for device in device_interfaces:
        for interface in device["interfaces"]:
            branches.append({
                "type": "object", "additionalProperties": False,
                "required": ["device_id", "capability", "operation", "parameters"],
                "properties": {
                    "device_id": {"type": "string", "enum": [device["device_id"]]},
                    "capability": {"type": "string", "enum": [interface["capability"]]},
                    "operation": {"type": "string", "enum": [interface["operation"]]},
                    "parameters": _strict_parameters_schema(interface["parameters_schema"]),
                },
            })
    if not branches:
        raise ValueError("Episode exposes no actionable device interfaces")
    return {"anyOf": branches}


def _event_filter_schema(event_catalog: list[dict[str, Any]]) -> dict[str, Any]:
    scalar_types = {"string": "string", "integer": "integer", "number": "number", "boolean": "boolean"}
    branches = []
    for event in event_catalog:
        properties: dict[str, Any] = {}
        required = []
        for field in event["filterable_scalar_fields"]:
            name, kind = field["name"], scalar_types[field["type"]]
            required.append(name)
            if name == "type":
                properties[name] = {"type": "string", "enum": [event["event_type"]]}
            else:
                properties[name] = {"type": [kind, "null"]}
        branches.append({"type": "object", "additionalProperties": False,
                         "required": required, "properties": properties})
    if not branches:
        raise ValueError("Episode exposes no observable event types")
    return {"anyOf": branches}


def _tool(name: str, description: str, parameters: dict[str, Any]) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": description,
                                                "strict": True, "parameters": parameters}}


def build_native_tools(device_interfaces: list[dict[str, Any]], selected_event_types: tuple[str, ...]) -> list[dict[str, Any]]:
    """Build Episode-specific strict tools carrying all public capabilities."""
    selected = set(selected_event_types)
    catalog = [deepcopy(row) for row in v4.OBSERVABLE_EVENT_CATALOG if row["event_type"] in selected]
    command = _command_schema(device_interfaces)
    object_base = {"type": "object", "additionalProperties": False}
    tools = [
        _tool(TOOL_NAMES["act"], "Execute zero or more published device commands.", {
            **object_base, "required": ["kind", "commands"],
            "properties": {"kind": {"type": "string", "enum": ["act"]},
                           "commands": {"type": "array", "items": command}},
        }),
        _tool(TOOL_NAMES["wait_for"], "Advance simulated time for a bounded duration.", {
            **object_base, "required": ["kind", "mode", "duration_seconds"],
            "properties": {"kind": {"type": "string", "enum": ["wait"]},
                           "mode": {"type": "string", "enum": ["for"]},
                           "duration_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": 604800}},
        }),
        _tool(TOOL_NAMES["wait_until"], "Advance until a future RFC3339 timestamp.", {
            **object_base, "required": ["kind", "mode", "timestamp"],
            "properties": {"kind": {"type": "string", "enum": ["wait"]},
                           "mode": {"type": "string", "enum": ["until"]},
                           "timestamp": {"type": "string", "minLength": 1}},
        }),
        _tool(TOOL_NAMES["wait_until_event"], "Advance until a published event filter matches or timeout.", {
            **object_base, "required": ["kind", "mode", "event_filter", "timeout_seconds"],
            "properties": {"kind": {"type": "string", "enum": ["wait"]},
                           "mode": {"type": "string", "enum": ["until_event"]},
                           "event_filter": _event_filter_schema(catalog),
                           "timeout_seconds": {"type": "number", "exclusiveMinimum": 0, "maximum": 604800}},
        }),
        _tool(TOOL_NAMES["cancel_rule"], "Cancel a rule ID visible in active_rule_ids.", {
            **object_base, "required": ["kind", "rule_id"],
            "properties": {"kind": {"type": "string", "enum": ["cancel_rule"]},
                           "rule_id": {"type": "string", "minLength": 1}},
        }),
        _tool(TOOL_NAMES["install_rule"], "Install a future rule using published device commands.", {
            **object_base, "required": ["kind", "rule"],
            "properties": {"kind": {"type": "string", "enum": ["install_rule"]}, "rule": {
                "type": "object", "additionalProperties": False,
                "required": ["rule_id", "fire_at_step", "release_at_step", "commands", "release_commands"],
                "properties": {"rule_id": {"type": "string", "minLength": 1}, "fire_at_step": {"type": "integer"},
                               "release_at_step": {"type": ["integer", "null"]},
                               "commands": {"type": "array", "minItems": 1, "items": command},
                               "release_commands": {"type": "array", "items": command}},
            }},
        }),
    ]
    return tools


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def build_system_content() -> str:
    """Static system role: household-agent rules only; capabilities live in tools."""
    return V7_HOUSEHOLD_AGENT_RULES


def build_initial_user_message(view: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "original_query": deepcopy(view["query"]),
        "public_preferences": deepcopy(view["public_profile"]),
        "initial_observation": deepcopy(view["observation"]),
    }
    return {"role": "user", "content": _canonical_json(payload)}


def build_tool_observation(tool_call_id: str, feedback: Any, observation: dict[str, Any]) -> dict[str, Any]:
    payload = {"action_result": deepcopy(feedback), "current_observation": deepcopy(observation)}
    return {"role": "tool", "tool_call_id": tool_call_id, "content": _canonical_json(payload)}


def canonical_assistant_tool_call(tool_call_id: str, action: dict[str, Any], tool_name: str | None = None) -> dict[str, Any]:
    tool_name = tool_name or tool_name_for_action(action)
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{
            "id": tool_call_id,
            "type": "function",
            "function": {"name": tool_name, "arguments": _canonical_json(action)},
        }],
    }


def response_fingerprint(response: dict[str, Any]) -> tuple[int, str]:
    """Fingerprint the ephemeral provider message without retaining its content."""
    raw = _canonical_json(response.get("message")).encode("utf-8")
    return len(raw), hashlib.sha256(raw).hexdigest()


def tool_name_for_action(action: dict[str, Any]) -> str:
    kind = action.get("kind")
    if kind == "wait":
        return TOOL_NAMES.get("wait_" + str(action.get("mode")), "")
    return TOOL_NAMES.get(str(kind), "")


def _strip_null_event_fields(action: dict[str, Any]) -> dict[str, Any]:
    value = deepcopy(action)
    if value.get("kind") == "wait" and value.get("mode") == "until_event" and isinstance(value.get("event_filter"), dict):
        value["event_filter"] = {key: item for key, item in value["event_filter"].items() if item is not None}
    return value


def parse_native_tool_response(response: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Return the call id and strict action; provider prose is ignored."""
    message = response.get("message")
    if not isinstance(message, dict):
        raise ValueError("missing_assistant_message")
    calls = message.get("tool_calls")
    if not isinstance(calls, list) or len(calls) != 1:
        raise ValueError("exactly_one_native_tool_call_required")
    call = calls[0]
    if not isinstance(call, dict) or call.get("type") != "function":
        raise ValueError("malformed_native_tool_call")
    call_id = call.get("id")
    function = call.get("function")
    if not isinstance(call_id, str) or not call_id or not isinstance(function, dict):
        raise ValueError("malformed_native_tool_call")
    tool_name = function.get("name")
    if tool_name not in set(TOOL_NAMES.values()):
        raise ValueError("unexpected_native_tool_name")
    arguments = function.get("arguments")
    if not isinstance(arguments, str):
        raise ValueError("tool_arguments_not_text")
    action = _strip_null_event_fields(_parse_strict_json(arguments))
    if tool_name_for_action(action) != tool_name:
        raise ValueError("native_tool_action_branch_mismatch")
    return call_id, tool_name, action


def usage_token_record(usage: Any) -> dict[str, Any]:
    usage = usage if isinstance(usage, dict) else {}
    present = {key: key in usage for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
    values = {key: int(usage.get(key) or 0) if present[key] else 0 for key in present}
    if any(value < 0 for value in values.values()):
        raise ValueError("negative_provider_token_count")
    all_present = all(present.values())
    return {
        "prompt_tokens": values["prompt_tokens"],
        "completion_tokens": values["completion_tokens"],
        "output_tokens": values["completion_tokens"],
        "provider_total_tokens": values["total_tokens"],
        "provider_prompt_tokens_present": present["prompt_tokens"],
        "provider_completion_tokens_present": present["completion_tokens"],
        "provider_total_tokens_present": present["total_tokens"],
        "provider_total_tokens_matches_sum": (
            values["total_tokens"] == values["prompt_tokens"] + values["completion_tokens"]
            if all_present else None
        ),
    }


class NativeToolsChatClient:
    """Minimal OpenAI-compatible client that always sends native tools."""

    def __init__(self, base_url: str = DEFAULT_BASE, model: str = DEFAULT_MODEL, api_key: str | None = None,
                 retries: int = 2, timeout: int = 120):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key or os.environ.get("AIGC_API_KEY")
        self.retries = retries
        self.timeout = timeout
        if not self.api_key:
            raise RuntimeError("AIGC_API_KEY is required")

    def complete(self, messages: list[dict[str, Any]], *, tools: list[dict[str, Any]], tool_choice: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps({
            "model": self.model, "messages": messages, "tools": tools, "tool_choice": tool_choice,
            "parallel_tool_calls": False, "temperature": 0, "max_tokens": 512,
        }, ensure_ascii=False, allow_nan=False).encode("utf-8")
        for attempt in range(self.retries + 1):
            started = time.perf_counter()
            try:
                request = Request(self.base_url + "/chat/completions", data=body, method="POST", headers={
                    "Content-Type": "application/json", "Authorization": "Bearer " + self.api_key,
                })
                with urlopen(request, timeout=self.timeout) as incoming:
                    payload = json.loads(incoming.read().decode("utf-8"))
                message = (payload.get("choices") or [{}])[0].get("message")
                return {"message": message, "usage": payload.get("usage") or {},
                        "latency_ms": (time.perf_counter() - started) * 1000}
            except HTTPError as exc:
                if exc.code not in {429, 500, 502, 503, 504} or attempt >= self.retries:
                    raise APIError(f"http_{exc.code}") from exc
            except (URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
                if attempt >= self.retries:
                    raise APIError(type(exc).__name__.lower()) from exc
            time.sleep(min(8.0, 0.5 * (2 ** attempt)))
        raise APIError("retry_exhausted")


class NativeToolHistoryPolicy:
    def __init__(self, client: Any, *, native_tools_verified: bool):
        if native_tools_verified is not True:
            raise RuntimeError("v7 requires explicit verified native-tools evidence; fallback is forbidden")
        self.client = client
        self.calls = self.api_successes = self.parsed_json_actions = 0
        self.prompt_tokens = self.completion_tokens = self.output_tokens = self.provider_total_tokens = 0
        self.provider_total_tokens_present_calls = self.provider_total_tokens_mismatch_calls = 0
        self.latency_ms = 0.0
        self.errors: Counter[str] = Counter()
        self.output_records: list[dict[str, Any]] = []
        self.request_log: list[list[dict[str, Any]]] = []
        self.history: list[dict[str, Any]] = []
        self.pending_tool_call_id: str | None = None
        self.system_content: str | None = None
        self.system_prompt_sha256: str | None = None
        self.native_tools: list[dict[str, Any]] | None = None
        self.native_tools_sha256: str | None = None
        self.selected_event_types: tuple[str, ...] = ()
        self.selection_matched_keywords: tuple[str, ...] = ()

    def _initialize(self, view: dict[str, Any]) -> None:
        selected, matched = v4.select_event_types_for_query(view["query"])
        system = build_system_content()
        tools = build_native_tools(v4.device_interfaces_from_observation(view["observation"]), selected)
        self.system_content = system
        self.system_prompt_sha256 = hashlib.sha256(system.encode()).hexdigest()
        self.native_tools = tools
        self.native_tools_sha256 = hashlib.sha256(_canonical_json(tools).encode()).hexdigest()
        self.selected_event_types, self.selection_matched_keywords = selected, matched
        self.history = [{"role": "system", "content": system}, build_initial_user_message(view)]

    def decide(self, view: dict[str, Any]) -> dict[str, Any]:
        if self.system_content is None:
            self._initialize(view)
            if view.get("last_feedback") is not None:
                raise RuntimeError("initial decision unexpectedly has prior feedback")
        else:
            selected, _ = v4.select_event_types_for_query(view["query"])
            candidate_system = build_system_content()
            candidate_tools = build_native_tools(v4.device_interfaces_from_observation(view["observation"]), selected)
            if candidate_system != self.system_content or candidate_tools != self.native_tools:
                raise RuntimeError("Episode system/tools prefix must be byte-identical across calls")
            if self.pending_tool_call_id is None:
                raise RuntimeError("missing prior tool_call_id")
            self.history.append(build_tool_observation(self.pending_tool_call_id, view.get("last_feedback"), view["observation"]))

        self.calls += 1
        messages = deepcopy(self.history)
        self.request_log.append(deepcopy(messages))
        record: dict[str, Any] = {
            "call_index": self.calls - 1, "tool_call_id": None, "canonical_action": None,
            "raw_response_sha256": None, "raw_response_byte_length": 0, "usage": None, "protocol_error": None,
        }
        try:
            response = self.client.complete(messages, tools=deepcopy(self.native_tools), tool_choice=deepcopy(TOOL_CHOICE))
            self.api_successes += 1
            usage = usage_token_record(response.get("usage"))
            record["usage"] = usage
            for name in ("prompt_tokens", "completion_tokens", "output_tokens", "provider_total_tokens"):
                setattr(self, name, getattr(self, name) + usage[name])
            if usage["provider_total_tokens_present"]:
                self.provider_total_tokens_present_calls += 1
                if usage["provider_total_tokens_matches_sum"] is False:
                    self.provider_total_tokens_mismatch_calls += 1
            self.latency_ms += float(response.get("latency_ms", 0.0))
            byte_length, digest = response_fingerprint(response)
            record.update({"raw_response_sha256": digest, "raw_response_byte_length": byte_length})
            call_id, tool_name, action = parse_native_tool_response(response)
            record.update({"tool_call_id": call_id, "canonical_action": deepcopy(action),
                           "raw_response_sha256": digest, "raw_response_byte_length": byte_length})
            if any(existing.get("tool_calls", [{}])[0].get("id") == call_id for existing in self.history if existing.get("role") == "assistant"):
                raise ValueError("duplicate_tool_call_id")
            self.history.append(canonical_assistant_tool_call(call_id, action, tool_name))
            self.pending_tool_call_id = call_id
            self.parsed_json_actions += 1
            return action
        except Exception as exc:
            error = f"{type(exc).__name__}:{exc}"
            record["protocol_error"] = error
            self.errors[error] += 1
            return {"kind": "invalid_model_output"}
        finally:
            self.output_records.append(record)

    def finalize(self, public_trace: tuple[dict[str, Any], ...]) -> None:
        """Pair the final assistant action with terminal/error feedback and observation."""
        if not self.history or self.history[-1].get("role") != "assistant":
            return
        observations = [row["value"] for row in public_trace if row.get("type") == "observation"]
        if not observations or self.pending_tool_call_id is None:
            raise RuntimeError("cannot finalize canonical conversation without observation/call id")
        actions = [row for row in public_trace if row.get("type") == "action"]
        if actions:
            feedback = deepcopy(actions[-1].get("feedback"))
        else:
            errors = [row for row in public_trace if row.get("type") in {"protocol_error", "backend_error"}]
            feedback = {"status": "rejected", "error_code": "PROTOCOL_ERROR",
                        "message": errors[-1].get("message") if errors else "action not executed"}
        self.history.append(build_tool_observation(self.pending_tool_call_id, feedback, observations[-1]))


def prompt_template_sha256() -> str:
    value = {"rules": V7_HOUSEHOLD_AGENT_RULES, "tool_builder_version": 2,
             "event_catalog": v4.OBSERVABLE_EVENT_CATALOG, "migration_manifest": MIGRATION_MANIFEST}
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


@lru_cache(maxsize=None)
def expected_episode_prompt(index: int) -> tuple[str, tuple[str, ...]]:
    public, _, spec, backend = v4.load_temporal_episode(index, RELEASE_DIR)
    observation = backend.reset(spec).public_observation
    selected, _ = v4.select_event_types_for_query(public["query"])
    return hashlib.sha256(build_system_content().encode()).hexdigest(), selected


@lru_cache(maxsize=None)
def expected_episode_tools_sha256(index: int) -> str:
    public, _, spec, backend = v4.load_temporal_episode(index, RELEASE_DIR)
    observation = backend.reset(spec).public_observation
    selected, _ = v4.select_event_types_for_query(public["query"])
    tools = build_native_tools(v4.device_interfaces_from_observation(observation), selected)
    return hashlib.sha256(_canonical_json(tools).encode()).hexdigest()


def evaluate_one(client: Any, index: int, release_dir: Path = RELEASE_DIR, *, native_tools_verified: bool) -> dict[str, Any]:
    public, private, spec, backend = v4.load_temporal_episode(index, release_dir)
    policy = NativeToolHistoryPolicy(client, native_tools_verified=native_tools_verified)
    run = Harness(backend).run_one(spec, policy)
    policy.finalize(run.public_trace)
    score = evaluate_temporal_home(private["contract"]["scenario_type"], run, contract=private["contract"], profile=public["public_profile"])
    action_records = [row for row in run.public_trace if row["type"] in {"action", "protocol_error", "backend_error"}]
    executed = [row for row in action_records if row["type"] == "action"]
    result = {
        "episode_id": spec.episode_id, "query": public["query"], "model": getattr(client, "model", DEFAULT_MODEL),
        "run_status": run.status, "responsibility_success": score["success"],
        "device_command_count": score["device_command_count"], "count_unit": score["count_unit"],
        "calls": policy.calls, "api_successes": policy.api_successes, "parsed_json_actions": policy.parsed_json_actions,
        "harness_action_records": len(executed), "accepted_harness_actions": sum(x["accepted"] is True for x in executed),
        "protocol_error_count": sum(x["type"] == "protocol_error" for x in action_records),
        "backend_error_count": sum(x["type"] == "backend_error" for x in action_records),
        "all_actions_accepted": bool(executed) and all(x["accepted"] is True for x in executed),
        "rejected_action_count": sum(x["accepted"] is not True for x in executed),
        "system_prompt_sha256": policy.system_prompt_sha256, "native_tools_sha256": policy.native_tools_sha256,
        "selected_event_types": list(policy.selected_event_types),
        "selection_matched_keywords": list(policy.selection_matched_keywords),
        "prompt_tokens": policy.prompt_tokens, "completion_tokens": policy.completion_tokens,
        "output_tokens": policy.output_tokens, "provider_total_tokens": policy.provider_total_tokens,
        "provider_total_tokens_present_calls": policy.provider_total_tokens_present_calls,
        "provider_total_tokens_mismatch_calls": policy.provider_total_tokens_mismatch_calls,
        "latency_ms": policy.latency_ms, "errors": dict(policy.errors), "trace_digest": run.trace_digest,
        "public_action_records": action_records, "model_output_records": policy.output_records,
        "canonical_conversation": deepcopy(policy.history),
    }
    result["max_consecutive_identical_rejected_action"] = v4.max_consecutive_identical_rejected_action(action_records)
    return result


def metrics_with_device_total(results: list[dict[str, Any]]) -> dict[str, Any]:
    compat = []
    for row in results:
        item = dict(row)
        item["tokens"] = row["provider_total_tokens"]
        compat.append(item)
    metrics = v4.aggregate_results(compat)
    metrics.pop("tokens", None)
    metrics.update({
        "total_device_command_count": sum(x["device_command_count"] for x in results),
        "prompt_tokens": sum(x["prompt_tokens"] for x in results),
        "completion_tokens": sum(x["completion_tokens"] for x in results),
        "output_tokens": sum(x["output_tokens"] for x in results),
        "provider_total_tokens": sum(x["provider_total_tokens"] for x in results),
        "provider_total_tokens_present_calls": sum(x["provider_total_tokens_present_calls"] for x in results),
        "provider_total_tokens_mismatch_calls": sum(x["provider_total_tokens_mismatch_calls"] for x in results),
        "output_token_metric": "completion_tokens",
    })
    return metrics


def validate_result_prefix(results: list[dict[str, Any]]) -> None:
    if [row.get("episode_id") for row in results] != SAMPLE_EPISODE_IDS[:len(results)]:
        raise RuntimeError("checkpoint results are not a prefix of the frozen sample")
    public_rows, _ = v5.assert_release_and_sample_frozen()
    for pos, row in enumerate(results):
        if row.get("model") != DEFAULT_MODEL or row.get("query") != public_rows[pos]["query"]:
            raise RuntimeError("checkpoint model/query does not match frozen Episode")
        expected_hash, selected = expected_episode_prompt(SAMPLE_INDICES[pos])
        if row.get("system_prompt_sha256") != expected_hash or row.get("selected_event_types") != list(selected):
            raise RuntimeError("checkpoint Episode prompt/event selection mismatch")
        if row.get("native_tools_sha256") != expected_episode_tools_sha256(SAMPLE_INDICES[pos]):
            raise RuntimeError("checkpoint Episode native tools SHA256 mismatch")
        _validate_token_row(row)
        actions = validate_canonical_conversation(row.get("canonical_conversation"), expected_query=row["query"],
                                                  expected_episode_id=row["episode_id"])
        outputs = row.get("model_output_records")
        if outputs is not None:
            if not isinstance(outputs, list) or [x["canonical_action"] for x in outputs if x.get("canonical_action") is not None] != actions:
                raise RuntimeError("checkpoint model records do not match canonical conversation")
        public_records = row.get("public_action_records")
        if public_records is not None:
            public_actions = [x["action"] for x in public_records if x.get("type") == "action"]
            if public_actions != actions[:len(public_actions)]:
                raise RuntimeError("checkpoint public actions do not match canonical conversation")


def _validate_token_row(row: dict[str, Any]) -> None:
    names = ("prompt_tokens", "completion_tokens", "output_tokens", "provider_total_tokens",
             "provider_total_tokens_present_calls", "provider_total_tokens_mismatch_calls")
    if any(isinstance(row.get(name), bool) or not isinstance(row.get(name), int) or row[name] < 0 for name in names):
        raise RuntimeError("checkpoint token fields missing or invalid")
    if row["output_tokens"] != row["completion_tokens"]:
        raise RuntimeError("checkpoint output/completion token mismatch")
    calls = row.get("calls")
    if not isinstance(calls, int) or not 0 <= row["provider_total_tokens_mismatch_calls"] <= row["provider_total_tokens_present_calls"] <= calls:
        raise RuntimeError("checkpoint provider token counters invalid")


def validate_canonical_conversation(conversation: Any, *, expected_query: str | None = None,
                                    expected_episode_id: str | None = None) -> list[dict[str, Any]]:
    if not isinstance(conversation, list) or len(conversation) < 2:
        raise RuntimeError("checkpoint canonical conversation is incomplete")
    if [conversation[0].get("role"), conversation[1].get("role")] != ["system", "user"]:
        raise RuntimeError("checkpoint canonical conversation prefix invalid")
    if conversation[0] != {"role": "system", "content": build_system_content()}:
        raise RuntimeError("checkpoint canonical system message invalid")
    try:
        initial = json.loads(conversation[1]["content"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError("checkpoint canonical initial user invalid") from exc
    if set(initial) != {"original_query", "public_preferences", "initial_observation"}:
        raise RuntimeError("checkpoint canonical initial user fields invalid")
    if conversation[1] != {"role": "user", "content": _canonical_json(initial)}:
        raise RuntimeError("checkpoint canonical initial user serialization invalid")
    if expected_query is not None and initial["original_query"] != expected_query:
        raise RuntimeError("checkpoint canonical initial query mismatch")
    if not isinstance(initial["initial_observation"], dict) or (
        expected_episode_id is not None and initial["initial_observation"].get("episode_id") != expected_episode_id
    ):
        raise RuntimeError("checkpoint canonical initial observation mismatch")
    suffix = conversation[2:]
    if len(suffix) % 2:
        raise RuntimeError("checkpoint canonical conversation has unpaired action")
    seen_ids: set[str] = set()
    actions: list[dict[str, Any]] = []
    for offset in range(0, len(suffix), 2):
        assistant, tool = suffix[offset:offset + 2]
        calls = assistant.get("tool_calls") if isinstance(assistant, dict) else None
        if not isinstance(assistant, dict) or assistant.get("role") != "assistant" or assistant.get("content") is not None or not isinstance(calls, list) or len(calls) != 1:
            raise RuntimeError("checkpoint canonical assistant tool call invalid")
        call = calls[0]
        call_id = call.get("id") if isinstance(call, dict) else None
        function = call.get("function") if isinstance(call, dict) else None
        if (not isinstance(call_id, str) or not call_id or call_id in seen_ids or call.get("type") != "function"
                or not isinstance(function, dict) or function.get("name") not in set(TOOL_NAMES.values())):
            raise RuntimeError("checkpoint canonical assistant tool call invalid")
        seen_ids.add(call_id)
        try:
            action = _strip_null_event_fields(_parse_strict_json(function.get("arguments")))
        except (TypeError, ValueError) as exc:
            raise RuntimeError("checkpoint canonical assistant arguments invalid") from exc
        if tool_name_for_action(action) != function["name"]:
            raise RuntimeError("checkpoint canonical assistant branch mismatch")
        if assistant != canonical_assistant_tool_call(call_id, action, function["name"]):
            raise RuntimeError("checkpoint canonical assistant serialization invalid")
        actions.append(action)
        if not isinstance(tool, dict) or tool.get("role") != "tool" or tool.get("tool_call_id") != call_id:
            raise RuntimeError("checkpoint canonical tool pairing invalid")
        try:
            payload = json.loads(tool["content"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError("checkpoint canonical tool content invalid") from exc
        if set(payload) != {"action_result", "current_observation"} or not isinstance(payload["current_observation"], dict):
            raise RuntimeError("checkpoint canonical tool observation invalid")
        if tool != build_tool_observation(call_id, payload["action_result"], payload["current_observation"]):
            raise RuntimeError("checkpoint canonical tool serialization invalid")
    return actions


def build_report(results: list[dict[str, Any]], *, execution_finished: bool,
                 native_tools_evidence: dict[str, Any] | None = None) -> dict[str, Any]:
    validate_result_prefix(results)
    if execution_finished and len(results) != len(SAMPLE_INDICES):
        raise RuntimeError("finished report requires all 15 Episodes")
    evidence = native_tools_evidence
    validate_verified_evidence(evidence)
    return {
        "schema_version": SCHEMA_VERSION, "execution_finished": execution_finished,
        "experiment_config": {
            "model": results[0]["model"] if results else DEFAULT_MODEL, "base_url": DEFAULT_BASE,
            "release_dir": str(RELEASE_DIR.relative_to(ROOT)), "release_public_sha256": v5.PUBLIC_RELEASE_SHA256,
            "release_private_sha256": v5.PRIVATE_RELEASE_SHA256, "planned_indices": SAMPLE_INDICES,
            "completed_indices": SAMPLE_INDICES[:len(results)], "planned_episode_ids": SAMPLE_EPISODE_IDS,
            "sampling_semantics": (
                "manually frozen mechanism-diversity smoke subset; the first release-order Episode from each of "
                "15 distinct responsibilities; not a random sample or estimator of all 21 responsibilities"
            ),
            "scope_semantics": (
                "notification-only responsibilities excluded; mixed responsibilities with independently "
                "evaluated physical/device outcomes remain eligible"
            ),
            "prompt_implementation": "evaluate_workflow_v4_flash_v7", "prompt_template_sha256": prompt_template_sha256(),
            "observable_event_catalog_sha256": v5.V4_EVENT_CATALOG_SHA256,
            "system_and_tools_policy": "episode_static_byte_identical_across_calls",
            "conversation_policy": "initial user then full alternating canonical assistant tool_call/tool observation history",
            "native_tools_evidence": deepcopy(evidence), "text_fallback": "forbidden",
            "raw_model_content_retention": "sha256_and_byte_length_only",
            "token_accounting": {"provider_total_synthesized": False, "missing_provider_fields": "stay_zero",
                                 "provider_total_mismatch": "retained_exactly_and_counted", "generic_tokens_field": "not_emitted"},
            "migration_manifest": deepcopy(MIGRATION_MANIFEST),
        },
        "metrics_all": metrics_with_device_total(results),
        "rejected_action_loop_diagnostics": {x["episode_id"]: x["max_consecutive_identical_rejected_action"] for x in results},
        "episodes": results,
    }


def atomic_write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_checkpoint(path: Path, *, native_tools_evidence: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    if checkpoint.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError("existing output is not a v7 native-tools checkpoint")
    if checkpoint.get("execution_finished") is True:
        raise RuntimeError("refusing to overwrite an already completed v7 report")
    results = checkpoint.get("episodes")
    if not isinstance(results, list):
        raise RuntimeError("existing checkpoint has invalid episodes")
    evidence = native_tools_evidence or checkpoint.get("experiment_config", {}).get("native_tools_evidence")
    if checkpoint != build_report(results, execution_finished=False, native_tools_evidence=evidence):
        raise RuntimeError("existing checkpoint metadata or metrics mismatch")
    return results


def validate_verified_evidence(evidence: Any) -> None:
    import probe_v4_flash_native_tools as probe
    expected_action = {"kind": "wait", "mode": "for", "duration_seconds": 60}
    response_sha = evidence.get("response_message_sha256") if isinstance(evidence, dict) else None
    valid = isinstance(evidence, dict) and (
        evidence.get("verified_native_tools") is True
        and evidence.get("verdict") == "supported"
        and evidence.get("http_status") == 200
        and evidence.get("probe_kind") == probe.PROBE_KIND
        and evidence.get("probe_schema_version") == probe.PROBE_SCHEMA_VERSION
        and evidence.get("base_url") == DEFAULT_BASE
        and evidence.get("model") == DEFAULT_MODEL
        and isinstance(evidence.get("request_sha256"), str)
        and len(evidence["request_sha256"]) == 64
        and all(char in "0123456789abcdef" for char in evidence["request_sha256"])
        and evidence["request_sha256"] == probe.probe_request_sha256(DEFAULT_MODEL)
        and evidence.get("tools_sha256") == probe.probe_tools_sha256()
        and evidence.get("returned_tool_name") == TOOL_NAMES["wait_for"]
        and evidence.get("returned_arguments") == expected_action
        and isinstance(response_sha, str)
        and len(response_sha) == 64
        and all(char in "0123456789abcdef" for char in response_sha)
        and isinstance(evidence.get("response_message_byte_length"), int)
        and not isinstance(evidence.get("response_message_byte_length"), bool)
        and evidence["response_message_byte_length"] > 0
    )
    if not valid:
        raise RuntimeError("probe evidence does not verify frozen base/model native tools")


def load_verified_evidence(path: Path) -> dict[str, Any]:
    evidence = json.loads(path.read_text(encoding="utf-8"))
    validate_verified_evidence(evidence)
    return evidence


def main(args: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE); parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--native-tools-evidence", type=Path, required=True,
                        help="JSON captured from probe_v4_flash_native_tools.py with verified_native_tools=true")
    ns = parser.parse_args(args)
    if ns.base_url != DEFAULT_BASE or ns.model != DEFAULT_MODEL:
        raise ValueError("v7 smoke freezes the default base URL and model")
    evidence = load_verified_evidence(ns.native_tools_evidence)
    v5.assert_release_and_sample_frozen()
    results = load_checkpoint(ns.output, native_tools_evidence=evidence)
    client = NativeToolsChatClient(ns.base_url, ns.model)
    for index in SAMPLE_INDICES[len(results):]:
        results.append(evaluate_one(client, index, RELEASE_DIR, native_tools_verified=True))
        atomic_write_report(ns.output, build_report(results, execution_finished=False, native_tools_evidence=evidence))
    report = build_report(results, execution_finished=True, native_tools_evidence=evidence)
    atomic_write_report(ns.output, report)
    return report


if __name__ == "__main__":
    main()
