#!/usr/bin/env python3
"""TemporalHome V4 Flash paired rerun on the frozen 20 workflow Episodes.

Compatibility-safe successor to the v3 path.  Historical v1/v2/v3 runners and
reports remain frozen.  Differences from v3:

- The system message is Episode-static: it carries the action grammar, static
  examples, protocol rules, the complete device interfaces, and a COMPACT
  observable-event interface whose event types are selected deterministically
  from the public query text ONLY (plus transparent conservative fallback
  groups and the common rule lifecycle events).
- The per-turn user payload contains only the public query/profile, the
  current observation, the previous action, and its result.  Device
  interfaces and event catalogs are never duplicated there.
- The system prefix is byte-identical across every call inside one Episode
  and its SHA256 plus the selected event types are recorded in the report.

No private Episode data (scenario type, contract, required actions,
schedules, evaluator information) is ever read to build or select prompts.
"""

from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from evaluate_harness_v2_v4_flash import ChatClient, DEFAULT_BASE, DEFAULT_MODEL, _parse_strict_json
from evaluate_workflow_formal_release_v1 import DEFAULT_RELEASE, _read_jsonl
from harness_v2.core import Harness
from harness_v2.temporal_home_backend import PUBLIC_ACTION_KINDS, TemporalHomeBackend
from harness_v2.temporal_home_evaluator import evaluate_temporal_home
from harness_v2.trust_evidence import decode_serialization
from validate_formal_dataset_release import validate_release


ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT = ROOT / "runs/workflow_v4_flash_query_scoped_system_prefix_20_v4/report.json"
BASELINE_REPORT_PATH = ROOT / "runs/workflow_v4_flash_complete_prompt_20_v2/report.json"
BASELINE_REPORT_SHA256 = "4663d88a5253a334bb4536086a6a72eee5211a630c5f28c715044638a1da70ba"
PUBLIC_RELEASE_SHA256 = "022ff843ae33c5ca3329f73f66f7548f8342bb989d8b74ec37e6bfb76c6f11b5"
PRIVATE_RELEASE_SHA256 = "fd35435858a33d6855bae94ebe3a396b7e56a619e783350c767417c2eaa09b60"
FIXED_INDICES = [offset * 10 for offset in range(20)]
REQUEST_BODY_FIELDS = ["model", "messages", "temperature", "max_tokens"]

