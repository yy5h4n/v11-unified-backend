"""Code-side trust anchors for Harness V2 release conformance.

Bundle fields are evidence, never trust roots.  A caller must select a runtime
entry from this registry; the entry pins the complete executable package and
provides independent reset projection, policy execution, replay, and evaluator
manifest verification callbacks.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import platform
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

import rfc8785


ProjectReset = Callable[[dict[str, Any]], dict[str, Any]]
ReconstructReset = Callable[[str, str], dict[str, Any]]
ExecutePolicy = Callable[[dict[str, Any], list[dict[str, Any]], str, dict[str, Any]], list[dict[str, Any]]]
ReplayRun = Callable[[dict[str, Any], dict[str, Any], str, str, dict[str, Any]], dict[str, Any]]


def sha256_value(value: Any) -> str:
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def runtime_environment() -> dict[str, str]:
    return {
        "python": platform.python_version(),
        "jsonschema": importlib.metadata.version("jsonschema"),
        "rfc8785": importlib.metadata.version("rfc8785"),
    }


def package_digest(paths: Iterable[Path], *, package_id: str, version: str) -> str:
    """Hash source, schemas, and relevant runtime versions as one trust unit."""
    resolved = sorted((item.resolve() for item in paths), key=str)
    member_names = [path.name for path in resolved]
    if len(member_names) != len(set(member_names)):
        raise ValueError("trusted package member names must be unique")
    members = []
    for path in resolved:
        members.append({"name": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    descriptor = {
        "package_id": package_id,
        "version": version,
        "members": members,
        "runtime": runtime_environment(),
    }
    return sha256_value(descriptor)


@dataclass(frozen=True)
class TrustedRuntime:
    runtime_id: str
    version: str
    package_digest: str
    backend_package_digest: str
    adapter_package_digest: str
    evaluator_package_digest: str
    policy_package_digests: dict[str, str]
    policy_configs: dict[str, dict[str, Any]]
    evaluator_manifest_digests: dict[str, str]
    reconstruct_reset: ReconstructReset
    project_reset: ProjectReset
    execute_policy: ExecutePolicy
    replay: ReplayRun

    @property
    def registry_entry_digest(self) -> str:
        return sha256_value({
            "runtime_id": self.runtime_id,
            "version": self.version,
            "package_digest": self.package_digest,
            "backend_package_digest": self.backend_package_digest,
            "adapter_package_digest": self.adapter_package_digest,
            "evaluator_package_digest": self.evaluator_package_digest,
            "policy_package_digests": self.policy_package_digests,
            "policy_config_digests": {policy_id: sha256_value(config) for policy_id, config in self.policy_configs.items()},
            "evaluator_manifest_digests": self.evaluator_manifest_digests,
        })


class TrustRegistry:
    def __init__(self, entries: Iterable[TrustedRuntime]):
        materialized = list(entries)
        self._entries = {entry.runtime_id: entry for entry in materialized}
        if len(self._entries) != len(materialized):
            raise ValueError("duplicate trusted runtime id")

    def require(self, runtime_id: str) -> TrustedRuntime:
        try:
            return self._entries[runtime_id]
        except KeyError as exc:
            raise ValueError(f"untrusted runtime: {runtime_id}") from exc
