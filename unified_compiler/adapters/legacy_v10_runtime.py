"""Lazy bridge back to the frozen v10 runtime for replay.

The bridge resolves the original public/private episode pair(s) for a migrated
``PhysicalProcess`` and can instantiate the existing v10 runtime
(``HVACRuntime`` / ``EVRuntime``) on demand. The v10 runtime module is imported
only inside the replay path (:meth:`V10RuntimeBridge.runtime_for_episode` and
:meth:`V10RuntimeBridge.replay`); scanning and pair resolution never touch any
simulator environment.

``replay`` drives one episode to termination with caller-supplied decisions
only — an explicit policy callback or an explicit action sequence. Actions are
validated against the public ``allowed_actions`` schema; the hidden Contract
is never inspected and no gold actions are ever synthesized.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from typing import Any, Callable, Mapping, Sequence

from .legacy_v10 import DEFAULT_V10_ROOT, LegacyArtifactError, V10ArtifactIndex

V10_RUNTIME_MODULE_NAME = "v10_runtime_for_v11_replay"


class UnknownEpisodeError(LookupError):
    pass


class UnknownLegacyProcessError(LookupError):
    pass


class ReplayActionError(ValueError):
    """Caller-supplied actions are missing, malformed, or not publicly legal."""


class ReplayError(RuntimeError):
    """The runtime failed to reach termination within the allowed steps."""


class V10RuntimeBridge:
    def __init__(self, index: V10ArtifactIndex | None = None) -> None:
        self._index = index if index is not None else V10ArtifactIndex()

    @property
    def index(self) -> V10ArtifactIndex:
        return self._index

    def episode_ids_for_process(self, process_id: str) -> tuple[str, ...]:
        self._index.ensure_loaded()
        try:
            return self._index.episode_ids(process_id)
        except KeyError:
            raise UnknownLegacyProcessError(process_id) from None

    def resolve_pair(self, episode_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        """Return the original (public, private) record pair for one episode."""
        self._index.ensure_loaded()
        try:
            return self._index.pair(episode_id)
        except KeyError:
            raise UnknownEpisodeError(episode_id) from None

    def resolve_pairs(
        self,
        process_id: str | None = None,
        episode_ids: Sequence[str] | None = None,
    ) -> tuple[tuple[dict[str, Any], dict[str, Any]], ...]:
        if process_id is not None:
            episode_ids = self.episode_ids_for_process(process_id)
        if not episode_ids:
            raise UnknownEpisodeError("no episode_ids given or resolvable")
        return tuple(self.resolve_pair(episode_id) for episode_id in episode_ids)

    def runtime_for_episode(self, episode_id: str) -> Any:
        """Instantiate the frozen v10 runtime for one episode, lazily."""
        public, private = self.resolve_pair(episode_id)
        runtime_module = _load_v10_runtime()
        backend = private["backend_binding"]["backend"]
        if backend == "CityLearn":
            return runtime_module.HVACRuntime(public, private)
        if backend == "EV2Gym":
            return runtime_module.EVRuntime(public, private)
        raise LegacyArtifactError(f"no v10 runtime for backend {backend!r}")

    def replay(
        self,
        episode_id: str,
        *,
        policy: Callable[[int, dict[str, Any]], Any] | None = None,
        actions: Sequence[Any] | None = None,
        max_steps: int | None = None,
    ) -> dict[str, Any]:
        """Run one episode to termination and return a serializable result.

        Exactly one of ``policy`` or ``actions`` drives the episode:

        - ``policy(step_index, observation)`` is called each step with the
          current public observation and returns the action to apply.
        - ``actions`` is an explicit action sequence consumed one per step; it
          must be exactly as long as the episode.

        Every action is checked against the episode's public
        ``allowed_actions`` schema before being applied. The hidden Contract
        is never inspected and no actions are synthesized beyond what the
        caller supplies.

        The result is a plain JSON-serializable dict: the per-step transition
        records plus completion metadata (steps taken, horizon, termination
        flags). It contains no Contract clauses and no gold actions.
        """
        if (policy is None) == (actions is None):
            raise ReplayActionError("exactly one of policy= or actions= is required")
        public, private = self.resolve_pair(episode_id)
        allowed_actions = public.get("allowed_actions") or {}
        if not isinstance(allowed_actions, Mapping):
            raise LegacyArtifactError(f"{episode_id}: malformed public allowed_actions")
        horizon_steps = int(public["horizon_steps"])
        step_limit = horizon_steps if max_steps is None else int(max_steps)
        if step_limit < 0:
            raise ReplayActionError("max_steps must be non-negative")

        require_mapping = _schema_declares_parameters(allowed_actions)

        action_sequence: list[Any] | None = None
        if actions is not None:
            try:
                action_sequence = list(actions)
            except TypeError:
                raise ReplayActionError("actions must be an iterable of actions") from None
            for index, action in enumerate(action_sequence):
                _validate_action(action, allowed_actions, index, require_mapping)

        runtime = self.runtime_for_episode(episode_id)
        transitions: list[dict[str, Any]] = []
        while not runtime.done:
            step_index = len(transitions)
            if step_index >= step_limit:
                raise ReplayError(
                    f"episode {episode_id} not done after {step_index} steps "
                    f"(limit {step_limit})"
                )
            if action_sequence is not None:
                if step_index >= len(action_sequence):
                    raise ReplayActionError(
                        f"action sequence exhausted at step {step_index} "
                        "before the episode terminated"
                    )
                action = action_sequence[step_index]
            else:
                observation = _jsonable(runtime.observe())
                action = policy(step_index, observation)
                _validate_action(action, allowed_actions, step_index, require_mapping)
            transitions.append(_jsonable(runtime.step(action)))
        if action_sequence is not None and len(action_sequence) != len(transitions):
            raise ReplayActionError(
                f"action sequence has {len(action_sequence)} entries but the episode "
                f"terminated after {len(transitions)} steps"
            )

        result = {
            "episode_id": episode_id,
            "physical_process_id": private["physical_process_id"],
            "backend": private["backend_binding"]["backend"],
            "action_source": "actions" if action_sequence is not None else "policy",
            "horizon_steps": horizon_steps,
            "steps_completed": len(transitions),
            "episode_done": bool(runtime.done),
            "backend_terminated": bool(transitions[-1].get("backend_terminated", False))
            if transitions
            else False,
            "completed": bool(runtime.done) and len(transitions) == horizon_steps,
            "transitions": transitions,
        }
        json.dumps(result)
        return result


def _schema_declares_parameters(allowed_actions: Mapping[str, Any]) -> bool:
    """Whether the public schema declares numeric parameters for any action type."""
    for spec in allowed_actions.values():
        if not isinstance(spec, Mapping):
            continue
        for constraint in spec.values():
            if isinstance(constraint, Mapping) and (
                constraint.get("minimum") is not None or constraint.get("maximum") is not None
            ):
                return True
    return False


def _validate_action(
    action: Any,
    allowed_actions: Mapping[str, Any],
    index: int,
    require_mapping: bool,
) -> None:
    """Fail closed unless ``action`` matches the public action schema's shape."""
    if isinstance(action, str):
        if require_mapping:
            raise ReplayActionError(
                f"step {index}: action must be a mapping with a declared 'type'"
            )
        action_type, params = action, None
    elif isinstance(action, Mapping):
        if not require_mapping:
            raise ReplayActionError(
                f"step {index}: action must be a declared mode string, not a mapping"
            )
        action_type, params = action.get("type"), action
    else:
        raise ReplayActionError(f"step {index}: action must be a string or mapping")
    spec = allowed_actions.get(action_type) if isinstance(action_type, str) else None
    if spec is None:
        raise ReplayActionError(
            f"step {index}: action type {action_type!r} is not in public allowed_actions"
        )
    if not isinstance(spec, Mapping):
        return
    for name, constraint in spec.items():
        if not isinstance(constraint, Mapping):
            continue
        minimum = constraint.get("minimum")
        maximum = constraint.get("maximum")
        if minimum is None and maximum is None:
            continue
        raw = params.get(name) if isinstance(params, Mapping) else None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise ReplayActionError(
                f"step {index}: action {action_type!r} requires numeric parameter {name!r}"
            ) from None
        if minimum is not None and value < float(minimum) - 1e-9:
            raise ReplayActionError(
                f"step {index}: parameter {name!r}={value} below minimum {minimum}"
            )
        if maximum is not None and value > float(maximum) + 1e-9:
            raise ReplayActionError(
                f"step {index}: parameter {name!r}={value} above maximum {maximum}"
            )


def _jsonable(value: Any) -> Any:
    """Project runtime records onto plain JSON types (never adding content)."""
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    item = getattr(value, "item", None)
    if callable(item):
        return item()
    raise ReplayError(f"non-serializable value of type {type(value).__name__} in replay record")


def _load_v10_runtime() -> Any:
    module = sys.modules.get(V10_RUNTIME_MODULE_NAME)
    if module is not None:
        return module
    runtime_path = DEFAULT_V10_ROOT / "runtime.py"
    if not runtime_path.is_file():
        raise LegacyArtifactError(f"missing frozen v10 runtime: {runtime_path}")
    spec = importlib.util.spec_from_file_location(V10_RUNTIME_MODULE_NAME, runtime_path)
    if spec is None or spec.loader is None:
        raise LegacyArtifactError(f"unable to load frozen v10 runtime: {runtime_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[V10_RUNTIME_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module
