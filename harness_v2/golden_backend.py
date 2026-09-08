"""Tiny deterministic backend used only to prove Harness V2 trust-chain semantics."""

from __future__ import annotations

import base64
import hashlib
import json
from copy import deepcopy
from datetime import datetime
from typing import Any

import rfc8785

from .reference_scheduler import Occurrence, RuleState, scheduler_step


START = "2026-01-01T17:00:00+00:00"
RETRY = "2026-01-01T17:01:00+00:00"
TRIGGER = "2026-01-01T17:30:00+00:00"
RELEASE = "2026-01-01T18:30:00+00:00"
END = "2026-01-01T19:00:00+00:00"


def apply_immediate_command(pre_backend_digest: str, command: dict[str, Any]) -> str:
    """Return the committed backend-state digest for one validated command."""
    return hashlib.sha256(rfc8785.dumps({"prior": pre_backend_digest, "command": command})).hexdigest()


def resulting_temperature_c(target_c: float | None) -> float:
    """Deterministic response curve for the formal conformance fixture."""
    if target_c is None:
        return 17.0
    return {18: 17.5, 20: 19.0, 22: 20.0, 23: 21.0}.get(target_c, 17.0)


def native_reset_snapshot() -> dict[str, Any]:
    return {
        "timestamp": START,
        "timezone": "UTC",
        "rooms": {"kitchen": {"temperature_c": 18.0}, "living": {"temperature_c": 18.0}},
        "devices": {"hvac.kitchen": {"room": "kitchen", "mode": "off"}, "hvac.living": {"room": "living", "mode": "off"}},
    }


def project_reset(native: dict[str, Any]) -> dict[str, Any]:
    rooms = []
    devices = []
    observations = []
    for room_id in ("kitchen", "living"):
        device_id = f"hvac.{room_id}"
        rooms.append({"room_id": room_id, "device_ids": [device_id], "availability": "available"})
        devices.append({"device_id": device_id, "device_type": "hvac", "capabilities": ["thermal.control"], "availability": "available", "state": {"mode": native["devices"][device_id]["mode"]}})
        observations.append({"name": f"rooms.{room_id}.temperature_c", "value": native["rooms"][room_id]["temperature_c"], "unit": "C", "quality": "fresh", "observed_at": native["timestamp"]})
    return {
        "rooms": rooms,
        "virtual_clock": {"now": native["timestamp"], "timezone": native["timezone"]},
        "inventory": {"complete": True, "devices": devices},
        "observations": observations,
        "events": [],
    }


def _datum(value: float, timestamp: str, unit: str = "C") -> dict[str, Any]:
    return {"value": value, "unit": unit, "quality": "fresh", "observed_at": timestamp}


def _command_record(command: dict[str, Any], *, source: str, origin: dict[str, Any], timestamp: str) -> dict[str, Any]:
    return {
        **command,
        "source": source,
        "origin": origin,
        "requested_at": timestamp,
        "applied_at": timestamp,
        "status": "committed",
        "error_code": None,
        "action_cost": 1,
        "cost_unit": "benchmark_unit",
    }


