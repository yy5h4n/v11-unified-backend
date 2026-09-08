"""Small command-driven backend used to test the minimal Harness core."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from typing import Any

import rfc8785

from .core import ActionOutcome, BackendStep, EpisodeSpec


class GoldenHomeBackend:
    def reset(self, episode: EpisodeSpec) -> BackendStep:
        self._index = 0
        self._temperatures = {"kitchen": 18.0, "living": 18.0}
        self._targets: dict[str, float | None] = {"kitchen": None, "living": None}
        self._rules: dict[str, dict[str, Any]] = {}
        self._energy = 0.0
        self._events: list[dict[str, Any]] = []
        self._applied: list[dict[str, Any]] = []
        self._started_at = datetime(2026, 1, 1, 18, 0, tzinfo=timezone.utc)
        return self._step(False)

    def state_digest(self) -> str:
        state = {"index": self._index, "temperatures": self._temperatures, "targets": self._targets, "rules": self._rules, "energy": self._energy}
        return sha256(rfc8785.dumps(state)).hexdigest()

    def execute_atomic(self, action: dict[str, Any]) -> ActionOutcome:
        kind = action["kind"]
        commands = action["commands"] if kind == "act" else action["rule"].get("commands", []) + action["rule"].get("release_commands", []) if kind == "install_rule" else []
        error = self._validate_commands(commands)
        if error:
            return ActionOutcome(False, {"status": "rejected", "error_code": error}, {"status": "rejected"}, error)
        if kind == "act":
            self._apply_commands(action["commands"], source="agent")
        elif kind == "install_rule":
            rule = deepcopy(action["rule"])
            self._rules[rule["rule_id"]] = rule
            self._events.append({"type": "rule_installed", "rule_id": rule["rule_id"]})
        elif kind == "cancel_rule":
            self._rules.pop(action["rule_id"], None)
            self._events.append({"type": "rule_cancelled", "rule_id": action["rule_id"]})
        elif kind == "ask":
            return ActionOutcome(True, {"answer": "unknown"}, {"answer_source": "fixture"})
        return ActionOutcome(True, {"status": "accepted"}, {"status": "accepted"})

    def advance(self, triggering_action: dict[str, Any] | None = None) -> BackendStep:
        triggering_action = triggering_action or {"kind": "act", "commands": []}
        steps = 1
        if triggering_action["kind"] == "wait":
            mode = triggering_action["mode"]
            if mode == "for":
                duration = triggering_action["duration_seconds"]
                if duration % 900:
                    raise ValueError("UNSUPPORTED_WAIT_GRANULARITY")
                steps = int(duration // 900)
            elif mode == "until":
                target = datetime.fromisoformat(triggering_action["timestamp"].replace("Z", "+00:00"))
                remaining = (target - (self._started_at + timedelta(seconds=900 * self._index))).total_seconds()
                if remaining <= 0 or remaining % 900:
                    raise ValueError("INVALID_WAIT_TIMESTAMP")
                steps = int(remaining // 900)
            else:
                timeout = triggering_action["timeout_seconds"]
                if timeout % 900:
                    raise ValueError("UNSUPPORTED_WAIT_GRANULARITY")
                steps = int(timeout // 900)
        for _ in range(steps):
            self._advance_once()
            if self._index >= 3:
                break
            if triggering_action["kind"] == "wait" and self._events:
                break
        return self._step(self._index >= 3)

    def _advance_once(self) -> None:
        self._index += 1
        self._events = []
        self._applied = []
        for rule in list(self._rules.values()):
            if self._index == rule["fire_at_step"]:
                self._apply_commands(rule["commands"], source="rule_firing", rule_id=rule["rule_id"])
                self._events.append({"type": "rule_fired", "rule_id": rule["rule_id"]})
            if self._index == rule["release_at_step"]:
                self._apply_commands(rule["release_commands"], source="rule_release", rule_id=rule["rule_id"])
                self._events.append({"type": "rule_released", "rule_id": rule["rule_id"]})
                del self._rules[rule["rule_id"]]
        for room, target in self._targets.items():
            if target is None:
                self._temperatures[room] -= 0.5
            else:
                delta = max(-2.0, min(2.0, target - self._temperatures[room]))
                self._temperatures[room] += delta
                self._energy += abs(delta)

    def _event_matches(self, event_filter: dict[str, Any]) -> bool:
        return any(all(event.get(key) == value for key, value in event_filter.items()) for event in self._events)

    def _apply_commands(self, commands: list[dict[str, Any]], *, source: str, rule_id: str | None = None) -> None:
        for command in commands:
            room = command["device_id"].split(".", 1)[1]
            self._targets[room] = float(command["parameters"]["target_c"])
            self._applied.append({**deepcopy(command), "source": source, "rule_id": rule_id, "applied_at_step": self._index})

    @staticmethod
    def _validate_commands(commands: list[dict[str, Any]]) -> str | None:
        for command in commands:
            if command.get("device_id") not in {"hvac.kitchen", "hvac.living"}:
                return "UNKNOWN_DEVICE"
            if command.get("capability") != "thermal.control" or command.get("operation") != "set":
                return "UNSUPPORTED_COMMAND"
            target = command.get("parameters", {}).get("target_c")
            if not isinstance(target, (int, float)) or not 16 <= target <= 30:
                return "TARGET_OUT_OF_RANGE"
        return None

    def _step(self, terminal: bool) -> BackendStep:
        public = {
            "step": self._index,
            "time": (self._started_at + timedelta(seconds=900 * self._index)).isoformat().replace("+00:00", "Z"),
            "rooms": {room: {"temperature_c": value} for room, value in self._temperatures.items()},
            "devices": {f"hvac.{room}": {"target_c": self._targets[room]} for room in self._targets},
            "events": deepcopy(self._events),
        }
        private = {"energy": self._energy, "applied_commands": deepcopy(self._applied), "active_rule_ids": sorted(self._rules)}
        return BackendStep(public, private, terminal)


class RulePolicy:
    def __init__(self):
        self._installed = False

    def decide(self, public_view: dict[str, Any]) -> dict[str, Any]:
        if self._installed:
            return {"kind": "wait", "mode": "for", "duration_seconds": 900}
        query_value = public_view["query"]
        query = (query_value.get("text", "") if isinstance(query_value, dict) else query_value).casefold()
        rooms = ["kitchen", "living"] if "whole home" in query else ["kitchen"] if "kitchen" in query else []
        if not rooms:
            return {"kind": "wait", "mode": "for", "duration_seconds": 900}
        self._installed = True
        commands = [self._command(room, 22) for room in rooms]
        release = [self._command(room, 18) for room in rooms]
        return {"kind": "install_rule", "rule": {"rule_id": "rule.evening", "fire_at_step": 1, "release_at_step": 2, "commands": commands, "release_commands": release}}

    @staticmethod
    def _command(room: str, target: float) -> dict[str, Any]:
        return {"device_id": f"hvac.{room}", "capability": "thermal.control", "operation": "set", "parameters": {"target_c": target}}


class NoOpPolicy:
    def decide(self, public_view: dict[str, Any]) -> dict[str, Any]:
        return {"kind": "wait", "mode": "for", "duration_seconds": 900}


def golden_episode(query: str = "Keep the kitchen comfortable this evening.") -> EpisodeSpec:
    return EpisodeSpec(
        episode_id="episode.simple.multiroom",
        public_bootstrap={
            "query": query,
            "profile": {"thermal_preference_c": 22},
            "capabilities": {"thermal.control": {"target_c": {"min": 16, "max": 30}}},
            "action_schema": {
                "kinds": ["act", "install_rule", "cancel_rule", "ask", "wait"],
                "command": {"required": ["device_id", "capability", "operation", "parameters"]},
                "rule": {"required": ["rule_id", "fire_at_step", "release_at_step", "commands", "release_commands"]},
            },
        },
        seed=7,
        max_decisions=4,
    )
