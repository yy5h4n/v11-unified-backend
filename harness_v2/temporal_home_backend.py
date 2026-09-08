"""Temporal-home compatibility backend layer for future benchmark runs.

Thin public layer over the legacy :class:`WorkflowBackend`.  It exposes exactly
the public action kinds ``act``, ``wait``, ``install_rule`` and ``cancel_rule``:
``ask`` is rejected at reset time when declared in the Episode bootstrap and a
direct ``execute_atomic`` ask returns an unaccepted outcome without mutating
state.  Every public surface (public feedback, ``BackendStep`` observations and
private state) is recursively renamed from the legacy ``action_cost`` /
``cost_unit`` vocabulary to ``device_command_count`` / ``count_unit`` and the
legacy ``ask_log`` is stripped.
"""

from __future__ import annotations

from typing import Any

from .core import ActionOutcome, BackendStep, EpisodeSpec
from .workflow_backend import WorkflowBackend

PUBLIC_ACTION_KINDS = frozenset({"act", "wait", "install_rule", "cancel_rule"})
COUNT_UNIT = "device_command"
UNSUPPORTED_ACTION_ERROR = "UNSUPPORTED_ACTION"
LEGACY_KEY_RENAMES = {"action_cost": "device_command_count", "cost_unit": "count_unit"}
DROPPED_KEYS = frozenset({"ask_log"})


def _transform_value(value: Any) -> Any:
    if isinstance(value, dict):
        transformed: dict[Any, Any] = {}
        for key, item in value.items():
            if key in DROPPED_KEYS:
                continue
            new_key = LEGACY_KEY_RENAMES.get(key, key) if isinstance(key, str) else key
            if new_key == "count_unit" and item == "action_unit":
                transformed[new_key] = COUNT_UNIT
            else:
                transformed[new_key] = _transform_value(item)
        return transformed
    if isinstance(value, list):
        return [_transform_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_transform_value(item) for item in value)
    return value


def _transform_step(step: BackendStep) -> BackendStep:
    return BackendStep(
        _transform_value(step.public_observation),
        _transform_value(step.private_state),
        step.terminal,
    )


def _transform_outcome(outcome: ActionOutcome) -> ActionOutcome:
    return ActionOutcome(
        outcome.accepted,
        _transform_value(outcome.public_feedback),
        _transform_value(outcome.private_feedback),
        outcome.error_code,
    )


class TemporalHomeBackend(WorkflowBackend):
    """WorkflowBackend subclass exposing the temporal-home public vocabulary."""

    action_kinds = PUBLIC_ACTION_KINDS

    def reset(self, episode: EpisodeSpec) -> BackendStep:
        bootstrap = episode.public_bootstrap if isinstance(episode.public_bootstrap, dict) else {}
        allowed = bootstrap.get("allowed_action_kinds")
        if (
            not isinstance(allowed, list)
            or len(allowed) != len(PUBLIC_ACTION_KINDS)
            or set(allowed) != PUBLIC_ACTION_KINDS
        ):
            raise ValueError(
                "TemporalHomeBackend requires exactly act, wait, install_rule, "
                "and cancel_rule; ask and implicit action defaults are unsupported"
            )
        return _transform_step(super().reset(episode))

    def execute_atomic(self, action: dict[str, Any]) -> ActionOutcome:
        if isinstance(action, dict) and action.get("kind") == "ask":
            return ActionOutcome(
                False,
                {"status": "rejected", "error_code": UNSUPPORTED_ACTION_ERROR},
                {"status": "rejected"},
                UNSUPPORTED_ACTION_ERROR,
            )
        return _transform_outcome(super().execute_atomic(action))

    def advance(self, triggering_action: dict[str, Any] | None = None) -> BackendStep:
        return _transform_step(super().advance(triggering_action))


__all__ = [
    "COUNT_UNIT",
    "PUBLIC_ACTION_KINDS",
    "UNSUPPORTED_ACTION_ERROR",
    "TemporalHomeBackend",
]
