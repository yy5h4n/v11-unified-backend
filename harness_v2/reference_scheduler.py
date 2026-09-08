"""Deterministic reference scheduler for Harness V2 rule-state golden vectors."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable


@dataclass
class RuleState:
    rule_id: str
    priority: int
    creation_sequence: int
    commands: list[dict[str, Any]]
    release_commands: list[dict[str, Any]] = field(default_factory=list)
    lifecycle: str = "manual_cancel"
    max_fires: int | None = None
    cooldown_seconds: int = 0
    fire_count: int = 0
    cooldown_until: datetime | None = None
    active: bool = True
    released: bool = False


@dataclass(frozen=True)
class Occurrence:
    occurrence_id: str
    rule_id: str
    timestamp: datetime
    kind: str = "trigger"  # trigger | expiry | cancel
    condition: bool = True


@dataclass
class StepResult:
    timestamp: datetime
    events: list[dict[str, Any]]
    applied_commands: list[dict[str, Any]]
    callback_reasons: list[str]
    terminated: bool


def _command_key(command: dict[str, Any]) -> tuple[str, str]:
    return command["device_id"], command["capability"]


def scheduler_step(
    *,
    timestamp: datetime,
    horizon: datetime,
    rules: dict[str, RuleState],
    occurrences: list[Occurrence],
    backend_apply: Callable[[list[dict[str, Any]]], bool],
    public_callback_reasons: list[str] | None = None,
) -> StepResult:
    """Execute one scheduler instant in the frozen Harness V2 phase order."""
    events: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []
    release_queue: list[RuleState] = []

    # Horizon precedence suppresses all non-termination callbacks and Agent turns.
    if timestamp >= horizon:
        return StepResult(timestamp, [{"type": "horizon_termination"}], [], ["episode_termination"], True)

    ordered = sorted(
        (item for item in occurrences if item.timestamp == timestamp),
        key=lambda item: (0 if item.kind in {"expiry", "cancel"} else 1, item.occurrence_id),
    )

    # Expiry/cancel retires before any trigger at the same instant.
    for occurrence in ordered:
        if occurrence.kind not in {"expiry", "cancel"}:
            continue
        rule = rules.get(occurrence.rule_id)
        if rule and rule.active:
            rule.active = False
            events.append({"type": f"rule_{occurrence.kind}", "rule_id": rule.rule_id, "occurrence_id": occurrence.occurrence_id})
            release_queue.append(rule)

    candidates: list[tuple[RuleState, Occurrence]] = []
    for occurrence in ordered:
        if occurrence.kind != "trigger":
            continue
        rule = rules.get(occurrence.rule_id)
        if not rule or not rule.active:
            events.append({"type": "trigger_inactive", "rule_id": occurrence.rule_id, "occurrence_id": occurrence.occurrence_id})
            continue
        if not occurrence.condition:
            events.append({"type": "condition_false", "rule_id": rule.rule_id, "occurrence_id": occurrence.occurrence_id})
            continue
        if rule.cooldown_until is not None and timestamp < rule.cooldown_until:
            events.append({"type": "cooldown_suppressed", "rule_id": rule.rule_id, "occurrence_id": occurrence.occurrence_id})
            continue
        candidates.append((rule, occurrence))

    candidates.sort(key=lambda pair: (-pair[0].priority, pair[0].creation_sequence, pair[0].rule_id, pair[1].occurrence_id))
    claimed: dict[tuple[str, str], tuple[dict[str, Any], str]] = {}
    firing_batches: list[tuple[RuleState, Occurrence, list[dict[str, Any]]]] = []
    for rule, occurrence in candidates:
        batch: list[dict[str, Any]] = []
        conflict = False
        own: dict[tuple[str, str], dict[str, Any]] = {}
        for command in rule.commands:
            key = _command_key(command)
            if key in own and own[key] != command:
                conflict = True
                break
            own[key] = command
        if conflict:
            events.append({"type": "firing_conflict_within_rule", "rule_id": rule.rule_id, "occurrence_id": occurrence.occurrence_id})
            continue
        for key, command in own.items():
            prior = claimed.get(key)
            if prior is None:
                claimed[key] = (command, rule.rule_id)
                batch.append(command)
            elif prior[0] == command:
                events.append({"type": "command_coalesced", "rule_id": rule.rule_id, "winner_rule_id": prior[1]})
            else:
                conflict = True
                events.append({"type": "firing_conflict_lost", "rule_id": rule.rule_id, "winner_rule_id": prior[1]})
                break
        if not conflict:
            firing_batches.append((rule, occurrence, batch))

    for rule, occurrence, batch in firing_batches:
        success = backend_apply(batch)
        events.append({"type": "firing_committed" if success else "firing_failed", "rule_id": rule.rule_id, "occurrence_id": occurrence.occurrence_id})
        if not success:
            continue
        applied.extend(batch)
        rule.fire_count += 1
        rule.cooldown_until = timestamp + timedelta(seconds=rule.cooldown_seconds)
        complete = rule.lifecycle == "once" or (rule.lifecycle == "max_fires" and rule.max_fires is not None and rule.fire_count >= rule.max_fires)
        if complete:
            rule.active = False
            release_queue.append(rule)

    # Releases run after firings; retirement is permanent even when release fails.
    release_claims = {_command_key(command) for command in applied}
    for rule in sorted(release_queue, key=lambda item: (item.creation_sequence, item.rule_id)):
        if rule.released:
            continue
        rule.released = True
        if not rule.release_commands:
            events.append({"type": "rule_released", "rule_id": rule.rule_id, "empty": True})
            continue
        if any(_command_key(command) in release_claims for command in rule.release_commands):
            events.append({"type": "release_conflict_failed", "rule_id": rule.rule_id})
            continue
        success = backend_apply(rule.release_commands)
        events.append({"type": "release_committed" if success else "release_failed", "rule_id": rule.rule_id})
        if success:
            applied.extend(rule.release_commands)
            release_claims.update(_command_key(command) for command in rule.release_commands)

    reasons = sorted(set(public_callback_reasons or []))
    return StepResult(timestamp, events, applied, reasons, False)