SYSTEM_PROMPT = (
    "You are responsible for carrying out the user's standing household request. "
    "Observe events and device state, act only when justified, and keep monitoring until the episode ends. "
    "Your response is parsed directly as JSON. Think silently. Your entire response MUST start with { and end with }. "
    "The response describes exactly ONE next action: a single JSON object whose top-level \"kind\" is one of exactly four values: "
    "\"act\", \"wait\", \"cancel_rule\", \"install_rule\". "
    "Top-level keys named \"action\", \"action_protocol\", or \"response\" (or any other wrapper) are forbidden: "
    "the object you output IS the action itself. Never output reasoning, a preamble, Markdown, code fences, or trailing text. "
    "Before sending, remove everything outside the single JSON object. "
    "Output exactly one next action per call. After any state-changing action you are re-queried with a fresh observation; "
    "re-observe before deciding the next step."
)
GRAMMAR = [
    '{"kind":"act","commands":[{"device_id":"<published device_id>","capability":"<published capability>","operation":"<published operation>","parameters":{...}}]}',
    '{"kind":"wait","mode":"for","duration_seconds":<number in (0, 604800]>}',
    '{"kind":"wait","mode":"until","timestamp":"<future RFC3339 timestamp computed from the current observation time>"}',
    '{"kind":"wait","mode":"until_event","event_filter":{"<event field>":"<scalar value>"},"timeout_seconds":<number in (0, 604800]>}',
    '{"kind":"cancel_rule","rule_id":"<id from observation.active_rule_ids>"}',
    '{"kind":"install_rule","rule":{"rule_id":"<new unique id>","fire_at_step":<absolute step GREATER than current observation step>,"release_at_step":<absolute step GREATER than fire_at_step, or null>,"commands":[<command>, ...],"release_commands":[<command>, ...]}}',
]
ACTION_EXAMPLES = {
    "act": {"kind": "act", "commands": [{
        "device_id": "notification.service",
        "capability": "notification.send",
        "operation": "send",
        "parameters": {"message": "The laundry cycle has finished.", "channel": "app", "recipients": ["resident.primary"]},
    }]},
    "wait_for": {"kind": "wait", "mode": "for", "duration_seconds": 300},
    "wait_until": {"kind": "wait", "mode": "until", "timestamp": "<computed from the current observation time>"},
    "wait_until_event": {
        "kind": "wait", "mode": "until_event",
        "event_filter": {"type": "laundry_cycle_finished"}, "timeout_seconds": 3600,
    },
    "cancel_rule": {"kind": "cancel_rule", "rule_id": "rule.notify-washer-done"},
    "install_rule": {"kind": "install_rule", "rule": {
        "rule_id": "rule.notify-washer-done", "fire_at_step": "<current observation step + ceil(delay_seconds / tick_seconds)>",
        "release_at_step": None,
        "commands": [{
            "device_id": "notification.service",
            "capability": "notification.send",
            "operation": "send",
            "parameters": {"message": "Washer cycle finished.", "channel": "app", "recipients": ["resident.primary"]},
        }],
        "release_commands": [],
    }},
}
PROTOCOL_RULES = [
    "The first response character must be { and the last must be }.",
    "Return exactly one plain JSON object: one of the four top-level kinds in grammar; no reasoning, markdown, code fence, preamble, or trailing text.",
    'Never wrap the action: top-level keys "action", "action_protocol", or "response" are forbidden; the response object itself IS the action.',
    "Each object field set must match its grammar branch exactly; use only published device interfaces and exact parameter schemas from device_interfaces in this system message.",
    "install_rule.rule.fire_at_step is an absolute step that must be strictly greater than the current observation step: each step advances observation.tick_seconds seconds, so compute fire_at_step = current observation step + ceil(delay_seconds / tick_seconds); never reuse a fixed constant.",
    "All action_examples are illustrative only; every timestamp and step value must be computed from the current observation in the user payload, never copied from an example or from an earlier call.",
    "install_rule.rule.commands must be a non-empty list; release_at_step is optional and release_commands must be non-empty exactly when release_at_step is set.",
    "wait means allowing real simulated time to pass; choose its duration yourself; until_event stops early once an event matching event_filter appears; until requires a future RFC3339 timestamp computed from the current observation time.",
    "The observable_event_interface lists the event types available in this Episode: query-relevant types plus conservative device-completion and exogenous-trigger fallback groups plus the rule lifecycle events. Use only its filterable scalar fields in wait.until_event; it does not reveal which events will occur or when.",
    "After a rejected action, do not repeat the identical action. Re-observe and choose a corrected action or a short wait.",
    "An empty act command list is legal but changes nothing.",
    "Output exactly one next action per call. After any change is accepted you are re-queried with a fresh observation; re-observe before acting again.",
]

# Exhaustive public event vocabulary emitted by WorkflowBackend.  It is global,
# deterministic and identical for every Episode; it contains no occurrence
# schedule, values, scenario membership, contract, or evaluator information.
EVENT_TYPES = tuple(sorted({
    "alarm_acknowledged", "alarm_sounded", "alarm_timeout", "authorized_resident_arrived",
    "authorized_return", "authorized_vehicle_arrived", "away_started", "bathroom_occupied",
    "bathroom_vacated", "bin_collection_due", "coffee_expired", "coffee_ready", "coffee_served",
    "delivery_arrived", "departure_cancelled", "departure_completed", "departure_started",
    "dishwasher_cycle_finished", "emergency_call_accepted", "emergency_call_placed",
    "emergency_detected", "expected_delivery_arrived", "failed_entry_attempt", "fridge_door_alarm",
    "fridge_door_closed", "fridge_door_opened", "front_door_auto_lock_engaged", "front_door_locked",
    "front_door_unlocked", "garage_door_closed", "garage_door_obstruction", "garage_door_opened",
    "intended_wake_time", "inventory_stockout", "keys_present", "laundry_cycle_finished",
    "laundry_loaded", "mail_collected", "mail_delivered", "mailbox_full", "media_resume_attempt",
    "media_started", "media_stopped", "notification_sent", "occupancy_became_zero",
    "occupants_departed", "occupants_returned", "resident_arrived", "rule_cancelled",
    "rule_command_skipped", "rule_fired", "rule_installed", "rule_released", "rule_retired",
    "shower_ended", "shower_started", "stove_attendance_restored", "stove_became_unattended",
    "stove_extinguished", "stove_hazard_shutoff", "supply_delivered", "supply_order_accepted",
    "supply_restocked", "supported_emergency_detected", "television_curfew_ended",
    "television_curfew_started", "toilet_flushed", "toilet_paper_depleted", "toilet_use_finished",
    "trash_bin_emptied", "unauthorized_access_attempt", "unauthorized_vehicle_arrived",
    "unknown_credential_arrived", "unsupported_alarm", "vacuum_cycle_finished", "vacuum_docked",
    "vacuum_low_battery", "vehicle_arrived", "vehicle_parked", "visitor_answered",
    "visitor_interaction_ended", "visitor_missed", "visitor_rang", "visitor_recorded",
    "weekly_cleaning_due",
}))

