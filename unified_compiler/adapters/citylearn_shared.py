"""Data-probe layer: one shared CityLearn adapter over deduplicated source bundles.

Instead of building one simulator environment per responsibility, this module
derives one read-only :class:`SourceBundle` per real CityLearn
building+time-window from the frozen v10 backend bindings, then probes actual
component availability against the frozen v5 ``source_cache/schema.json`` plus
the referenced CSV headers/assets. Subsystem ``PhysicalProcess`` slices are
emitted only for probed subsystems and all slices of one window share the same
``source_bundle_id``.

Supported probe targets are ``hvac`` and ``battery_pv``. DHW storage and
thermal storage are out of probe scope: they emit zero capabilities and zero
processes (fail closed), even where the schema or a bare ``dhw_demand`` column
might suggest otherwise. A ``dhw_demand`` column alone never implies a
controllable DHW tank.

CityLearn is never imported or instantiated here; scanning reads schema JSON,
CSV headers, and file existence only. Emitted capability status is
``DATA_PROBED_PENDING_REPLAY``: data availability is confirmed, executability
is not claimed. Manifests carry project-relative references, stable digests,
and no hidden Contract clauses, gold actions, bulk traces, or absolute paths.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Mapping, Sequence

from ..types import (
    BackendCapability,
    CapabilityStatus,
    PhysicalProcess,
    ProcessRequirement,
)
from .legacy_v10 import LegacyArtifactError, V10ArtifactIndex, _canonical_json

V11_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_V5_SOURCE_CACHE = (V11_ROOT.parent / "v5_scenario_compiler" / "source_cache").resolve()
DEFAULT_CITYLEARN_CACHE_DIR = Path.home() / "Library" / "Caches" / "citylearn" / "v2.5.0"
DEFAULT_BACKEND_SITE_PACKAGES_DIR = (
    V11_ROOT.parent
    / "v10_diversity_aware_compiler"
    / ".runtime"
    / "venv"
    / "lib"
    / "python3.13"
    / "site-packages"
).resolve()
SCHEMA_FILENAME = "schema.json"

SUBSYSTEM_HVAC = "hvac"
SUBSYSTEM_BATTERY_PV = "battery_pv"
SUBSYSTEM_DHW_STORAGE = "dhw_storage"
SUBSYSTEM_THERMAL_STORAGE = "thermal_storage"
SUPPORTED_SUBSYSTEMS: tuple[str, ...] = (SUBSYSTEM_HVAC, SUBSYSTEM_BATTERY_PV)
OUT_OF_SCOPE_SUBSYSTEMS: tuple[str, ...] = (SUBSYSTEM_DHW_STORAGE, SUBSYSTEM_THERMAL_STORAGE)
ALL_SUBSYSTEMS: tuple[str, ...] = SUPPORTED_SUBSYSTEMS + OUT_OF_SCOPE_SUBSYSTEMS

SUBSYSTEM_DOMAINS: Mapping[str, str] = {
    SUBSYSTEM_HVAC: "hvac",
    SUBSYSTEM_BATTERY_PV: "electrical_storage",
}

SUBSYSTEM_PROVIDES: Mapping[str, tuple[str, ...]] = {
    SUBSYSTEM_HVAC: ("thermal.zone_temperature", "thermal.hvac_action"),
    SUBSYSTEM_BATTERY_PV: (
        "storage.soc",
        "storage.charge_discharge_action",
        "pv.generation_profile",
    ),
}

_HVAC_COLUMNS = ("cooling_demand", "heating_demand", "indoor_dry_bulb_temperature")
_HVAC_ACTION_KEYS = ("cooling_or_heating_device", "cooling_device", "heating_device")
_BATTERY_PV_COLUMNS = ("non_shiftable_load", "solar_generation")
_BATTERY_PV_ACTION_KEY = "electrical_storage"

_BINDING_DIGEST_FIELDS = ("source_trace_sha256", "thermal_model_sha256")
_FORBIDDEN_MANIFEST_TOKENS = ("password", "secret", "token", "credential", "gold")


def _rel(path: Path) -> str:
    return os.path.relpath(path, V11_ROOT)


@dataclass(frozen=True)
class SourceBundle:
    """One real CityLearn building+time-window, derived read-only from v10 bindings."""

    bundle_id: str
    building_id: str
    source_start_row: int
    source_end_row: int
    source_trace_sha256: str
    thermal_model_sha256: str
    backend_version: str
    legacy_process_ids: tuple[str, ...]
    legacy_episode_ids: tuple[str, ...]

    @property
    def horizon_steps(self) -> int:
        return self.source_end_row - self.source_start_row + 1


class CityLearnSourceIndex:
    """One-shot, read-only probe index over v10 bindings + v5 source cache.

    The v10 artifact index and the v5 ``schema.json`` are each loaded at most
    once; per-building CSV headers and row counts are probed at most once and
    cached. Missing or malformed inputs raise :class:`LegacyArtifactError`
    (fail closed), except probe-time absence of optional assets, which is
    recorded as an exclusion reason.
    """

    def __init__(
        self,
        v10_index: V10ArtifactIndex | None = None,
        artifact_dir: Path | str | None = None,
        source_cache_dir: Path | str | None = None,
        citylearn_cache_dir: Path | str | None = None,
        backend_site_packages_dir: Path | str | None = None,
    ) -> None:
        self._v10_index = v10_index if v10_index is not None else V10ArtifactIndex(artifact_dir)
        self._cache_dir = Path(source_cache_dir) if source_cache_dir else DEFAULT_V5_SOURCE_CACHE
        self._citylearn_cache_dir = (
            Path(citylearn_cache_dir) if citylearn_cache_dir else DEFAULT_CITYLEARN_CACHE_DIR
        )
        self._backend_site_packages_dir = (
            Path(backend_site_packages_dir)
            if backend_site_packages_dir
            else DEFAULT_BACKEND_SITE_PACKAGES_DIR
        )
        self.schema_load_count = 0
        self.header_loads = 0
        self.row_count_loads = 0
        self._bundles: tuple[SourceBundle, ...] | None = None
        self._schema: dict[str, Any] | None = None
        self._columns: dict[str, frozenset[str] | None] = {}
        self._row_counts: dict[str, int | None] = {}

    @property
    def v10_index(self) -> V10ArtifactIndex:
        return self._v10_index

    @property
    def source_cache_dir(self) -> Path:
        return self._cache_dir

    @property
    def citylearn_cache_dir(self) -> Path:
        return self._citylearn_cache_dir

    @property
    def backend_site_packages_dir(self) -> Path:
        return self._backend_site_packages_dir

    def bundles(self) -> tuple[SourceBundle, ...]:
        """Deduplicated CityLearn building+window bundles from frozen v10 bindings."""
        if self._bundles is not None:
            return self._bundles
        self._v10_index.ensure_loaded()
        by_key: dict[tuple[str, int, int], dict[str, Any]] = {}
        for process_id in self._v10_index.process_ids:
            episode_ids = self._v10_index.episode_ids(process_id)
            private = self._v10_index.private_record(episode_ids[0])
            binding = private["backend_binding"]
            if binding["backend"] != "CityLearn":
                continue
            building_id = binding.get("building_id")
            start = binding.get("source_start_row")
            end = binding.get("source_end_row")
            digests = {k: binding.get(k) for k in _BINDING_DIGEST_FIELDS}
            if (
                not isinstance(building_id, str)
                or not building_id
                or not isinstance(start, int)
                or not isinstance(end, int)
                or start < 0
                or end < start
                or any(not isinstance(v, str) or not v for v in digests.values())
            ):
                raise LegacyArtifactError(
                    f"{episode_ids[0] if episode_ids else '<unknown>'}: malformed CityLearn "
                    "backend_binding (building_id / source rows / sha256 digests)"
                )
            key = (building_id, start, end)
            entry = by_key.setdefault(
                key,
                {
                    "digests": digests,
                    "version": str(binding.get("version", "unknown")),
                    "processes": [],
                    "episodes": [],
                },
            )
            if entry["digests"] != digests:
                raise LegacyArtifactError(
                    f"inconsistent CityLearn binding digests for window {key!r}"
                )
            entry["processes"].append(process_id)
            entry["episodes"].extend(episode_ids)

        bundles = [
            SourceBundle(
                bundle_id=f"citylearn-resstock://{building_id}/rows:{start}-{end}",
                building_id=building_id,
                source_start_row=start,
                source_end_row=end,
                source_trace_sha256=entry["digests"]["source_trace_sha256"],
                thermal_model_sha256=entry["digests"]["thermal_model_sha256"],
                backend_version=entry["version"],
                legacy_process_ids=tuple(sorted(entry["processes"])),
                legacy_episode_ids=tuple(sorted(entry["episodes"])),
            )
            for (building_id, start, end), entry in by_key.items()
        ]
        bundles.sort(key=lambda b: (b.building_id, b.source_start_row, b.source_end_row))
        self._bundles = tuple(bundles)
        return self._bundles

    def schema(self) -> dict[str, Any]:
        if self._schema is None:
            schema_path = self._cache_dir / SCHEMA_FILENAME
            if not schema_path.is_file():
                raise LegacyArtifactError(f"missing v5 source schema: {schema_path}")
            try:
                loaded = json.loads(schema_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                raise LegacyArtifactError(f"invalid v5 source schema: {exc}") from exc
            if not isinstance(loaded, dict) or not isinstance(loaded.get("buildings"), dict):
                raise LegacyArtifactError("v5 source schema lacks a 'buildings' mapping")
            self.schema_load_count += 1
            self._schema = loaded
        return self._schema

    def building_entry(self, building_id: str) -> Mapping[str, Any] | None:
        buildings = self.schema()["buildings"]
        entry = buildings.get(building_id)
        return entry if isinstance(entry, Mapping) else None

    def seconds_per_time_step(self) -> float:
        value = self.schema().get("seconds_per_time_step", 3600)
        return float(value)

    def action_active(self, building_id: str, action_key: str) -> bool:
        actions = self.schema().get("actions")
        spec = actions.get(action_key) if isinstance(actions, Mapping) else None
        if not isinstance(spec, Mapping) or spec.get("active") is not True:
            return False
        entry = self.building_entry(building_id) or {}
        inactive = entry.get("inactive_actions") or ()
        return action_key not in set(inactive)

    def csv_columns(self, csv_name: str) -> frozenset[str] | None:
        """Header columns of a source CSV, probed at most once per file."""
        if csv_name in self._columns:
            return self._columns[csv_name]
        header: frozenset[str] | None = None
        path = self._cache_dir / csv_name
        if path.is_file():
            self.header_loads += 1
            try:
                with path.open("r", encoding="utf-8", newline="") as handle:
                    first = handle.readline().strip()
                if first:
                    header = frozenset(col.strip() for col in first.split(","))
            except OSError:
                header = None
        self._columns[csv_name] = header
        return header

    def csv_row_count(self, csv_name: str) -> int | None:
        if csv_name in self._row_counts:
            return self._row_counts[csv_name]
        count: int | None = None
        path = self._cache_dir / csv_name
        if path.is_file():
            self.row_count_loads += 1
            try:
                with path.open("rb") as handle:
                    count = max(sum(chunk.count(b"\n") for chunk in iter(lambda: handle.read(1 << 20), b"")) - 1, 0)
            except OSError:
                count = None
        self._row_counts[csv_name] = count
        return count

    def asset_exists(self, filename: str) -> bool:
        return (self._cache_dir / filename).is_file()

    # -- probing -----------------------------------------------------------

    def probe_bundle(self, bundle: SourceBundle) -> dict[str, str | None]:
        """Structural source candidacy: ``None`` = candidate, else exclusion reason."""
        verdicts: dict[str, str | None] = {}
        for subsystem in ALL_SUBSYSTEMS:
            if subsystem in OUT_OF_SCOPE_SUBSYSTEMS:
                verdicts[subsystem] = "out_of_probe_scope"
        verdicts[SUBSYSTEM_HVAC] = self._probe_hvac(bundle)
        verdicts[SUBSYSTEM_BATTERY_PV] = self._probe_battery_pv(bundle)
        return verdicts

    def probe_readiness(
        self, bundle: SourceBundle, candidates: dict[str, str | None]
    ) -> dict[str, str | None]:
        """Process readiness: candidates plus fail-closed static asset/backend gates."""
        readiness = dict(candidates)
        if candidates[SUBSYSTEM_BATTERY_PV] is None:
            readiness[SUBSYSTEM_BATTERY_PV] = self._probe_battery_pv_readiness(bundle)
        return readiness

    def _probe_battery_pv_readiness(self, bundle: SourceBundle) -> str | None:
        misc_dir = self._citylearn_cache_dir / "misc"
        if not (misc_dir / "battery_choices.yaml").is_file():
            return "citylearn cache misc/battery_choices.yaml missing"
        if not (misc_dir / "lbl-tracking_the_sun-res-pv.csv").is_file():
            return "citylearn cache misc/lbl-tracking_the_sun-res-pv.csv missing"
        entry = self.building_entry(bundle.building_id)
        assert entry is not None
        pv = entry.get("pv")
        autosize = pv.get("autosize_attributes") if isinstance(pv, Mapping) else None
        epw = autosize.get("epw_filepath") if isinstance(autosize, Mapping) else None
        if not isinstance(epw, str) or not epw:
            return "pv autosize_attributes.epw_filepath missing from schema"
        epw_path = Path(epw)
        if not epw_path.is_absolute():
            epw_path = self._cache_dir / epw_path
        if not epw_path.is_file():
            return "pv epw file missing under source_cache_dir"
        site = self._backend_site_packages_dir
        pysam_present = site.is_dir() and (
            (site / "PySAM").is_dir() or any(site.glob("PySAM*.dist-info"))
        )
        if not pysam_present:
            return "PySAM package missing from backend site-packages"
        return None

    def _probe_common(self, bundle: SourceBundle) -> tuple[Mapping[str, Any] | None, str | None]:
        entry = self.building_entry(bundle.building_id)
        if entry is None:
            return None, "building absent from v5 schema"
        if entry.get("include") is False:
            return None, "building excluded (include=false) in v5 schema"
        csv_name = entry.get("energy_simulation")
        if not isinstance(csv_name, str) or not csv_name:
            return None, "schema building lacks energy_simulation csv"
        columns = self.csv_columns(csv_name)
        if columns is None:
            return None, "energy_simulation csv missing from source cache"
        rows = self.csv_row_count(csv_name)
        if rows is None:
            return None, "energy_simulation csv unreadable"
        if bundle.source_end_row >= rows:
            return None, (
                f"window rows {bundle.source_start_row}-{bundle.source_end_row} "
                f"exceed csv rows {rows}"
            )
        return entry, None

    def _bundle_columns(self, entry: Mapping[str, Any]) -> frozenset[str]:
        columns = self.csv_columns(entry["energy_simulation"])
        assert columns is not None
        return columns

    def _probe_hvac(self, bundle: SourceBundle) -> str | None:
        entry, reason = self._probe_common(bundle)
        if reason is not None:
            return reason
        assert entry is not None
        dynamics = entry.get("dynamics")
        if not isinstance(dynamics, Mapping):
            return "schema building lacks a dynamics model"
        attributes = dynamics.get("attributes")
        filename = attributes.get("filename") if isinstance(attributes, Mapping) else None
        if not isinstance(filename, str) or not filename:
            return "dynamics model filename missing from schema"
        if not self.asset_exists(filename):
            return "dynamics model asset missing from source cache"
        columns = self._bundle_columns(entry)
        missing = [c for c in _HVAC_COLUMNS if c not in columns]
        if missing:
            return f"missing hvac source columns: {missing}"
        if not any(self.action_active(bundle.building_id, key) for key in _HVAC_ACTION_KEYS):
            return "no active cooling/heating device action in schema"
        return None

    def _probe_battery_pv(self, bundle: SourceBundle) -> str | None:
        entry, reason = self._probe_common(bundle)
        if reason is not None:
            return reason
        assert entry is not None
        if not isinstance(entry.get("electrical_storage"), Mapping):
            return "schema electrical_storage is not configured"
        if not isinstance(entry.get("pv"), Mapping):
            return "schema pv is not configured"
        if not self.action_active(bundle.building_id, _BATTERY_PV_ACTION_KEY):
            return "electrical_storage action not active in schema"
        columns = self._bundle_columns(entry)
        missing = [c for c in _BATTERY_PV_COLUMNS if c not in columns]
        if missing:
            return f"missing battery/pv source columns: {missing}"
        return None


class CityLearnSharedAdapter:
    """Shared-probe CityLearn adapter: one bundle, many subsystem slices, no env."""

    backend: ClassVar[str] = "citylearn"

    def __init__(
        self,
        source_index: CityLearnSourceIndex | None = None,
        v10_index: V10ArtifactIndex | None = None,
        artifact_dir: Path | str | None = None,
        source_cache_dir: Path | str | None = None,
        citylearn_cache_dir: Path | str | None = None,
        backend_site_packages_dir: Path | str | None = None,
    ) -> None:
        self._source_index = (
            source_index
            if source_index is not None
            else CityLearnSourceIndex(
                v10_index=v10_index,
                artifact_dir=artifact_dir,
                source_cache_dir=source_cache_dir,
                citylearn_cache_dir=citylearn_cache_dir,
                backend_site_packages_dir=backend_site_packages_dir,
            )
        )
        self.scan_calls = 0
        self._probes: dict[str, dict[str, str | None]] | None = None
        self._readiness: dict[str, dict[str, str | None]] | None = None
        self._processes: tuple[PhysicalProcess, ...] | None = None

    @property
    def source_index(self) -> CityLearnSourceIndex:
        return self._source_index

    def bundles(self) -> tuple[SourceBundle, ...]:
        return self._source_index.bundles()

    def _ensure_probed(self) -> None:
        if self._probes is not None:
            return
        probes: dict[str, dict[str, str | None]] = {}
        readiness: dict[str, dict[str, str | None]] = {}
        processes: list[PhysicalProcess] = []
        for bundle in self._source_index.bundles():
            verdicts = self._source_index.probe_bundle(bundle)
            ready = self._source_index.probe_readiness(bundle, verdicts)
            probes[bundle.bundle_id] = verdicts
            readiness[bundle.bundle_id] = ready
            for subsystem in SUPPORTED_SUBSYSTEMS:
                if ready[subsystem] is None:
                    processes.append(self._make_process(bundle, subsystem))
        self._probes = probes
        self._readiness = readiness
        self._processes = tuple(processes)

    def probe_report(self) -> dict[str, Any]:
        """JSON-serializable probe outcome per bundle and subsystem."""
        self._ensure_probed()
        assert self._readiness is not None
        source_counts = {subsystem: 0 for subsystem in ALL_SUBSYSTEMS}
        ready_counts = {subsystem: 0 for subsystem in ALL_SUBSYSTEMS}
        per_bundle: dict[str, Any] = {}
        for bundle in self.bundles():
            verdicts = self._probes[bundle.bundle_id]
            ready = self._readiness[bundle.bundle_id]
            for subsystem, reason in verdicts.items():
                if reason is None:
                    source_counts[subsystem] += 1
            for subsystem, reason in ready.items():
                if reason is None:
                    ready_counts[subsystem] += 1
            per_bundle[bundle.bundle_id] = {
                "building_id": bundle.building_id,
                "window": {
                    "source_start_row": bundle.source_start_row,
                    "source_end_row": bundle.source_end_row,
                },
                "source_candidates": [s for s in ALL_SUBSYSTEMS if verdicts[s] is None],
                "ready": [s for s in ALL_SUBSYSTEMS if ready[s] is None],
                "supported": [s for s in ALL_SUBSYSTEMS if ready[s] is None],
                "excluded": {
                    s: reason for s, reason in ready.items() if reason is not None
                },
            }
        return {
            "backend": self.backend,
            "status": CapabilityStatus.DATA_PROBED_PENDING_REPLAY.value,
            "source_cache": _rel(self._source_index.source_cache_dir),
            "bundle_count": len(self.bundles()),
            "subsystem_source_candidate_counts": source_counts,
            "subsystem_process_ready_counts": ready_counts,
            "subsystem_candidate_counts": dict(ready_counts),
            "per_bundle": per_bundle,
        }

    def capabilities(self) -> Sequence[BackendCapability]:
        self._ensure_probed()
        assert self._readiness is not None
        caps: list[BackendCapability] = []
        supported = {
            subsystem
            for verdicts in self._readiness.values()
            for subsystem, reason in verdicts.items()
            if reason is None
        }
        schema_ref = _rel(self._source_index.source_cache_dir / SCHEMA_FILENAME)
        if SUBSYSTEM_HVAC in supported:
            caps.append(
                BackendCapability(
                    capability_id="citylearn_shared.hvac",
                    backend=self.backend,
                    provides=SUBSYSTEM_PROVIDES[SUBSYSTEM_HVAC],
                    status=CapabilityStatus.DATA_PROBED_PENDING_REPLAY,
                    evidence=(
                        f"{schema_ref}#buildings.*.dynamics",
                        f"{schema_ref}#buildings.*.energy_simulation (columns probed)",
                    ),
                )
            )
        if SUBSYSTEM_BATTERY_PV in supported:
            caps.append(
                BackendCapability(
                    capability_id="citylearn_shared.electrical_storage",
                    backend=self.backend,
                    provides=("storage.soc", "storage.charge_discharge_action"),
                    status=CapabilityStatus.DATA_PROBED_PENDING_REPLAY,
                    evidence=(
                        f"{schema_ref}#buildings.*.electrical_storage (non-null, probed)",
                    ),
                )
            )
            caps.append(
                BackendCapability(
                    capability_id="citylearn_shared.solar_generation",
                    backend=self.backend,
                    provides=("pv.generation_profile",),
                    status=CapabilityStatus.DATA_PROBED_PENDING_REPLAY,
                    evidence=(
                        f"{schema_ref}#buildings.*.pv (non-null, probed)",
                        f"{schema_ref}#buildings.*.energy_simulation solar_generation column",
                    ),
                )
            )
        return caps

    def processes(self) -> tuple[PhysicalProcess, ...]:
        self._ensure_probed()
        return self._processes

    def scan(self, requirements: Sequence[ProcessRequirement]) -> Sequence[PhysicalProcess]:
        """Return probed subsystem slices matching any requirement; probes once."""
        self.scan_calls += 1
        processes = self.processes()
        if not requirements:
            return ()
        return tuple(
            process
            for process in processes
            if any(req.required_capabilities <= process.provided_capabilities for req in requirements)
        )

    def _make_process(self, bundle: SourceBundle, subsystem: str) -> PhysicalProcess:
        index = self._source_index
        entry = index.building_entry(bundle.building_id)
        assert entry is not None
        csv_name = entry["energy_simulation"]
        columns = index._bundle_columns(entry)
        dynamics = entry.get("dynamics")
        dynamics_file = None
        if isinstance(dynamics, Mapping):
            attributes = dynamics.get("attributes")
            if isinstance(attributes, Mapping) and isinstance(attributes.get("filename"), str):
                dynamics_file = attributes["filename"]
        assets = {
            "schema": _rel(index.source_cache_dir / SCHEMA_FILENAME),
            "building_csv": _rel(index.source_cache_dir / csv_name),
        }
        if subsystem == SUBSYSTEM_HVAC and dynamics_file:
            assets["dynamics_model"] = _rel(index.source_cache_dir / dynamics_file)

        digest = hashlib.sha256(
            _canonical_json({"bundle": bundle.bundle_id, "subsystem": subsystem}).encode("utf-8")
        ).hexdigest()
        process_id = f"{subsystem}_clshare_{digest[:16]}"
        source_hash = "sha256:" + hashlib.sha256(
            _canonical_json(
                {
                    "v10_source_digests": {
                        "source_trace_sha256": bundle.source_trace_sha256,
                        "thermal_model_sha256": bundle.thermal_model_sha256,
                    },
                    "bundle": {
                        "building_id": bundle.building_id,
                        "source_start_row": bundle.source_start_row,
                        "source_end_row": bundle.source_end_row,
                    },
                    "subsystem": subsystem,
                    "probe": {
                        "schema_building_present": True,
                        "probed_columns": sorted(
                            c
                            for c in columns
                            if c in set(_HVAC_COLUMNS) | set(_BATTERY_PV_COLUMNS) | {"hvac_mode"}
                        ),
                        "assets": assets,
                    },
                }
            ).encode("utf-8")
        ).hexdigest()

        if subsystem == SUBSYSTEM_HVAC:
            state_variables = tuple(sorted(c for c in columns if c in _HVAC_COLUMNS))
            action_types = tuple(
                key
                for key in _HVAC_ACTION_KEYS
                if index.action_active(bundle.building_id, key)
            )
        else:
            state_variables = tuple(sorted(c for c in columns if c in _BATTERY_PV_COLUMNS))
            action_types = (
                (_BATTERY_PV_ACTION_KEY,)
                if index.action_active(bundle.building_id, _BATTERY_PV_ACTION_KEY)
                else ()
            )

        manifest = {
            "probe": "citylearn_shared_v1",
            "source_bundle_id": bundle.bundle_id,
            "subsystem": subsystem,
            "building_id": bundle.building_id,
            "window": {
                "source_start_row": bundle.source_start_row,
                "source_end_row": bundle.source_end_row,
            },
            "assets": assets,
            "legacy_process_ids": list(bundle.legacy_process_ids),
            "legacy_episode_ids": list(bundle.legacy_episode_ids),
            "provenance": {
                "citylearn_version": bundle.backend_version,
                "source_trace_sha256": bundle.source_trace_sha256,
                "thermal_model_sha256": bundle.thermal_model_sha256,
            },
        }
        blob = json.dumps(manifest).lower()
        for token in _FORBIDDEN_MANIFEST_TOKENS:
            assert token not in blob, f"manifest token {token!r} leaked"

        return PhysicalProcess(
            process_id=process_id,
            domain=SUBSYSTEM_DOMAINS[subsystem],
            backend=self.backend,
            backend_version=bundle.backend_version,
            source_id=bundle.bundle_id,
            source_hash=source_hash,
            horizon_steps=bundle.horizon_steps,
            observation_interval_seconds=index.seconds_per_time_step(),
            provided_capabilities=frozenset(SUBSYSTEM_PROVIDES[subsystem]),
            state_variables=state_variables,
            action_types=action_types,
            manifest=manifest,
        )
