"""Migrate the frozen v10 physical process inventory into the ProcessAdapter protocol.

Reads the already-built v10 artifacts (episodes_public.jsonl / episodes_private.jsonl)
from the sibling ``../v10_diversity_aware_compiler`` project, deduplicates by
private ``physical_process_id``, and exposes one canonical ``PhysicalProcess`` per
unique process. Scans never reconstruct a simulator environment; no files are
written here. Hidden Contract clauses and gold-action payloads are never copied
into a process manifest.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, ClassVar, Mapping, Sequence

from ..types import BackendCapability, CapabilityStatus, PhysicalProcess, ProcessRequirement

V11_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_V10_ROOT = (V11_ROOT.parent / "v10_diversity_aware_compiler").resolve()
DEFAULT_ARTIFACT_DIR = DEFAULT_V10_ROOT / "generated" / "diversity_pilot_v1"

PUBLIC_FILENAME = "episodes_public.jsonl"
PRIVATE_FILENAME = "episodes_private.jsonl"

_SHA_FIELDS = (
    "source_trace_sha256",
    "thermal_model_sha256",
    "config_sha256",
    "load_source_sha256",
    "price_source_sha256",
)
_SECRET_TOKENS = ("password", "secret", "token", "credential")
_BULK_TRACE_FIELDS = ("load_trace_kw", "price_trace_eur_per_kwh")


class LegacyArtifactError(RuntimeError):
    """Raised when the frozen v10 artifacts are absent or inconsistent."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in _SECRET_TOKENS)


def _source_hash(binding: Mapping[str, Any]) -> str:
    """Stable SHA-256 over non-secret provenance.

    Prefers the digests already recorded by v10; otherwise hashes the
    backend-binding description itself (never any secret field).
    """
    present = {k: binding[k] for k in _SHA_FIELDS if isinstance(binding.get(k), str)}
    if present:
        payload = {
            "v10_source_digests": present,
            "v10_binding_description": _non_secret_provenance(binding),
        }
    else:
        payload = {
            "v10_backend_binding_description": {
                k: v for k, v in sorted(binding.items()) if not _is_secret_key(k)
            }
        }
    return "sha256:" + hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _non_secret_provenance(binding: Mapping[str, Any]) -> dict[str, Any]:
    """Project-safe binding summary: scalar provenance + digests, no paths/traces."""
    summary: dict[str, Any] = {}
    for key in sorted(binding):
        if _is_secret_key(key) or key.endswith("_path") or key in _BULK_TRACE_FIELDS:
            continue
        value = binding[key]
        if isinstance(value, (str, int, float, bool)):
            summary[key] = value
        elif key == "source" and isinstance(value, Mapping):
            summary[key] = {
                sk: sv for sk, sv in sorted(value.items())
                if isinstance(sv, (str, int, float, bool)) and not _is_secret_key(sk)
            }
    return summary


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise LegacyArtifactError(
                    f"{path.name}:{line_number} is not valid JSON: {exc}"
                ) from exc
            if not isinstance(record, dict) or not isinstance(record.get("episode_id"), str):
                raise LegacyArtifactError(
                    f"{path.name}:{line_number} lacks a string episode_id"
                )
            records.append(record)
    return records