FIELD_TYPES = {
    "type": "string", "step": "integer", "device_id": "string", "rule_id": "string",
    "visitor_id": "string", "authorized": "boolean", "attempts": "integer",
    "delivery_id": "string", "vehicle_id": "string", "content": "string",
    "content_date": "string", "credential": "string", "item": "string",
    "quantity": "integer", "occurrence_id": "string", "scope": "string",
    "blocked": "boolean", "emergency_type": "string", "supported": "boolean",
    "automatic": "boolean", "message": "string", "channel": "string",
    "sent_at_step": "integer", "service": "string", "called_at_step": "integer",
    "order_id": "string", "supplier": "string", "max_cost": "number",
    "status": "string", "accepted_at_step": "integer", "delivery_remaining_seconds": "integer",
    "keys_present": "boolean",
}

DEVICE_EVENTS = {
    name for name in EVENT_TYPES if any(token in name for token in (
        "alarm", "bathroom", "coffee", "dishwasher", "fridge", "door", "laundry", "mail",
        "media", "notification", "stove", "toilet", "trash", "vacuum", "visitor",
    ))
}
EVENT_EXTRA_FIELDS = {
    "visitor_rang": ("visitor_id", "authorized"), "visitor_recorded": ("visitor_id", "authorized"),
    "delivery_arrived": ("delivery_id", "authorized"), "expected_delivery_arrived": ("delivery_id", "authorized"),
    "resident_arrived": ("credential", "authorized"), "authorized_resident_arrived": ("credential", "authorized"),
    "unknown_credential_arrived": ("credential", "authorized"), "failed_entry_attempt": ("attempts",),
    "vehicle_arrived": ("vehicle_id", "authorized"), "authorized_vehicle_arrived": ("vehicle_id", "authorized"),
    "unauthorized_vehicle_arrived": ("vehicle_id", "authorized"), "vehicle_parked": ("vehicle_id",),
    "media_started": ("content", "content_date"), "media_resume_attempt": ("content",),
    "inventory_stockout": ("item",), "supply_delivered": ("item", "quantity"),
    "weekly_cleaning_due": ("occurrence_id",), "unauthorized_access_attempt": ("scope", "blocked"),
    "departure_started": ("keys_present",), "front_door_locked": ("automatic",),
    "emergency_detected": ("emergency_type", "supported"), "supported_emergency_detected": ("emergency_type", "supported"),
    "unsupported_alarm": ("emergency_type", "supported"),
    "notification_sent": ("message", "channel", "sent_at_step"),
    "emergency_call_placed": ("device_id", "service", "called_at_step"), "emergency_call_accepted": ("device_id", "service", "called_at_step"),
    "rule_command_skipped": ("device_id",), "supply_restocked": ("device_id",),
    "supply_order_accepted": ("order_id", "item", "supplier", "quantity", "max_cost", "status", "accepted_at_step", "delivery_remaining_seconds"),
}

# ---------------------------------------------------------------------------
# Query-scoped event selection (public query text ONLY)
# ---------------------------------------------------------------------------

RULE_EVENT_TYPES = frozenset({
    "rule_cancelled", "rule_command_skipped", "rule_fired", "rule_installed",
    "rule_released", "rule_retired",
})

# Conservative fallback groups: always present so the query-scoped selection
# can never omit cross-cutting device-completion or exogenous-trigger events.
FALLBACK_DEVICE_COMPLETION_EVENTS = frozenset({
    "coffee_expired", "coffee_ready", "dishwasher_cycle_finished",
    "laundry_cycle_finished", "mail_collected", "media_stopped", "shower_ended",
    "stove_hazard_shutoff", "toilet_use_finished", "trash_bin_emptied",
    "vacuum_cycle_finished", "vacuum_docked", "visitor_interaction_ended",
})
FALLBACK_EXOGENOUS_TRIGGER_EVENTS = frozenset({
    "alarm_sounded", "alarm_timeout", "away_started", "bin_collection_due",
    "delivery_arrived", "emergency_detected", "expected_delivery_arrived",
    "failed_entry_attempt", "fridge_door_alarm", "fridge_door_opened",
    "intended_wake_time", "inventory_stockout", "laundry_loaded", "mail_delivered",
    "mailbox_full", "occupants_departed", "occupants_returned",
    "stove_became_unattended", "supported_emergency_detected", "toilet_paper_depleted",
    "unauthorized_access_attempt", "unauthorized_vehicle_arrived",
    "unknown_credential_arrived", "unsupported_alarm", "vacuum_low_battery",
    "visitor_missed", "visitor_rang", "weekly_cleaning_due",
})

