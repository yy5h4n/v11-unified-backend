"""Minimal Harness V2 runtime.

The core runs exactly one policy on one Episode.  It does not score, compare
baselines, mutate queries, or decide dataset admission.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Protocol

import rfc8785


AGENT_ACTIONS = frozenset({"act", "install_rule", "cancel_rule", "ask", "wait"})
WAIT_MODES = frozenset({"for", "until", "until_event"})


class ProtocolViolation(ValueError):
    pass


def _digest(value: Any) -> str:
    return sha256(rfc8785.dumps(value)).hexdigest()


@dataclass(frozen=True)
class EpisodeSpec:
    episode_id: str
    public_bootstrap: dict[str, Any]
    seed: int
    max_decisions: int = 32


@dataclass(frozen=True)
class BackendStep:
    public_observation: dict[str, Any]
    private_state: dict[str, Any]
    terminal: bool = False


@dataclass(frozen=True)
class ActionOutcome:
    accepted: bool
    public_feedback: dict[str, Any]
    private_feedback: dict[str, Any]
    error_code: str | None = None


class Backend(Protocol):
    def reset(self, episode: EpisodeSpec) -> BackendStep: ...
    def state_digest(self) -> str: ...
    def execute_atomic(self, action: dict[str, Any]) -> ActionOutcome: ...
    def advance(self, triggering_action: dict[str, Any]) -> BackendStep: ...


class Policy(Protocol):
    def decide(self, public_view: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class RunArtifact:
    episode_id: str
    status: str
    public_trace: tuple[dict[str, Any], ...]
    private_trace: tuple[dict[str, Any], ...]
    trace_digest: str


def validate_agent_action(action: dict[str, Any], allowed_actions: set[str] | frozenset[str] = AGENT_ACTIONS) -> None:
    if not isinstance(action, dict) or action.get("kind") not in AGENT_ACTIONS:
        raise ProtocolViolation(f"unsupported agent action: {action.get('kind') if isinstance(action, dict) else type(action).__name__}")
    kind = action["kind"]
    if kind not in allowed_actions:
        raise ProtocolViolation(f"agent action is not enabled for this track: {kind}")
    required = {
        "act": {"kind", "commands"},
        "install_rule": {"kind", "rule"},
        "cancel_rule": {"kind", "rule_id"},
        "ask": {"kind", "question"},
        "wait": None,
    }[kind]
    if kind != "wait" and set(action) != required:
        raise ProtocolViolation(f"{kind} action fields must be exactly {sorted(required)}")
    if kind == "act" and not isinstance(action["commands"], list):
        raise ProtocolViolation("act.commands must be a list")
    if kind == "install_rule" and not isinstance(action["rule"], dict):
        raise ProtocolViolation("install_rule.rule must be an object")
    if kind in {"cancel_rule", "ask"} and not isinstance(action["rule_id" if kind == "cancel_rule" else "question"], str):
        raise ProtocolViolation(f"{kind} payload must be a string")
    if kind == "wait":
        _validate_wait_action(action)


def _validate_wait_action(action: dict[str, Any]) -> None:
    mode = action.get("mode")
    if mode not in WAIT_MODES:
        raise ProtocolViolation("wait.mode must be one of for, until, until_event")
    expected = {
        "for": {"kind", "mode", "duration_seconds"},
        "until": {"kind", "mode", "timestamp"},
        "until_event": {"kind", "mode", "event_filter", "timeout_seconds"},
    }[mode]
    if set(action) != expected:
        raise ProtocolViolation(f"wait mode {mode} fields must be exactly {sorted(expected)}")
    if mode == "for":
        duration = action["duration_seconds"]
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not 0 < duration <= 604800:
            raise ProtocolViolation("wait.duration_seconds must be in (0, 604800]")
    elif mode == "until":
        timestamp = action["timestamp"]
        if not isinstance(timestamp, str) or not timestamp.strip():
            raise ProtocolViolation("wait.timestamp must be a non-empty RFC3339 timestamp")
    else:
        event_filter = action["event_filter"]
        timeout = action["timeout_seconds"]
        if not isinstance(event_filter, dict) or not event_filter or any(not isinstance(key, str) or not key for key in event_filter):
            raise ProtocolViolation("wait.event_filter must be a non-empty object")
        if any(isinstance(value, (dict, list)) for value in event_filter.values()):
            raise ProtocolViolation("wait.event_filter values must be scalar")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 604800:
            raise ProtocolViolation("wait.timeout_seconds must be in (0, 604800]")


class Harness:
    """Run a single closed-loop Episode; time advances only inside this class."""

    def __init__(self, backend: Backend):
        self._backend = backend

    def run_one(self, episode: EpisodeSpec, policy: Policy) -> RunArtifact:
        step = self._backend.reset(episode)
        public_records: list[dict[str, Any]] = []
        private_records: list[dict[str, Any]] = []
        feedback: dict[str, Any] | None = None
        configured_actions = episode.public_bootstrap.get("allowed_action_kinds")
        if configured_actions is None:
            allowed_actions = AGENT_ACTIONS
        elif (
            not isinstance(configured_actions, list)
            or not configured_actions
            or any(not isinstance(item, str) for item in configured_actions)
            or not set(configured_actions) <= AGENT_ACTIONS
        ):
            raise ProtocolViolation("allowed_action_kinds must be a non-empty subset of Harness actions")
        else:
            allowed_actions = frozenset(configured_actions)

        def record_observation(index: int, current: BackendStep) -> None:
            public_records.append({"type": "observation", "index": index, "value": deepcopy(current.public_observation)})
            private_records.append({"type": "backend_state", "index": index, "value": deepcopy(current.private_state)})

        record_observation(0, step)
        decisions = 0
        status = "completed"
        while not step.terminal:
            if decisions >= episode.max_decisions:
                status = "decision_budget_exhausted"
                break
            public_view = {
                **deepcopy(episode.public_bootstrap),
                "observation": deepcopy(step.public_observation),
                "last_feedback": deepcopy(feedback),
                "allowed_actions": sorted(allowed_actions),
            }
            action = policy.decide(public_view)
            try:
                validate_agent_action(action, allowed_actions)
            except ProtocolViolation as exc:
                public_records.append({"type": "protocol_error", "index": decisions, "message": str(exc)})
                status = "protocol_invalid"
                break
            pre_digest = self._backend.state_digest()
            outcome = self._backend.execute_atomic(deepcopy(action))
            post_digest = self._backend.state_digest()
            if not outcome.accepted and pre_digest != post_digest:
                public_records.append({"type": "backend_error", "index": decisions, "message": "rejected action changed backend state"})
                status = "backend_atomicity_violation"
                break
            public_records.append({"type": "action", "index": decisions, "action": deepcopy(action), "accepted": outcome.accepted, "error_code": outcome.error_code, "feedback": deepcopy(outcome.public_feedback)})
            private_records.append({"type": "action_result", "index": decisions, "action": deepcopy(action), "accepted": outcome.accepted, "error_code": outcome.error_code, "pre_state_digest": pre_digest, "post_state_digest": post_digest, "feedback": deepcopy(outcome.private_feedback)})
            feedback = outcome.public_feedback
            decisions += 1
            if not outcome.accepted:
                continue
            pre_advance_digest = self._backend.state_digest()
            try:
                step = self._backend.advance(deepcopy(action))
            except Exception as exc:
                post_failure_digest = self._backend.state_digest()
                atomic = pre_advance_digest == post_failure_digest
                public_records.append({
                    "type": "backend_error",
                    "index": decisions,
                    "message": "backend advance failed" if atomic else "failed advance changed backend state",
                })
                private_records.append({
                    "type": "advance_error",
                    "index": decisions,
                    "error_type": type(exc).__name__,
                    "error_code": getattr(exc, "error_code", "BACKEND_ADVANCE_FAILED"),
                    "phase": getattr(exc, "phase", "unknown"),
                    "pre_state_digest": pre_advance_digest,
                    "post_state_digest": post_failure_digest,
                })
                status = "backend_advance_failed" if atomic else "backend_advance_atomicity_violation"
                break
            record_observation(decisions, step)

        sealed = {"episode_id": episode.episode_id, "status": status, "public_trace": public_records, "private_trace": private_records}
        return RunArtifact(episode.episode_id, status, tuple(public_records), tuple(private_records), _digest(sealed))
