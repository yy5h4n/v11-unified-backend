"""Trusted deterministic policies for the Harness V2 multi-room golden fixture."""

from __future__ import annotations

from typing import Any


TRIGGER = "2026-01-01T17:30:00+00:00"
RELEASE = "2026-01-01T18:30:00+00:00"


def scope_from_query(text: str) -> tuple[str, ...]:
    folded = text.casefold()
    if not folded.strip():
        return ()
    if "whole home" in folded or "every room" in folded:
        return ("kitchen", "living")
    if "kitchen" in folded:
        return ("kitchen",)
    return ()


def _command(room: str, *, release: bool = False, probe: bool = False) -> dict[str, Any]:
    phase = "release" if release else "heat"
    return {
        "command_id": f"command.{'probe.' if probe else ''}{phase}.{room}",
        "device_id": f"hvac.{room}",
        "capability": "thermal.control",
        "operation": "set",
        "parameters": {"target_c": 18 if release else 23},
    }


def _rule(scope: tuple[str, ...], *, probe: bool = False) -> dict[str, Any]:
    suffix = "whole" if len(scope) == 2 else "kitchen"
    return {
        "rule_id": f"rule.{'probe.' if probe else 'evening.'}{suffix}",
        "trigger": {"type": "at_timestamp", "at": TRIGGER},
        "condition": {"op": "true"},
        "commands": [_command(room, probe=probe) for room in scope],
        "lifecycle": {"type": "until_timestamp", "until": RELEASE},
        "on_release_commands": [_command(room, release=True, probe=probe) for room in scope],
        "on_release_policy": {"execute_on": "every_normal_retirement_and_committed_manual_cancel", "exactly_once": True, "failure": "record_failure_retire_no_retry"},
        "cooldown_seconds": 0,
        "priority": 50,
    }


def _transaction(scope: tuple[str, ...], backend_digest: str, *, invalid_probe: bool) -> dict[str, Any]:
    suffix = "whole" if len(scope) == 2 else "kitchen"
    return {
        "transaction_id": f"transaction.{'rollback' if invalid_probe else 'install'}.{suffix}",
        "pre_turn_backend_state_digest": backend_digest,
        "act_now": {"commands": []},
        "create_rules": [{"rule": _rule(scope, probe=invalid_probe)}],
        "cancel_rules": [],
        "subscriptions": [],
        "wake_requests": ([{"wake_id": f"wake.invalid.{suffix}", "at": "2026-01-01T17:01:00+00:00"}] if invalid_probe else [{"wake_id": f"wake.retry.{suffix}", "at": "2026-01-01T17:01:00+00:00"}]),
    }


def execute(agent_view: dict[str, Any], callbacks: list[dict[str, Any]], policy_id: str, policy_config: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the exact terminal choice for each delivered callback."""
    if policy_config != {"policy_id": policy_id, "fixture_version": "2"}:
        raise ValueError("untrusted golden policy configuration")
    if policy_id == "policy.noop":
        return [{"kind": "yield_without_mutation"} for _ in callbacks]
    if policy_id not in {"policy.agent", "policy.oracle"}:
        raise ValueError(f"unknown golden policy: {policy_id}")
    scope = scope_from_query(agent_view["query"]["text"])
    if not scope:
        return [{"kind": "yield_without_mutation"} for _ in callbacks]
    choices = []
    for index, callback in enumerate(callbacks):
        backend_digest = callback["backend_state_digest"]
        choices.append({"kind": "commit_transaction", "transaction": _transaction(scope, backend_digest, invalid_probe=index == 1)})
    return choices