def replay_body(native: dict[str, Any], session: dict[str, Any], arm_id: str, query_digest: str, manifest: dict[str, Any]) -> dict[str, Any]:
    """Replay reset + sealed transactions through scheduler and simple dynamics."""
    reset_digest = hashlib.sha256(rfc8785.dumps(native)).hexdigest()
    for turn in session["turns"]:
        callback = json.loads(base64.b64decode(turn["callback"]["canonical_bytes_base64"]))
        choice = json.loads(base64.b64decode(turn["terminal_choice"]["canonical_bytes_base64"]))
        if callback["backend_state_digest"] != reset_digest or turn["pre_state"]["backend"] != reset_digest:
            raise ValueError("session did not start from trusted native reset")
        if choice["kind"] != "commit_transaction":
            continue
        exchange = turn["terminal_outcome"]
        invalid_wake = any(datetime.fromisoformat(item["at"]) <= datetime.fromisoformat(callback["timestamp"]) for item in choice["transaction"]["wake_requests"])
        expected_status = "rejected" if invalid_wake else "committed"
        if exchange["strict_outcome"]["status"] != expected_status:
            raise ValueError("trusted transaction validation status mismatch")
        if turn["post_state"]["backend"] != reset_digest or exchange["post_state"]["backend_state_digest"] != reset_digest:
            raise ValueError("rule transaction changed physical backend during validation")
        if invalid_wake and turn["post_state"]["rules"] != turn["pre_state"]["rules"]:
            raise ValueError("rejected rule transaction was not atomic")
    exchange_turns = [turn for turn in session["turns"] if isinstance(turn["terminal_outcome"], dict) and "request" in turn["terminal_outcome"]]
    exchanges = [turn["terminal_outcome"] for turn in exchange_turns]
    exchange_timestamps = {
        id(turn["terminal_outcome"]): json.loads(base64.b64decode(turn["callback"]["canonical_bytes_base64"]))["timestamp"]
        for turn in exchange_turns
    }
    committed = [exchange for exchange in exchanges if exchange["strict_outcome"]["status"] == "committed"]
    created = [(exchange, item["rule"]) for exchange in committed for item in exchange["request"]["create_rules"]]
    rules: dict[str, RuleState] = {}
    install_transaction: dict[str, str] = {}
    for sequence, (exchange, rule) in enumerate(created):
        rules[rule["rule_id"]] = RuleState(rule["rule_id"], rule["priority"], sequence, deepcopy(rule["commands"]), deepcopy(rule["on_release_commands"]), lifecycle="until_timestamp")
        install_transaction[rule["rule_id"]] = exchange["request"]["transaction_id"]
    applied_by_time: dict[str, list[dict[str, Any]]] = {START: [], RETRY: [], TRIGGER: [], RELEASE: []}
    rule_events: dict[str, list[dict[str, Any]]] = {START: [], RETRY: [], TRIGGER: [], RELEASE: []}
    protocol_events: dict[str, list[dict[str, Any]]] = {START: [], RETRY: [], TRIGGER: [], RELEASE: []}
    for exchange in exchanges:
        outcome = exchange["strict_outcome"]
        timestamp = exchange_timestamps[id(exchange)]
        protocol_events[timestamp].append({"event_id": f"event.{outcome['transaction_id']}", "timestamp": timestamp, "type": f"transaction_{outcome['status']}", "transaction_id": outcome["transaction_id"], "error_code": outcome["transaction_error_code"]})
        if outcome["status"] == "committed":
            for item in exchange["request"]["create_rules"]:
                rule = item["rule"]
                rule_events[timestamp].append({"event_id": f"event.install.{rule['rule_id']}", "timestamp": timestamp, "type": "installed", "rule_id": rule["rule_id"], "reason": "transaction_committed", "origin": {"kind": "installation_transaction", "id": exchange["request"]["transaction_id"]}})
    if rules:
        trigger_time = datetime.fromisoformat(TRIGGER)
        release_time = datetime.fromisoformat(RELEASE)
        horizon = datetime.fromisoformat(END)
        occurrences = [Occurrence(f"occurrence.trigger.{rule_id}", rule_id, trigger_time) for rule_id in rules]
        trigger_result = scheduler_step(timestamp=trigger_time, horizon=horizon, rules=rules, occurrences=occurrences, backend_apply=lambda _: True)
        for rule_id, rule in rules.items():
            if rule.fire_count:
                occurrence_id = f"occurrence.trigger.{rule_id}"
                rule_events[TRIGGER].append({"event_id": f"event.fire.{rule_id}", "timestamp": TRIGGER, "type": "firing_committed", "rule_id": rule_id, "trigger_occurrence_id": occurrence_id, "command_ids": [command["command_id"] for command in rule.commands], "error_code": None})
                for command in rule.commands:
                    applied_by_time[TRIGGER].append(_command_record(command, source="rule_firing", origin={"kind": "rule_firing", "installation_transaction_id": install_transaction[rule_id], "rule_id": rule_id, "trigger_occurrence_id": occurrence_id}, timestamp=TRIGGER))
        expiry = [Occurrence(f"occurrence.expiry.{rule_id}", rule_id, release_time, kind="expiry") for rule_id in rules]
        scheduler_step(timestamp=release_time, horizon=horizon, rules=rules, occurrences=expiry, backend_apply=lambda _: True)
        for rule_id, rule in rules.items():
            retirement = f"occurrence.expiry.{rule_id}"
            rule_events[RELEASE].append({"event_id": f"event.expire.{rule_id}", "timestamp": RELEASE, "type": "expired", "rule_id": rule_id, "reason": "until_timestamp", "origin": {"kind": "retirement_occurrence", "id": retirement}})
            rule_events[RELEASE].append({"event_id": f"event.release.{rule_id}", "timestamp": RELEASE, "type": "released", "rule_id": rule_id, "reason": "expiry_release_committed", "origin": {"kind": "retirement_occurrence", "id": retirement}})
            for command in rule.release_commands:
                applied_by_time[RELEASE].append(_command_record(command, source="rule_release", origin={"kind": "rule_release", "installation_transaction_id": install_transaction[rule_id], "rule_id": rule_id, "retirement_occurrence_id": retirement}, timestamp=RELEASE))
    controlled = {command["device_id"].split(".", 1)[1] for command in applied_by_time[TRIGGER]}
    initial_kitchen = float(native["rooms"]["kitchen"]["temperature_c"])
    initial_living = float(native["rooms"]["living"]["temperature_c"])
    timeline = [
        (START, 60, initial_kitchen, initial_living),
        (RETRY, 1740, initial_kitchen, initial_living),
        (TRIGGER, 3600, min(22.0, initial_kitchen + 3.0) if "kitchen" in controlled else initial_kitchen - 1.0, min(22.0, initial_living + 3.0) if "living" in controlled else initial_living - 1.0),
        (RELEASE, 1800, min(22.0, initial_kitchen + 4.0) if "kitchen" in controlled else initial_kitchen - 2.0, min(22.0, initial_living + 4.0) if "living" in controlled else initial_living - 2.0),
    ]
    frames = []
    for index, (timestamp, duration, kitchen, living) in enumerate(timeline):
        unauthorized = sum(1 for command in applied_by_time[timestamp] if arm_id == "A" and command["device_id"] == "hvac.living")
        frames.append({
            "frame_index": index,
            "timestamp": timestamp,
            "duration_to_next_seconds": duration,
            "exogenous_state": {},
            "public_state": {"kitchen_temperature_c": _datum(kitchen, timestamp), "living_temperature_c": _datum(living, timestamp)},
            "private_evaluator_primitives": {"unauthorized_action_count": _datum(float(unauthorized), timestamp, "count")},
            "device_workflow_state": {},
            "applied_commands": applied_by_time[timestamp],
            "rule_events": rule_events[timestamp],
            "protocol_events": protocol_events[timestamp],
            "safety_events": [],
        })
    return {
        "trace_schema_version": "sealed_trace_v2",
        "episode_id": session["episode_configuration"]["episode_id"],
        "scenario_group_id": "group.golden.multiroom",
        "arm_id": arm_id,
        "query_digest": query_digest,
        "manifest_id": manifest["manifest_id"],
        "manifest_version": manifest["manifest_version"],
        "start_inclusive": START,
        "termination_exclusive": END,
        "seed_digest": hashlib.sha256(b"seed").hexdigest(),
        "exogenous_realization_digest": hashlib.sha256(b"exogenous").hexdigest(),
        "frames": frames,
        "transaction_exchanges": exchanges,
        "sealed": True,
    }