# Deterministic keyword -> event-name-substring-token map.  Matching uses ONLY
# the public query text; every token is a substring of at least one event type.
QUERY_KEYWORD_TOKENS = {
    "alarm": ("alarm",),
    "alert": ("alarm", "notification"),
    "arriv": ("arrived",),
    "away": ("away", "occupants"),
    "bath": ("bathroom",),
    "beer": ("supply", "inventory"),
    "bin": ("bin",),
    "clean": ("cleaning",),
    "coffee": ("coffee",),
    "cook": ("stove",),
    "curfew": ("curfew", "television"),
    "depart": ("departure", "occupants"),
    "dishwash": ("dishwasher",),
    "door": ("door",),
    "doorbell": ("visitor",),
    "emergenc": ("emergency",),
    "entry": ("failed",),
    "fridge": ("fridge",),
    "garage": ("garage",),
    "garbag": ("trash", "bin"),
    "guest": ("visitor",),
    "intrud": ("unauthorized", "unknown", "failed"),
    "key": ("departure", "keys"),
    "laundry": ("laundry",),
    "lock": ("door",),
    "mail": ("mail",),
    "media": ("media", "television"),
    "message": ("notification",),
    "movi": ("media",),
    "music": ("media",),
    "night": ("curfew", "television"),
    "notif": ("notification",),
    "occupanc": ("occupancy_became_zero", "occupants"),
    "packag": ("delivery",),
    "parcel": ("delivery",),
    "pellet": ("supply", "inventory"),
    "refrigerator": ("fridge",),
    "restock": ("supply", "inventory"),
    "return": ("return", "occupants"),
    "secur": ("unauthorized", "unknown", "lock"),
    "shower": ("shower",),
    "stove": ("stove",),
    "stock": ("inventory", "supply"),
    "supply": ("supply", "inventory"),
    "television": ("television", "media"),
    "toilet": ("toilet",),
    "trash": ("trash", "bin"),
    "tv": ("television", "media"),
    "unauthoriz": ("unauthorized", "unknown"),
    "vacuum": ("vacuum",),
    "vehicl": ("vehicle",),
    "visitor": ("visitor",),
    "wake": ("wake", "alarm"),
    "wash": ("laundry",),
}
FALLBACK_EVENT_GROUPS = {
    "device_completion": FALLBACK_DEVICE_COMPLETION_EVENTS,
    "exogenous_trigger": FALLBACK_EXOGENOUS_TRIGGER_EVENTS,
}


def observable_event_catalog() -> list[dict[str, Any]]:
    catalog = []
    for event_type in EVENT_TYPES:
        names = ["type", "step"]
        if event_type in DEVICE_EVENTS:
            names.append("device_id")
        if event_type.startswith("rule_"):
            names.append("rule_id")
        names.extend(EVENT_EXTRA_FIELDS.get(event_type, ()))
        names = sorted(set(names))
        catalog.append({
            "event_type": event_type,
            "filterable_scalar_fields": [
                {"name": name, "type": FIELD_TYPES[name], "required": name == "type"}
                for name in names
            ],
            "public_nonfilterable_fields": (
                [{"name": "recipients", "type": "list[string]", "required": True}]
                if event_type == "notification_sent" else []
            ),
        })
    return catalog


OBSERVABLE_EVENT_CATALOG = observable_event_catalog()

EVENT_INTERFACE_SCOPE_NOTE = (
    "This interface is query-scoped: it contains the event types relevant to the "
    "user's request plus conservative device-completion and exogenous-trigger "
    "fallback groups plus the rule lifecycle events. Events outside it are not "
    "filterable in wait.until_event."
)


