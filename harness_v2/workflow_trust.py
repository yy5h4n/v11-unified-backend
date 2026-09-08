"""Fail-closed trust helpers for compact formal Workflow releases.

This module deliberately does not import the formal-release builder.  A
validator must construct a :class:`TrustedWorkflowRuntime` from code-side
registrations and then recompute reset, policy execution, and evaluation.
Bundle fields are evidence and never become executable registrations.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .core import Backend, EpisodeSpec, Harness, Policy, RunArtifact
from .semantic_validator import ConformanceError
from .trust_evidence import decode_serialization, serialization
from .trust_registry import package_digest, sha256_value


BackendFactory = Callable[[], Backend]
PolicyFactory = Callable[[dict[str, Any]], Policy]
Evaluator = Callable[[str, RunArtifact], dict[str, Any]]


def _without_receipt_identity(value: Any) -> Any:
    """Remove opaque receipt ids that must not create process diversity."""

    if isinstance(value, dict):
        return {
            key: _without_receipt_identity(item)
            for key, item in value.items()
            if key not in {"instance_id", "command_id", "rule_id"}
        }
    if isinstance(value, list):
        return [_without_receipt_identity(item) for item in value]
    return value


def causal_environment_digest(run: RunArtifact) -> str:
    """Hash the realized causal environment without Query/episode/receipt ids.

    The digest deliberately excludes action transcripts and opaque identifiers.
    In particular, changing a seed, Query paraphrase, or irrelevant global
    config cannot make an otherwise identical no-op process count as new.
    """

    public_observations = []
    for row in run.public_trace:
        if row.get("type") != "observation":
            continue
        value = row.get("value", {})
        public_observations.append(_without_receipt_identity({
            "time": value.get("time"),
            "step": value.get("step"),
            "devices": value.get("devices"),
            "events": value.get("events", []),
        }))
    private_frames = []
    for row in run.private_trace:
        if row.get("type") != "backend_state":
            continue
        value = row.get("value", {})
        private_frames.append(_without_receipt_identity({
            "step": value.get("step"),
            "devices": value.get("devices"),
            "household": value.get("household"),
            "workflow": value.get("workflow"),
            "active_tasks": value.get("active_tasks"),
            "completed_tasks": value.get("completed_tasks"),
            "exogenous_pending": value.get("exogenous_pending"),
        }))
    return sha256_value({"public_observations": public_observations, "private_frames": private_frames})


@dataclass(frozen=True)
class TrustedWorkflowPolicy:
    policy_id: str
    package_digest: str
    config: dict[str, Any]
    factory: PolicyFactory

    @property
    def config_digest(self) -> str:
        return sha256_value(self.config)


@dataclass(frozen=True)
class TrustedWorkflowEvaluator:
    evaluator_id: str
    package_digest: str
    manifest: dict[str, Any]
    evaluate: Evaluator

    @property
    def manifest_digest(self) -> str:
        return sha256_value(self.manifest)


@dataclass(frozen=True)
class TrustedWorkflowRuntime:
    runtime_id: str
    version: str
    package_digest: str
    backend_package_digest: str
    harness_package_digest: str
    backend_factory: BackendFactory
    policies: dict[str, TrustedWorkflowPolicy]
    evaluators: dict[str, TrustedWorkflowEvaluator]

    def __post_init__(self) -> None:
        if not self.runtime_id or not self.version:
            raise ValueError("trusted workflow runtime id and version must be non-empty")
        if set(self.policies) != {item.policy_id for item in self.policies.values()}:
            raise ValueError("workflow policy registry keys must equal policy ids")
        if set(self.evaluators) != {item.evaluator_id for item in self.evaluators.values()}:
            raise ValueError("workflow evaluator registry keys must equal evaluator ids")

    @property
    def registry_entry_digest(self) -> str:
        return sha256_value({
            "runtime_id": self.runtime_id,
            "version": self.version,
            "package_digest": self.package_digest,
            "backend_package_digest": self.backend_package_digest,
            "harness_package_digest": self.harness_package_digest,
            "policies": {
                policy_id: {
                    "package_digest": item.package_digest,
                    "config_digest": item.config_digest,
                }
                for policy_id, item in sorted(self.policies.items())
            },
            "evaluators": {
                evaluator_id: {
                    "package_digest": item.package_digest,
                    "manifest_digest": item.manifest_digest,
                }
                for evaluator_id, item in sorted(self.evaluators.items())
            },
        })

    def require_policy(self, policy_id: str) -> TrustedWorkflowPolicy:
        try:
            return self.policies[policy_id]
        except KeyError as exc:
            raise ConformanceError(f"untrusted workflow policy: {policy_id}") from exc

    def require_evaluator(self, evaluator_id: str) -> TrustedWorkflowEvaluator:
        try:
            return self.evaluators[evaluator_id]
        except KeyError as exc:
            raise ConformanceError(f"untrusted workflow evaluator: {evaluator_id}") from exc


class WorkflowTrustRegistry:
    """Code-side registry; callers must never populate it from release data."""

    def __init__(self, entries: Iterable[TrustedWorkflowRuntime]):
        materialized = list(entries)
        self._entries = {entry.runtime_id: entry for entry in materialized}
        if len(self._entries) != len(materialized):
            raise ValueError("duplicate trusted workflow runtime id")

    def require(self, runtime_id: str) -> TrustedWorkflowRuntime:
        try:
            return self._entries[runtime_id]
        except KeyError as exc:
            raise ConformanceError(f"untrusted workflow runtime: {runtime_id}") from exc


def episode_spec_value(episode: EpisodeSpec) -> dict[str, Any]:
    return {
        "episode_id": episode.episode_id,
        "public_bootstrap": deepcopy(episode.public_bootstrap),
        "seed": episode.seed,
        "max_decisions": episode.max_decisions,
    }


def episode_spec_digest(episode: EpisodeSpec) -> str:
    return sha256_value(episode_spec_value(episode))


def run_artifact_value(run: RunArtifact) -> dict[str, Any]:
    return {
        "episode_id": run.episode_id,
        "status": run.status,
        "public_trace": deepcopy(list(run.public_trace)),
        "private_trace": deepcopy(list(run.private_trace)),
        "trace_digest": run.trace_digest,
    }


def _recompute_trace_digest(value: dict[str, Any]) -> str:
    return sha256_value({
        "episode_id": value["episode_id"],
        "status": value["status"],
        "public_trace": value["public_trace"],
        "private_trace": value["private_trace"],
    })


def seal_run_artifact(run: RunArtifact) -> dict[str, Any]:
    body = run_artifact_value(run)
    if body["trace_digest"] != _recompute_trace_digest(body):
        raise ValueError("RunArtifact carries an invalid trace digest")
    sealed = serialization(body)
    return {
        "artifact": body,
        "serialization": sealed,
        "artifact_digest": sealed["digest"],
    }


def verify_sealed_run_artifact(sealed: dict[str, Any], *, expected_episode_id: str | None = None) -> RunArtifact:
    if set(sealed) != {"artifact", "serialization", "artifact_digest"}:
        raise ConformanceError("sealed workflow run has missing or unknown fields")
    decoded = decode_serialization(sealed["serialization"], "workflow RunArtifact")
    if decoded != sealed["artifact"] or sealed["artifact_digest"] != sealed["serialization"]["digest"]:
        raise ConformanceError("workflow RunArtifact serialization binding mismatch")
    value = sealed["artifact"]
    required = {"episode_id", "status", "public_trace", "private_trace", "trace_digest"}
    if not isinstance(value, dict) or set(value) != required:
        raise ConformanceError("workflow RunArtifact shape mismatch")
    if expected_episode_id is not None and value["episode_id"] != expected_episode_id:
        raise ConformanceError("workflow RunArtifact episode mismatch")
    if value["trace_digest"] != _recompute_trace_digest(value):
        raise ConformanceError("workflow RunArtifact trace digest mismatch")
    if not isinstance(value["public_trace"], list) or not isinstance(value["private_trace"], list):
        raise ConformanceError("workflow RunArtifact traces must be arrays")
    return RunArtifact(
        episode_id=value["episode_id"],
        status=value["status"],
        public_trace=tuple(deepcopy(value["public_trace"])),
        private_trace=tuple(deepcopy(value["private_trace"])),
        trace_digest=value["trace_digest"],
    )


def _runtime_bindings(runtime: TrustedWorkflowRuntime) -> dict[str, str]:
    return {
        "runtime_id": runtime.runtime_id,
        "runtime_version": runtime.version,
        "registry_entry_digest": runtime.registry_entry_digest,
        "runtime_package_digest": runtime.package_digest,
        "backend_package_digest": runtime.backend_package_digest,
        "harness_package_digest": runtime.harness_package_digest,
    }


def build_trusted_reset_receipt(runtime: TrustedWorkflowRuntime, episode: EpisodeSpec) -> dict[str, Any]:
    backend = runtime.backend_factory()
    initial = backend.reset(episode)
    body = {
        **_runtime_bindings(runtime),
        "episode_spec": serialization(episode_spec_value(episode)),
        "episode_spec_digest": episode_spec_digest(episode),
        "initial_public_observation": serialization(initial.public_observation),
        "initial_private_state": serialization(initial.private_state),
        "initial_terminal": initial.terminal,
        "initial_backend_state_digest": backend.state_digest(),
    }
    return {**body, "receipt_digest": sha256_value(body)}


def verify_trusted_reset_receipt(
    receipt: dict[str, Any],
    runtime: TrustedWorkflowRuntime,
    episode: EpisodeSpec,
) -> None:
    required = {
        *_runtime_bindings(runtime),
        "episode_spec", "episode_spec_digest", "initial_public_observation",
        "initial_private_state", "initial_terminal", "initial_backend_state_digest",
        "receipt_digest",
    }
    if set(receipt) != required:
        raise ConformanceError("trusted workflow reset receipt has missing or unknown fields")
    for field, expected in _runtime_bindings(runtime).items():
        if receipt[field] != expected:
            raise ConformanceError(f"trusted workflow reset binding mismatch: {field}")
    body = {key: value for key, value in receipt.items() if key != "receipt_digest"}
    if receipt["receipt_digest"] != sha256_value(body):
        raise ConformanceError("trusted workflow reset receipt digest mismatch")
    decoded_spec = decode_serialization(receipt["episode_spec"], "workflow EpisodeSpec")
    expected_spec = episode_spec_value(episode)
    if decoded_spec != expected_spec or receipt["episode_spec_digest"] != sha256_value(expected_spec):
        raise ConformanceError("trusted workflow reset EpisodeSpec mismatch")
    backend = runtime.backend_factory()
    initial = backend.reset(episode)
    if decode_serialization(receipt["initial_public_observation"], "workflow initial public observation") != initial.public_observation:
        raise ConformanceError("trusted workflow initial public observation mismatch")
    if decode_serialization(receipt["initial_private_state"], "workflow initial private state") != initial.private_state:
        raise ConformanceError("trusted workflow initial private state mismatch")
    if receipt["initial_terminal"] != initial.terminal:
        raise ConformanceError("trusted workflow initial terminal flag mismatch")
    if receipt["initial_backend_state_digest"] != backend.state_digest():
        raise ConformanceError("trusted workflow initial backend state digest mismatch")


def run_trusted_policy(
    runtime: TrustedWorkflowRuntime,
    episode: EpisodeSpec,
    policy_id: str,
) -> RunArtifact:
    registration = runtime.require_policy(policy_id)
    policy = registration.factory(deepcopy(registration.config))
    return Harness(runtime.backend_factory()).run_one(episode, policy)


def build_trusted_replay_receipt(
    runtime: TrustedWorkflowRuntime,
    episode: EpisodeSpec,
    policy_id: str,
    *,
    reset_receipt_digest: str,
) -> dict[str, Any]:
    policy = runtime.require_policy(policy_id)
    run = run_trusted_policy(runtime, episode, policy_id)
    sealed_run = seal_run_artifact(run)
    body = {
        **_runtime_bindings(runtime),
        "episode_spec_digest": episode_spec_digest(episode),
        "reset_receipt_digest": reset_receipt_digest,
        "policy_id": policy.policy_id,
        "policy_package_digest": policy.package_digest,
        "policy_config_digest": policy.config_digest,
        "sealed_run": sealed_run,
    }
    return {**body, "receipt_digest": sha256_value(body)}


def verify_trusted_replay_receipt(
    receipt: dict[str, Any],
    runtime: TrustedWorkflowRuntime,
    episode: EpisodeSpec,
    *,
    expected_policy_id: str,
    expected_reset_receipt_digest: str,
) -> RunArtifact:
    policy = runtime.require_policy(expected_policy_id)
    required = {
        *_runtime_bindings(runtime),
        "episode_spec_digest", "reset_receipt_digest", "policy_id",
        "policy_package_digest", "policy_config_digest", "sealed_run",
        "receipt_digest",
    }
    if set(receipt) != required:
        raise ConformanceError("trusted workflow replay receipt has missing or unknown fields")
    for field, expected in _runtime_bindings(runtime).items():
        if receipt[field] != expected:
            raise ConformanceError(f"trusted workflow replay binding mismatch: {field}")
    expected_bindings = {
        "episode_spec_digest": episode_spec_digest(episode),
        "reset_receipt_digest": expected_reset_receipt_digest,
        "policy_id": expected_policy_id,
        "policy_package_digest": policy.package_digest,
        "policy_config_digest": policy.config_digest,
    }
    for field, expected in expected_bindings.items():
        if receipt[field] != expected:
            raise ConformanceError(f"trusted workflow replay binding mismatch: {field}")
    body = {key: value for key, value in receipt.items() if key != "receipt_digest"}
    if receipt["receipt_digest"] != sha256_value(body):
        raise ConformanceError("trusted workflow replay receipt digest mismatch")
    sealed = verify_sealed_run_artifact(receipt["sealed_run"], expected_episode_id=episode.episode_id)
    expected = run_trusted_policy(runtime, episode, expected_policy_id)
    if run_artifact_value(sealed) != run_artifact_value(expected):
        raise ConformanceError("trusted deterministic workflow replay mismatch")
    return sealed


def build_trusted_evaluation_receipt(
    runtime: TrustedWorkflowRuntime,
    evaluator_id: str,
    scenario_type: str,
    run: RunArtifact,
) -> dict[str, Any]:
    evaluator = runtime.require_evaluator(evaluator_id)
    result = evaluator.evaluate(scenario_type, run)
    body = {
        **_runtime_bindings(runtime),
        "evaluator_id": evaluator.evaluator_id,
        "evaluator_package_digest": evaluator.package_digest,
        "evaluator_manifest_digest": evaluator.manifest_digest,
        "scenario_type": scenario_type,
        "trace_digest": run.trace_digest,
        "result": serialization(result),
    }
    return {**body, "receipt_digest": sha256_value(body)}


def verify_trusted_evaluation_receipt(
    receipt: dict[str, Any],
    runtime: TrustedWorkflowRuntime,
    evaluator_id: str,
    scenario_type: str,
    run: RunArtifact,
) -> dict[str, Any]:
    evaluator = runtime.require_evaluator(evaluator_id)
    required = {
        *_runtime_bindings(runtime),
        "evaluator_id", "evaluator_package_digest", "evaluator_manifest_digest",
        "scenario_type", "trace_digest", "result", "receipt_digest",
    }
    if set(receipt) != required:
        raise ConformanceError("trusted workflow evaluation receipt has missing or unknown fields")
    for field, expected in _runtime_bindings(runtime).items():
        if receipt[field] != expected:
            raise ConformanceError(f"trusted workflow evaluation binding mismatch: {field}")
    expected_bindings = {
        "evaluator_id": evaluator_id,
        "evaluator_package_digest": evaluator.package_digest,
        "evaluator_manifest_digest": evaluator.manifest_digest,
        "scenario_type": scenario_type,
        "trace_digest": run.trace_digest,
    }
    for field, expected in expected_bindings.items():
        if receipt[field] != expected:
            raise ConformanceError(f"trusted workflow evaluation binding mismatch: {field}")
    body = {key: value for key, value in receipt.items() if key != "receipt_digest"}
    if receipt["receipt_digest"] != sha256_value(body):
        raise ConformanceError("trusted workflow evaluation receipt digest mismatch")
    supplied = decode_serialization(receipt["result"], "workflow evaluation result")
    expected = evaluator.evaluate(scenario_type, run)
    if supplied != expected:
        raise ConformanceError("trusted workflow evaluation recomputation mismatch")
    return supplied


__all__ = [
    "TrustedWorkflowEvaluator", "TrustedWorkflowPolicy", "TrustedWorkflowRuntime",
    "WorkflowTrustRegistry", "build_trusted_evaluation_receipt",
    "build_trusted_replay_receipt", "build_trusted_reset_receipt",
    "episode_spec_digest", "episode_spec_value", "run_artifact_value",
    "run_trusted_policy", "seal_run_artifact", "verify_sealed_run_artifact",
    "verify_trusted_evaluation_receipt", "verify_trusted_replay_receipt",
    "verify_trusted_reset_receipt",
]