class V10ArtifactIndex:
    """One-shot, read-only index over the frozen v10 episode artifacts."""

    def __init__(self, artifact_dir: Path | str | None = None) -> None:
        self._artifact_dir = Path(artifact_dir) if artifact_dir else DEFAULT_ARTIFACT_DIR
        self._loaded = False
        self.load_count = 0
        self._public_by_episode: dict[str, dict[str, Any]] = {}
        self._private_by_episode: dict[str, dict[str, Any]] = {}
        self._episodes_by_process: dict[str, tuple[str, ...]] = {}

    @property
    def artifact_dir(self) -> Path:
        return self._artifact_dir

    def ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._load()
        self._loaded = True

    def _load(self) -> None:
        public_path = self._artifact_dir / PUBLIC_FILENAME
        private_path = self._artifact_dir / PRIVATE_FILENAME
        for path in (public_path, private_path):
            if not path.is_file():
                raise LegacyArtifactError(f"missing frozen v10 artifact: {path}")
        public_records = _read_jsonl(public_path)
        private_records = _read_jsonl(private_path)
        self.load_count += 1

        public_by_episode = {record["episode_id"]: record for record in public_records}
        private_by_episode = {record["episode_id"]: record for record in private_records}
        if len(public_by_episode) != len(public_records) or len(private_by_episode) != len(private_records):
            raise LegacyArtifactError("duplicate episode_id within a frozen v10 artifact")
        if set(public_by_episode) != set(private_by_episode):
            missing_public = sorted(set(private_by_episode) - set(public_by_episode))
            missing_private = sorted(set(public_by_episode) - set(private_by_episode))
            raise LegacyArtifactError(
                "public/private episode sets diverge "
                f"(missing public: {missing_public[:3]}, missing private: {missing_private[:3]})"
            )

        episodes_by_process: dict[str, list[str]] = {}
        bindings: dict[str, str] = {}
        public_shapes: dict[str, str] = {}
        for episode_id in sorted(private_by_episode):
            private = private_by_episode[episode_id]
            process_id = private.get("physical_process_id")
            binding = private.get("backend_binding")
            if not isinstance(process_id, str) or not process_id:
                raise LegacyArtifactError(f"{episode_id}: missing physical_process_id")
            if not isinstance(binding, Mapping) or not isinstance(binding.get("backend"), str):
                raise LegacyArtifactError(f"{episode_id}: missing backend_binding.backend")
            episodes_by_process.setdefault(process_id, []).append(episode_id)

            binding_fingerprint = _canonical_json(binding)
            prior = bindings.setdefault(process_id, binding_fingerprint)
            if prior != binding_fingerprint:
                raise LegacyArtifactError(
                    f"process {process_id!r} has inconsistent backend_binding across episodes"
                )

            public = public_by_episode[episode_id]
            shape = _canonical_json(
                {
                    "allowed_actions": sorted(public.get("allowed_actions", {})),
                    "horizon_steps": public.get("horizon_steps"),
                    "observation": sorted(public.get("initial_observation", {})),
                    "observation_interval_minutes": public.get("observation_interval_minutes"),
                }
            )
            prior_shape = public_shapes.setdefault(process_id, shape)
            if prior_shape != shape:
                raise LegacyArtifactError(
                    f"process {process_id!r} has inconsistent public episode shape across episodes"
                )

        self._public_by_episode = public_by_episode
        self._private_by_episode = private_by_episode
        self._episodes_by_process = {
            pid: tuple(sorted(episodes)) for pid, episodes in sorted(episodes_by_process.items())
        }

    @property
    def process_ids(self) -> tuple[str, ...]:
        self.ensure_loaded()
        return tuple(self._episodes_by_process)

    def episode_ids(self, process_id: str) -> tuple[str, ...]:
        self.ensure_loaded()
        return self._episodes_by_process[process_id]

    def private_record(self, episode_id: str) -> dict[str, Any]:
        self.ensure_loaded()
        return self._private_by_episode[episode_id]

    def public_record(self, episode_id: str) -> dict[str, Any]:
        self.ensure_loaded()
        return self._public_by_episode[episode_id]

    def pair(self, episode_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
        self.ensure_loaded()
        return self._public_by_episode[episode_id], self._private_by_episode[episode_id]


class _BaseV10Adapter:
    """Shared scan behaviour for the two frozen v10 backends."""

    backend: ClassVar[str]
    capability: ClassVar[BackendCapability]
    domain: ClassVar[str]

    def __init__(self, index: V10ArtifactIndex | None = None, artifact_dir: Path | str | None = None) -> None:
        self._index = index if index is not None else V10ArtifactIndex(artifact_dir)
        self._processes: tuple[PhysicalProcess, ...] | None = None
        self.scan_calls = 0

    @property
    def index(self) -> V10ArtifactIndex:
        return self._index

    @property
    def artifact_ref(self) -> str:
        return os.path.relpath(self._index.artifact_dir, V11_ROOT)

    def capabilities(self) -> Sequence[BackendCapability]:
        return (self.capability,)

    def processes(self) -> tuple[PhysicalProcess, ...]:
        if self._processes is None:
            self._processes = self._build_processes()
        return self._processes

    def scan(self, requirements: Sequence[ProcessRequirement]) -> Sequence[PhysicalProcess]:
        """Return canonical processes matching any requirement; loads artifacts once."""
        self.scan_calls += 1
        processes = self.processes()
        if not requirements:
            return ()
        return tuple(
            process
            for process in processes
            if any(req.required_capabilities <= process.provided_capabilities for req in requirements)
        )

    def _build_processes(self) -> tuple[PhysicalProcess, ...]:
        self._index.ensure_loaded()
        built: list[PhysicalProcess] = []
        for process_id in self._index.process_ids:
            episode_ids = self._index.episode_ids(process_id)
            private = self._index.private_record(episode_ids[0])
            binding = private["backend_binding"]
            if binding["backend"] != self.backend:
                continue
            public = self._index.public_record(episode_ids[0])
            built.append(self._make_process(process_id, episode_ids, public, private))
        if not built:
            raise LegacyArtifactError(
                f"no processes for backend {self.backend!r} in {self._index.artifact_dir}"
            )
        return tuple(built)

    def _make_process(
        self,
        process_id: str,
        episode_ids: Sequence[str],
        public: Mapping[str, Any],
        private: Mapping[str, Any],
    ) -> PhysicalProcess:
        binding = private["backend_binding"]
        manifest = {
            "migration": "legacy_v10",
            "artifact_ref": self.artifact_ref,
            "binding_ref": f"{PRIVATE_FILENAME}#physical_process_id={process_id}",
            "legacy_episode_ids": list(sorted(episode_ids)),
            "provenance": _non_secret_provenance(binding),
            "lifecycle": private.get("lifecycle", {}),
        }
        return PhysicalProcess(
            process_id=process_id,
            domain=self.domain,
            backend=self.backend,
            backend_version=self._backend_version(binding),
            source_id=self._source_id(binding),
            source_hash=_source_hash(binding),
            horizon_steps=int(public["horizon_steps"]),
            observation_interval_seconds=float(public["observation_interval_minutes"]) * 60.0,
            provided_capabilities=frozenset(self.capability.provides),
            state_variables=tuple(sorted(public.get("initial_observation", {}))),
            action_types=tuple(sorted(public.get("allowed_actions", {}))),
            manifest=manifest,
        )

    def _backend_version(self, binding: Mapping[str, Any]) -> str:
        raise NotImplementedError

    def _source_id(self, binding: Mapping[str, Any]) -> str:
        raise NotImplementedError


class CityLearnV10Adapter(_BaseV10Adapter):
    backend: ClassVar[str] = "CityLearn"
    domain: ClassVar[str] = "hvac"
    capability: ClassVar[BackendCapability] = BackendCapability(
        capability_id="citylearn_v10.thermal",
        backend="CityLearn",
        provides=("thermal.zone_temperature", "thermal.hvac_action"),
        status=CapabilityStatus.LEGACY_EXECUTABLE_PENDING_MIGRATION,
        evidence=(
            "prototypes/v10_diversity_aware_compiler/generated/diversity_pilot_v1/episodes_private.jsonl",
            "prototypes/v10_diversity_aware_compiler/runtime.py (HVACRuntime)",
        ),
    )

    def _backend_version(self, binding: Mapping[str, Any]) -> str:
        return str(binding.get("version", "unknown"))

    def _source_id(self, binding: Mapping[str, Any]) -> str:
        return f"citylearn-resstock://{binding['building_id']}"


class EV2GymV10Adapter(_BaseV10Adapter):
    backend: ClassVar[str] = "EV2Gym"
    domain: ClassVar[str] = "ev_charging"
    capability: ClassVar[BackendCapability] = BackendCapability(
        capability_id="ev2gym_v10.ev",
        backend="EV2Gym",
        provides=("ev.soc", "ev.charge_action"),
        status=CapabilityStatus.LEGACY_EXECUTABLE_PENDING_MIGRATION,
        evidence=(
            "prototypes/v10_diversity_aware_compiler/generated/diversity_pilot_v1/episodes_private.jsonl",
            "prototypes/v10_diversity_aware_compiler/runtime.py (EVRuntime)",
        ),
    )

    def _backend_version(self, binding: Mapping[str, Any]) -> str:
        return str(binding.get("commit", "unknown"))

    def _source_id(self, binding: Mapping[str, Any]) -> str:
        source = binding.get("source", {})
        return (
            f"ev2gym-residential://home_column:{source.get('home_column')}"
            f"/day:{source.get('day_index')}/row:{source.get('start_row')}"
        )


def process_to_record(process: PhysicalProcess) -> dict[str, Any]:
    """JSON-serializable view of a PhysicalProcess (for inventory exports)."""
    return {
        "process_id": process.process_id,
        "domain": process.domain,
        "backend": process.backend,
        "backend_version": process.backend_version,
        "source_id": process.source_id,
        "source_hash": process.source_hash,
        "horizon_steps": process.horizon_steps,
        "observation_interval_seconds": process.observation_interval_seconds,
        "provided_capabilities": sorted(process.provided_capabilities),
        "state_variables": list(process.state_variables),
        "action_types": list(process.action_types),
        "manifest": dict(process.manifest),
    }