def select_event_types_for_query(query: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Deterministically select event types using ONLY the public query text.

    No private Episode data is consulted: the single input is the public query
    string.  The union of query-matched types, the conservative fallback
    groups, and the rule lifecycle events is returned in sorted order.
    """

    words = re.findall(r"[a-z]+", str(query).lower())
    matched = [
        keyword for keyword in sorted(QUERY_KEYWORD_TOKENS)
        if any(word.startswith(keyword) for word in words)
    ]
    tokens = {token for keyword in matched for token in QUERY_KEYWORD_TOKENS[keyword]}
    matched_events = {event for event in EVENT_TYPES if any(token in event for token in tokens)}
    selected = matched_events | set(RULE_EVENT_TYPES)
    for group in FALLBACK_EVENT_GROUPS.values():
        selected |= set(group)
    return tuple(sorted(selected)), tuple(matched)


def device_interfaces_from_observation(observation: dict[str, Any]) -> list[dict[str, Any]]:
    """Complete public device interfaces, fixed for the whole Episode."""

    return [
        {
            "device_id": item["device_id"],
            "device_type": item["device_type"],
            "capabilities": list(item["capabilities"]),
            "interfaces": deepcopy(item["interfaces"]),
        }
        for item in observation["inventory"]["devices"]
    ]


def build_system_content(
    query: str,
    device_interfaces: list[dict[str, Any]],
    selected_event_types: tuple[str, ...],
) -> str:
    """Episode-static system message.  Built once per Episode and reused."""

    selected = set(selected_event_types)
    document = {
        "role_directive": SYSTEM_PROMPT,
        "action_grammar": list(GRAMMAR),
        "action_examples": deepcopy(ACTION_EXAMPLES),
        "protocol_rules": list(PROTOCOL_RULES),
        "device_interfaces": device_interfaces,
        "observable_event_interface": {
            "scope_note": EVENT_INTERFACE_SCOPE_NOTE,
            "selected_event_types": list(selected_event_types),
            "event_catalog": [
                row for row in OBSERVABLE_EVENT_CATALOG if row["event_type"] in selected
            ],
        },
    }
    return json.dumps(document, ensure_ascii=False, sort_keys=True, indent=1)


def system_prompt_sha256(content: str) -> str:
    return hashlib.sha256(content.encode()).hexdigest()


def prompt_template_sha256() -> str:
    value = {
        "system_directive": SYSTEM_PROMPT,
        "grammar": GRAMMAR,
        "examples": ACTION_EXAMPLES,
        "protocol_rules": PROTOCOL_RULES,
        "observable_event_catalog": OBSERVABLE_EVENT_CATALOG,
        "fallback_event_groups": {name: sorted(group) for name, group in FALLBACK_EVENT_GROUPS.items()},
        "rule_event_types": sorted(RULE_EVENT_TYPES),
        "query_keyword_tokens": QUERY_KEYWORD_TOKENS,
    }
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def event_catalog_sha256() -> str:
    encoded = json.dumps(OBSERVABLE_EVENT_CATALOG, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def build_messages(
    view: dict[str, Any],
    previous_action: dict[str, Any] | None,
    system_content: str,
) -> list[dict[str, str]]:
    """Per-turn payload: public query/profile, observation, previous fields only."""

    payload = {
        "query": view["query"],
        "user_preferences": view["public_profile"],
        "current_observation": view["observation"],
        "previous_action": previous_action,
        "previous_action_result": view["last_feedback"],
    }
    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


class WorkflowLLMPolicy:
    def __init__(self, client: Any):
        self.client = client
        self.calls = 0
        self.api_successes = 0
        self.parsed_json_actions = 0
        self.tokens = 0
        self.latency_ms = 0.0
        self.errors: Counter[str] = Counter()
        self.output_records: list[dict[str, Any]] = []
        self.previous_action: dict[str, Any] | None = None
        self.system_content: str | None = None
        self.system_prompt_sha256: str | None = None
        self.selected_event_types: tuple[str, ...] = ()
        self.selection_matched_keywords: tuple[str, ...] = ()

    def _episode_system_content(self, view: dict[str, Any]) -> str:
        query = view["query"]
        selected, matched = select_event_types_for_query(query)
        device_interfaces = device_interfaces_from_observation(view["observation"])
        content = build_system_content(query, device_interfaces, selected)
        if self.system_content is None:
            self.system_content = content
            self.system_prompt_sha256 = system_prompt_sha256(content)
            self.selected_event_types = selected
            self.selection_matched_keywords = matched
        elif content != self.system_content:
            raise RuntimeError("Episode system prefix must be byte-identical across calls")
        return self.system_content

    def decide(self, view: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        record = {"call_index": self.calls - 1, "raw_content": None, "raw_content_sha256": None, "error": None}
        # Invariant enforcement happens outside the error handling below: a
        # prefix drift inside one Episode is a bug, never a model failure.
        system_content = self._episode_system_content(view)
        try:
            response = self.client.complete(build_messages(view, self.previous_action, system_content))
            self.api_successes += 1
            self.tokens += int((response.get("usage") or {}).get("total_tokens", 0))
            self.latency_ms += float(response.get("latency_ms", 0.0))
            content = response.get("content")
            if isinstance(content, str):
                record["raw_content"] = content
                record["raw_content_sha256"] = hashlib.sha256(content.encode()).hexdigest()
            action = _parse_strict_json(content)
            self.parsed_json_actions += 1
            self.previous_action = deepcopy(action)
            return action
        except Exception as exc:
            error = f"{type(exc).__name__}:{exc}"
            record["error"] = error
            self.errors[error] += 1
            return {"kind": "invalid_model_output"}
        finally:
            self.output_records.append(record)


def load_temporal_episode(index: int, release_dir: Path = DEFAULT_RELEASE):
    public_rows = _read_jsonl(release_dir / "episodes_public.jsonl")
    private_rows = _read_jsonl(release_dir / "episodes_private.jsonl")
    if index < 0 or index >= len(public_rows) or len(public_rows) != len(private_rows):
        raise ValueError("episode index is outside the aligned formal release")
    public, private = public_rows[index], private_rows[index]
    if public["episode_id"] != private["episode_id"]:
        raise RuntimeError("public/private Episode IDs are misaligned")
    trusted_spec = decode_serialization(private["trusted_reset_receipt"]["episode_spec"], "workflow EpisodeSpec")
    reset = private["reset_receipt"]["payload"]
    bootstrap = deepcopy(reset["public_bootstrap"])
    bootstrap["allowed_action_kinds"] = sorted(PUBLIC_ACTION_KINDS)
    from harness_v2.core import EpisodeSpec
    spec = EpisodeSpec(public["episode_id"], bootstrap, reset["seed"], trusted_spec["max_decisions"])
    process = private["process_receipt"]["payload"]
    backend = TemporalHomeBackend(
        private_scenario_type=private["contract"]["scenario_type"],
        private_config=deepcopy(process["backend_effective_config"]),
        private_horizon_seconds=process["horizon_seconds"],
    )
    return public, private, spec, backend


def evaluate_one(client: Any, index: int = 0, release_dir: Path = DEFAULT_RELEASE) -> dict[str, Any]:
    public, private, spec, backend = load_temporal_episode(index, release_dir)
    policy = WorkflowLLMPolicy(client)
    run = Harness(backend).run_one(spec, policy)
    score = evaluate_temporal_home(
        private["contract"]["scenario_type"], run,
        contract=private["contract"], profile=public["public_profile"],
    )
    action_records = [row for row in run.public_trace if row["type"] in {"action", "protocol_error", "backend_error"}]
    executed_actions = [row for row in action_records if row["type"] == "action"]
    rejected_actions = sum(row["accepted"] is not True for row in executed_actions)
    result = {
        "episode_id": spec.episode_id,
        "query": public["query"],
        "model": getattr(client, "model", DEFAULT_MODEL),
        "run_status": run.status,
        "responsibility_success": score["success"],
        "device_command_count": score["device_command_count"],
        "count_unit": score["count_unit"],
        "calls": policy.calls,
        "api_successes": policy.api_successes,
        "parsed_json_actions": policy.parsed_json_actions,
        "harness_action_records": len(executed_actions),
        "accepted_harness_actions": sum(row["accepted"] is True for row in executed_actions),
        "protocol_error_count": sum(row["type"] == "protocol_error" for row in action_records),
        "backend_error_count": sum(row["type"] == "backend_error" for row in action_records),
        "all_actions_accepted": bool(executed_actions) and rejected_actions == 0,
        "rejected_action_count": rejected_actions,
        "system_prompt_sha256": policy.system_prompt_sha256,
        "selected_event_types": list(policy.selected_event_types),
        "selection_matched_keywords": list(policy.selection_matched_keywords),
        "tokens": policy.tokens,
        "latency_ms": policy.latency_ms,
        "errors": dict(policy.errors),
        "trace_digest": run.trace_digest,
        "public_action_records": action_records,
        "model_output_records": policy.output_records,
    }
    result["max_consecutive_identical_rejected_action"] = max_consecutive_identical_rejected_action(action_records)
    return result


def aggregate_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    calls = sum(row["calls"] for row in results)
    successful = [row for row in results if row["responsibility_success"]]
    metrics = {
        "episode_count": len(results),
        "responsibility_success_rate": sum(row["responsibility_success"] for row in results) / len(results) if results else None,
        "success_conditioned_device_command_count": (
            sum(row["device_command_count"] for row in successful) / len(successful) if successful else None
        ),
        "completed_run_rate": sum(row["run_status"] == "completed" for row in results) / len(results) if results else None,
        "decision_call_api_success_rate_after_transport_retries": sum(row["api_successes"] for row in results) / calls if calls else None,
        "plain_json_action_rate": sum(row["parsed_json_actions"] for row in results) / calls if calls else None,
        "rejected_action_count": sum(row["rejected_action_count"] for row in results),
        "total_api_calls": calls,
        "total_tokens": sum(row["tokens"] for row in results),
        "mean_latency_ms_per_call": sum(row["latency_ms"] for row in results) / calls if calls else None,
        "count_unit": "device_command",
    }
    harness_actions = sum(row["harness_action_records"] for row in results)
    accepted = sum(row["accepted_harness_actions"] for row in results)
    metrics["protocol_valid_action_rate"] = harness_actions / calls if calls else None
    metrics["backend_accepted_action_rate"] = accepted / harness_actions if harness_actions else None
    metrics["harness_action_total"] = harness_actions
    metrics["accepted_harness_action_total"] = accepted
    metrics["protocol_error_count"] = sum(row["protocol_error_count"] for row in results)
    metrics["backend_error_count"] = sum(row["backend_error_count"] for row in results)
    return metrics


def canonical_action_digest(action: Any) -> str:
    encoded = json.dumps(action, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def max_consecutive_identical_rejected_action(records: list[dict[str, Any]]) -> int:
    maximum = current = 0
    previous = None
    for row in records:
        if row.get("type") == "action" and row.get("accepted") is False:
            digest = canonical_action_digest(row.get("action"))
            current = current + 1 if digest == previous else 1
            previous = digest
            maximum = max(maximum, current)
        else:
            current, previous = 0, None
    return maximum


def paired_outcomes(baseline_episodes: list[dict[str, Any]], current_episodes: list[dict[str, Any]]) -> dict[str, Any]:
    """Episode-level gained/lost/tied versus v2, paired by identical Episode ID."""

    baseline_ids = [row["episode_id"] for row in baseline_episodes]
    current_ids = [row["episode_id"] for row in current_episodes]
    if len(baseline_ids) != len(set(baseline_ids)) or len(current_ids) != len(set(current_ids)):
        raise RuntimeError("paired Episode IDs must be unique")
    if current_ids != baseline_ids[: len(current_ids)]:
        raise RuntimeError("current Episodes must exactly match a prefix of the frozen baseline")
    current_by_id = {row["episode_id"]: row for row in current_episodes}
    dimensions = {
        "responsibility_success": lambda row: bool(row["responsibility_success"]),
        "run_completed": lambda row: row["run_status"] == "completed",
    }
    paired: dict[str, Any] = {}
    for name, pick in dimensions.items():
        gained: list[str] = []
        lost: list[str] = []
        tied: list[str] = []
        matched = 0
        for base in baseline_episodes:
            current = current_by_id.get(base["episode_id"])
            if current is None:
                break
            matched += 1
            before, after = pick(base), pick(current)
            if not before and after:
                gained.append(base["episode_id"])
            elif before and not after:
                lost.append(base["episode_id"])
            else:
                tied.append(base["episode_id"])
        paired[name] = {
            "gained": gained,
            "lost": lost,
            "tied": tied,
            "gained_count": len(gained),
            "lost_count": len(lost),
            "tied_count": len(tied),
            "paired_episode_count": matched,
        }
    return paired


def assert_baseline_and_release_frozen(path: Path = BASELINE_REPORT_PATH, release_dir: Path = DEFAULT_RELEASE) -> dict[str, Any]:
    """Fail closed on the frozen v2 paired report and release inputs."""

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != BASELINE_REPORT_SHA256:
        raise RuntimeError(f"v2 baseline report SHA256 mismatch: {digest}")
    release_hashes = {
        "episodes_public.jsonl": PUBLIC_RELEASE_SHA256,
        "episodes_private.jsonl": PRIVATE_RELEASE_SHA256,
    }
    for filename, expected in release_hashes.items():
        actual = hashlib.sha256((release_dir / filename).read_bytes()).hexdigest()
        if actual != expected:
            raise RuntimeError(f"frozen release SHA256 mismatch for {filename}: {actual}")
    report = json.loads(path.read_text(encoding="utf-8"))
    indices = report["experiment_config"]["sample_indices"]
    if indices != FIXED_INDICES:
        raise RuntimeError(f"v2 baseline sample indices must be exactly {FIXED_INDICES}")
    episodes = report["episodes"]
    if len(episodes) != len(FIXED_INDICES):
        raise RuntimeError("v2 baseline must contain exactly 20 paired Episodes")
    public_rows = _read_jsonl(release_dir / "episodes_public.jsonl")
    expected_ids = [public_rows[index]["episode_id"] for index in FIXED_INDICES]
    actual_ids = [row["episode_id"] for row in episodes]
    if actual_ids != expected_ids:
        raise RuntimeError("v2 baseline Episode IDs do not match the fixed release indices")
    return report


def build_report(results: list[dict[str, Any]], indices: list[int], *, execution_finished: bool, baseline: dict[str, Any]) -> dict[str, Any]:
    baseline_ids = [row["episode_id"] for row in baseline["episodes"]]
    result_ids = [row["episode_id"] for row in results]
    expected_ids = baseline_ids if execution_finished else baseline_ids[: len(results)]
    if result_ids != expected_ids:
        raise RuntimeError("v4 result Episode IDs do not exactly match the frozen paired sample")
    return {
        "schema_version": "temporal-home-v4-flash-query-scoped-system-prefix-v4",
        "execution_finished": execution_finished,
        "experiment_config": {
            "model": results[0]["model"] if results else DEFAULT_MODEL,
            "base_url": DEFAULT_BASE,
            "sample_indices": indices,
            "sampling_semantics": "release-order first classes, one variant-0 Episode per class; not a random or full-30 sample",
            "prompt_template_sha256": prompt_template_sha256(),
            "observable_event_catalog_sha256": event_catalog_sha256(),
            "observable_event_type_count": len(EVENT_TYPES),
            "system_prefix_policy": "episode_static_byte_identical_across_calls",
            "event_selection": {
                "basis": "public_query_text_only",
                "matched_keyword_tokens": QUERY_KEYWORD_TOKENS,
                "fallback_groups": {name: sorted(group) for name, group in FALLBACK_EVENT_GROUPS.items()},
                "always_included_groups": ["device_completion", "exogenous_trigger", "rule_events"],
            },
            "request_body_fields": REQUEST_BODY_FIELDS,
            "structured_output_enabled": False,
            "transport_retries": 2,
            "model_protocol_failure_retries": 0,
            "changed_vs_v2": [
                "temporal_home_backend", "temporal_home_evaluator", "four_action_api",
                "complete_observable_event_catalog", "previous_action_context",
                "device_command_count_vocabulary", "episode_static_system_prefix",
                "query_scoped_event_interface", "user_payload_without_prompt_catalogs",
            ],
            "baseline_report_path": str(BASELINE_REPORT_PATH.relative_to(ROOT)),
            "baseline_report_sha256": BASELINE_REPORT_SHA256,
        },
        "metrics_all": aggregate_results(results),
        "paired_vs_v2": paired_outcomes(baseline["episodes"], results),
        "rejected_action_loop_diagnostics": {
            row["episode_id"]: row["max_consecutive_identical_rejected_action"] for row in results
        },
        "episodes": results,
    }


def main(args: list[str] | None = None) -> dict[str, Any]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--stride", type=int, default=10)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    ns = parser.parse_args(args)
    if ns.base_url != DEFAULT_BASE or ns.model != DEFAULT_MODEL:
        raise ValueError("paired v4 rerun freezes the default base URL and model")
    validate_release(DEFAULT_RELEASE, minimum_responsibilities=30, minimum_episodes=300)
    if ns.count <= 0 or ns.stride <= 0:
        raise ValueError("count and stride must be positive")
    indices = [ns.index + offset * ns.stride for offset in range(ns.count)]
    if indices != FIXED_INDICES:
        raise ValueError("the paired v4 rerun requires exactly the fixed indices 0,10,...,190")
    baseline = assert_baseline_and_release_frozen()
    if baseline["experiment_config"].get("model") != DEFAULT_MODEL:
        raise RuntimeError("v2 baseline model does not match the frozen default model")
    client = ChatClient(ns.base_url, ns.model)
    ns.output.parent.mkdir(parents=True, exist_ok=True)
    results = []
    for index in indices:
        results.append(evaluate_one(client, index))
        checkpoint = build_report(results, indices[:len(results)], execution_finished=False, baseline=baseline)
        ns.output.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    report = build_report(results, indices, execution_finished=True, baseline=baseline)
    ns.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    main()
