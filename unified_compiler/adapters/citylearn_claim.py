"""Claim-facing CityLearn adapter: verified claims only, composed runtimes.

This adapter composes existing, independently verified pieces instead of
re-implementing CityLearn physics:

- :class:`CityLearnBatteryPVAdapter` supplies the replay-gate-verified
  battery/PV process pool (``EXECUTABLE_REPLAY_VERIFIED``).
- :class:`CityLearnSharedAdapter` supplies source-probed HVAC slices, which
  are executed through the native verified CityLearn episode runtime.
- The battery/PV episode route lazily reuses the exact probe/replay helpers the
  verified replay gate ran (``probe_citylearn_battery_replay``), so the physics
  is CityLearn's own device code, never duplicated here.
- The HVAC episode route is native v11: it resolves the frozen v10
  public/private episode records as data (never the v10 runtime chain) and
  drives the verified CityLearn episode runtime (the pinned CityLearn 2.5.0
  environment and its LSTM thermal dynamics via the frozen bindings), so HVAC
  physics is likewise CityLearn's own device code, never duplicated here.

Capability status is reported separately per subsystem route. DHW storage and
thermal storage stay fail closed: no capabilities, no processes, and
:meth:`CityLearnClaimAdapter.open_episode` raises for them; a ``dhw_demand``
column never implies a controllable DHW tank.

Reset electricity is never double-counted: ``reset`` emits a pre-action
observation with no electricity-consumption record; consumption accounting
begins with the first ``step`` record, exactly as in the verified replay gate.

Nothing on this surface exposes hidden contracts, gold actions, or evaluator
conclusions (gate scores and feasibility-witness traces stay inside the gate
artifact). Observations carry an explicit ``source_step`` so public
observations stay aligned with the source CSV rows.
"""

from __future__ import annotations

import importlib.util
import hashlib
import hmac
import json
import re
import sys
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any, ClassVar, Sequence

from ..types import (
    BackendCapability,
    CapabilityStatus,
    PhysicalProcess,
    PhysicalTopology,
    ProcessRequirement,
    ResponsibilityLifecycle,
)
from .citylearn_battery import CityLearnBatteryPVAdapter
from .citylearn_shared import (
    OUT_OF_SCOPE_SUBSYSTEMS,
    SUBSYSTEM_BATTERY_PV,
    SUBSYSTEM_HVAC,
    SUBSYSTEM_PROVIDES,
    SCHEMA_FILENAME,
    CityLearnSharedAdapter,
    CityLearnSourceIndex,
    _rel,
)
from .legacy_v10 import LegacyArtifactError, V10ArtifactIndex
from .legacy_v10_runtime import (
    ReplayActionError,
    _jsonable,
    _schema_declares_parameters,
    _validate_action,
)

V11_ROOT = Path(__file__).resolve().parents[2]
V5_ROOT = V11_ROOT.parent / "v5_scenario_compiler"
HVAC_RUNTIME_SOURCE = V5_ROOT / "episode_runtime.py"
HVAC_RUNTIME_MODULE_NAME = "citylearn_episode_runtime_for_v11"
HVAC_RUNTIME_BOOTSTRAP_SOURCE = V5_ROOT / "runtime_bootstrap.py"
HVAC_RUNTIME_PROBE_SOURCE = V5_ROOT / "citylearn_hvac_probe.py"

# These are the frozen v5 provenance roots used to build the v10 CityLearn
# episodes.  The manifests contain the per-file digests; pinning the manifest
# digests here prevents a modified manifest from silently blessing modified
# source data or runtime code.
SOURCE_MANIFEST_PATH = V5_ROOT / "generated" / "source_manifest.json"
RUNTIME_MANIFEST_PATH = V5_ROOT / "generated" / "production_v1" / "run_manifest.json"
PINNED_SOURCE_MANIFEST_SHA256 = (
    "29218dd6eef19b9ff8cda959204a14a4cb03aeceec48df65b8c826302835bdfe"
)
PINNED_RUNTIME_MANIFEST_SHA256 = (
    "b567c21306f11c67ef18c2f732a0738c16b6ef95bfdc6a5fefd123a784e40e7b"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

BATTERY_PROVIDES = SUBSYSTEM_PROVIDES[SUBSYSTEM_BATTERY_PV]
HVAC_PROVIDES = SUBSYSTEM_PROVIDES[SUBSYSTEM_HVAC]

_MATCH_ALL_REQUIREMENT = ProcessRequirement(
    requirement_id="citylearn_claim_match_all",
    responsibility_lifecycle=ResponsibilityLifecycle.MAINTAIN,
    physical_topology=PhysicalTopology.STORAGE_DYNAMICS,
    required_capabilities=frozenset(),
    state_variables=(),
    action_types=(),
)

ROUTE_BATTERY_PV = "battery_pv_replay_verified"
ROUTE_HVAC_NATIVE = "citylearn_hvac_native_replay_verified"

PROBE_MODULE_NAME = "citylearn_battery_replay_probe_for_v11"
_probe_module: Any | None = None
_hvac_runtime_module: Any | None = None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_digest(value: Any, label: str, episode_id: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value.lower()) is None:
        raise ClaimEpisodeError(
            f"{episode_id}: {label} must be a 64-hex SHA-256 digest"
        )
    return value.lower()


def _safe_source_path(cache_dir: Path, filename: str, label: str) -> Path:
    candidate = Path(filename)
    if candidate.is_absolute():
        raise ClaimEpisodeError(f"pinned {label} must be relative to source cache")
    root = cache_dir.resolve()
    resolved = (root / candidate).resolve()
    if resolved != root and root not in resolved.parents:
        raise ClaimEpisodeError(f"pinned {label} escapes source cache")
    return resolved


def _load_manifest(path: Path, expected_sha256: str, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise ClaimEpisodeError(f"pinned {label} missing: {path}")
    actual = _sha256_file(path)
    if not hmac.compare_digest(actual, expected_sha256):
        raise ClaimEpisodeError(
            f"pinned {label} digest mismatch ({actual} != {expected_sha256})"
        )
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClaimEpisodeError(f"pinned {label} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ClaimEpisodeError(f"pinned {label} must contain a JSON object")
    return value


def _load_pinned_provenance_manifests() -> tuple[dict[str, Any], dict[str, Any]]:
    source_manifest = _load_manifest(
        SOURCE_MANIFEST_PATH, PINNED_SOURCE_MANIFEST_SHA256, "CityLearn source manifest"
    )
    runtime_manifest = _load_manifest(
        RUNTIME_MANIFEST_PATH, PINNED_RUNTIME_MANIFEST_SHA256, "CityLearn runtime manifest"
    )
    declared_source_digest = runtime_manifest.get("source_manifest_sha256")
    if not isinstance(declared_source_digest, str) or not hmac.compare_digest(
        declared_source_digest.lower(), PINNED_SOURCE_MANIFEST_SHA256
    ):
        raise ClaimEpisodeError(
            "pinned CityLearn runtime manifest is not bound to the pinned source manifest"
        )
    return source_manifest, runtime_manifest


def _source_manifest_entries(manifest: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    entries: dict[str, Mapping[str, Any]] = {}
    for key in ("files", "support_files"):
        values = manifest.get(key)
        if not isinstance(values, list):
            raise ClaimEpisodeError(f"pinned CityLearn source manifest lacks {key}")
        for value in values:
            if isinstance(value, Mapping) and isinstance(value.get("name"), str):
                entries[value["name"]] = value
    return entries


def _require_manifest_digest(
    entry: Mapping[str, Any], name: str, episode_id: str
) -> str:
    return _require_digest(entry.get("sha256"), f"manifest digest for {name}", episode_id)


class ClaimEpisodeError(RuntimeError):
    """The claim episode facade was misused or its assets are unavailable."""


class UnsupportedSubsystemError(ClaimEpisodeError):
    """Fail-closed refusal to open an out-of-scope subsystem episode."""


class ClaimActionError(ValueError):
    """Caller-supplied action is missing, malformed, or not publicly legal."""


class UnknownClaimProcessError(LookupError):
    """No CityLearn claim process is registered under the given id."""


def _load_battery_probe() -> Any:
    """Lazily load the verified battery/PV probe module (imports CityLearn)."""
    global _probe_module
    if _probe_module is not None:
        return _probe_module
    probe_path = V11_ROOT / "probe_citylearn_battery_replay.py"
    if not probe_path.is_file():
        raise ClaimEpisodeError(f"missing verified battery/PV probe module: {probe_path}")
    spec = importlib.util.spec_from_file_location(PROBE_MODULE_NAME, probe_path)
    if spec is None or spec.loader is None:
        raise ClaimEpisodeError(f"unable to load battery/PV probe module: {probe_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[PROBE_MODULE_NAME] = module
    spec.loader.exec_module(module)
    _probe_module = module
    return module


def _load_hvac_runtime() -> Any:
    """Lazily load the verified CityLearn episode runtime (imports CityLearn).

    The verified runtime module pins CityLearn 2.5.0 at import time via
    ``runtime_bootstrap.ensure_citylearn_runtime`` and fails loudly if the
    pinned environment cannot be resolved. HVAC physics is always CityLearn's
    own device code plus its official LSTM thermal dynamics; nothing here
    re-implements or fakes it.
    """
    global _hvac_runtime_module
    if _hvac_runtime_module is not None:
        return _hvac_runtime_module
    if not HVAC_RUNTIME_SOURCE.is_file():
        raise ClaimEpisodeError(
            f"missing verified CityLearn episode runtime: {HVAC_RUNTIME_SOURCE}"
        )
    if str(V5_ROOT) not in sys.path:
        sys.path.insert(0, str(V5_ROOT))
    spec = importlib.util.spec_from_file_location(HVAC_RUNTIME_MODULE_NAME, HVAC_RUNTIME_SOURCE)
    if spec is None or spec.loader is None:
        raise ClaimEpisodeError(
            f"unable to load CityLearn episode runtime: {HVAC_RUNTIME_SOURCE}"
        )
    try:
        module = importlib.util.module_from_spec(spec)
        sys.modules[HVAC_RUNTIME_MODULE_NAME] = module
        spec.loader.exec_module(module)
    except Exception as exc:
        sys.modules.pop(HVAC_RUNTIME_MODULE_NAME, None)
        raise ClaimEpisodeError(
            f"pinned CityLearn runtime unavailable for the HVAC route: {exc}"
        ) from exc
    runtime_cls = getattr(module, "CityLearnEpisodeRuntime", None)
    if runtime_cls is None:
        raise ClaimEpisodeError(
            f"CityLearn episode runtime lacks CityLearnEpisodeRuntime: {HVAC_RUNTIME_SOURCE}"
        )
    _hvac_runtime_module = module
    return module


class CityLearnBatteryPVEpisode:
    """Stateful episode facade over one replay-verified battery/PV process.

    Drives the CityLearn battery device directly (the exact pattern of the
    verified replay gate): one ``charge`` call per step, one consumption
    record per step, ``next_time_step`` between steps. ``reset`` never
    appends an electricity record, so reset electricity is not double-counted.
    """

    _ACTION_TYPE = "charge_discharge_rate"
    _MIN_RATE = -1.0
    _MAX_RATE = 1.0

    def __init__(self, process: PhysicalProcess) -> None:
        manifest = process.manifest
        window = manifest["window"]
        self._process_id = process.process_id
        self._building_id = str(manifest["building_id"])
        self._start = int(window["source_start_row"])
        self._end = int(window["source_end_row"])
        self._horizon = self._end - self._start + 1
        self._initial_soc = float(manifest["battery"]["initial_soc"])
        self._device: Any | None = None
        self._load: Sequence[float] | None = None
        self._solar: Sequence[float] | None = None
        self._steps_completed = 0
        self._done = False
        self._last_observation: dict[str, Any] | None = None

    # -- facade API --------------------------------------------------------

    def replay_id(self) -> str:
        return self._process_id

    def legal_actions(self) -> dict[str, Any]:
        return {
            self._ACTION_TYPE: {
                "minimum": self._MIN_RATE,
                "maximum": self._MAX_RATE,
                "unit": "fraction_of_nominal_power",
            }
        }

    def reset(self) -> dict[str, Any]:
        probe = _load_battery_probe()
        probe.require_assets()
        with probe.shared_sizing_catalogs():
            runtime = probe.building_battery_runtime(self._building_id)
        device = deepcopy(runtime["battery_template"])
        device.reset()
        device.force_set_soc(self._initial_soc)
        self._device = device
        self._load = runtime["load"]
        self._solar = runtime["solar"]
        self._steps_completed = 0
        self._done = False
        self._last_observation = {
            "source_step": self._start,
            "non_shiftable_load": float(self._load[self._start]),
            "solar_generation": float(self._solar[self._start]),
            "electrical_storage_soc": float(device.soc[device.time_step]),
        }
        return dict(self._last_observation)

    def observe(self) -> dict[str, Any]:
        if self._last_observation is None:
            raise ClaimEpisodeError("reset() must be called before observe()")
        return dict(self._last_observation)

    def step(self, action: Any) -> dict[str, Any]:
        if self._device is None:
            raise ClaimEpisodeError("reset() must be called before step()")
        if self._done:
            raise ClaimEpisodeError("episode is already done")
        rate = self._action_rate(action, self._steps_completed)
        local_step = self._steps_completed
        device = self._device
        device.charge(rate * device.nominal_power)
        soc = float(device.soc[local_step])
        storage = float(device.electricity_consumption[local_step])
        source_step = self._start + local_step
        load = float(self._load[source_step])
        solar = float(self._solar[source_step])
        observation = {
            "source_step": source_step,
            "non_shiftable_load": load,
            "solar_generation": solar,
            "electrical_storage_soc": soc,
            "storage_electricity": storage,
            "net_electricity": load - solar + storage,
        }
        self._last_observation = dict(observation)
        if local_step + 1 < self._horizon:
            device.next_time_step()
        self._steps_completed = local_step + 1
        self._done = self._steps_completed >= self._horizon
        return {
            "step_index": local_step,
            "source_step": source_step,
            "observation": observation,
            "done": self._done,
        }

    def private_state(self) -> dict[str, Any]:
        return {
            "route": ROUTE_BATTERY_PV,
            "process_id": self._process_id,
            "building_id": self._building_id,
            "window": {"source_start_row": self._start, "source_end_row": self._end},
            "horizon_steps": self._horizon,
            "steps_completed": self._steps_completed,
            "done": self._done,
            "electricity_accounting": (
                "reset emits no consumption record; one record per step"
            ),
        }

    # -- internals ---------------------------------------------------------

    def _action_rate(self, action: Any, index: int) -> float:
        if isinstance(action, Mapping):
            if action.get("type") != self._ACTION_TYPE:
                raise ClaimActionError(
                    f"step {index}: action type must be {self._ACTION_TYPE!r}"
                )
            raw = action.get("value")
        else:
            raw = action
        try:
            rate = float(raw)
        except (TypeError, ValueError):
            raise ClaimActionError(
                f"step {index}: action requires a numeric 'value' in "
                f"[{self._MIN_RATE}, {self._MAX_RATE}]"
            ) from None
        if not self._MIN_RATE <= rate <= self._MAX_RATE:
            raise ClaimActionError(
                f"step {index}: rate {rate} outside [{self._MIN_RATE}, {self._MAX_RATE}]"
            )
        return rate


class CityLearnNativeHvacEpisode:
    """Stateful native v11 episode facade over one frozen CityLearn HVAC window.

    The frozen v10 public/private records are read as data (episode identity,
    public ``allowed_actions`` schema, horizon, backend binding with the hidden
    LSTM initialization row). Execution goes through the verified CityLearn
    episode runtime, which drives the pinned CityLearn 2.5.0 environment and
    its LSTM thermal dynamics directly — the frozen v10 runtime chain
    (:class:`V10RuntimeBridge`) is never imported or executed on this path.

    Public actions are validated against the public ``allowed_actions`` schema
    before any runtime call; hidden Contracts and gold actions are never read.
    Observations carry an explicit ``source_step`` aligned with the source CSV
    rows (hidden initialization rows are never exposed as decisions).
    """

    def __init__(
        self,
        source_index: CityLearnSourceIndex,
        episode_id: str,
        process_id: str,
        expected_building_id: str,
        expected_window: Mapping[str, Any],
    ) -> None:
        self._source_index_ref = source_index
        self._episode_id = episode_id
        self._process_id = process_id
        self._expected_building_id = expected_building_id
        self._expected_window = dict(expected_window)
        self._runtime: Any | None = None
        self._allowed: Mapping[str, Any] | None = None
        self._require_mapping = False
        self._horizon = 0
        self._steps_taken = 0
        self._done = False

    # -- facade API --------------------------------------------------------

    def replay_id(self) -> str:
        return self._episode_id

    def legal_actions(self) -> dict[str, Any]:
        if self._allowed is None:
            raise ClaimEpisodeError("reset() must be called before legal_actions()")
        return deepcopy(dict(self._allowed))

    def reset(self) -> dict[str, Any]:
        public, private = self._resolve_pair()
        binding = private.get("backend_binding")
        if not isinstance(binding, Mapping) or binding.get("backend") != "CityLearn":
            raise ClaimEpisodeError(
                f"{self._episode_id}: frozen binding is not a CityLearn episode"
            )
        building_id = binding.get("building_id")
        start = binding.get("source_start_row")
        end = binding.get("source_end_row")
        hidden_start = binding.get("hidden_initialization_start_row")
        if (
            building_id != self._expected_building_id
            or start != self._expected_window.get("source_start_row")
            or end != self._expected_window.get("source_end_row")
        ):
            raise ClaimEpisodeError(
                f"{self._episode_id}: frozen binding window {building_id!r}"
                f"[{start}, {end}] does not match the scanned process window "
                f"{self._expected_building_id!r}"
                f"[{self._expected_window.get('source_start_row')}, "
                f"{self._expected_window.get('source_end_row')}]"
            )
        if not isinstance(hidden_start, int) or not 0 <= hidden_start <= start:
            raise ClaimEpisodeError(
                f"{self._episode_id}: malformed hidden_initialization_start_row"
            )
        allowed = public.get("allowed_actions")
        if not isinstance(allowed, Mapping):
            raise ClaimEpisodeError(f"{self._episode_id}: malformed public allowed_actions")
        self._allowed = allowed
        self._require_mapping = _schema_declares_parameters(allowed)
        self._horizon = int(public["horizon_steps"])
        self._ensure_source_assets(str(building_id), binding=binding)
        try:
            runtime_module = _load_hvac_runtime()
        except ClaimEpisodeError:
            raise
        except Exception as exc:  # pragma: no cover - loader already wraps
            raise ClaimEpisodeError(f"pinned CityLearn runtime unavailable: {exc}") from exc
        try:
            self._runtime = runtime_module.CityLearnEpisodeRuntime(public, private)
        except Exception as exc:
            raise ClaimEpisodeError(
                f"{self._episode_id}: failed to start the native CityLearn "
                f"HVAC runtime: {exc}"
            ) from exc
        self._steps_taken = 0
        self._done = bool(self._runtime.done)
        return self.observe()

    def observe(self) -> dict[str, Any]:
        if self._runtime is None:
            raise ClaimEpisodeError("reset() must be called before observe()")
        try:
            observation = _jsonable(self._runtime.observe())
        except ClaimEpisodeError:
            raise
        except Exception as exc:
            raise ClaimEpisodeError(f"CityLearn runtime observe failed: {exc}") from exc
        if isinstance(observation, dict):
            return {"source_step": int(self._runtime.source_row), **observation}
        return observation

    def step(self, action: Any) -> dict[str, Any]:
        if self._runtime is None:
            raise ClaimEpisodeError("reset() must be called before step()")
        if self._done:
            raise ClaimEpisodeError("episode is already done")
        try:
            _validate_action(action, self._allowed, self._steps_taken, self._require_mapping)
        except ReplayActionError as exc:
            raise ClaimActionError(str(exc)) from None
        try:
            record = _jsonable(self._runtime.step(action))
        except ClaimEpisodeError:
            raise
        except Exception as exc:
            raise ClaimEpisodeError(f"CityLearn runtime step failed: {exc}") from exc
        step_index = self._steps_taken
        self._steps_taken += 1
        self._done = bool(self._runtime.done)
        return {
            "step_index": step_index,
            "source_step": int(record["source_row"]),
            "observation": record,
            "done": self._done,
        }

    def private_state(self) -> dict[str, Any]:
        return {
            "route": ROUTE_HVAC_NATIVE,
            "execution": "native_verified_citylearn_runtime",
            "process_id": self._process_id,
            "episode_id": self._episode_id,
            "horizon_steps": self._horizon,
            "steps_taken": self._steps_taken,
            "done": self._done,
        }

    # -- internals ---------------------------------------------------------

    def _resolve_pair(self) -> tuple[dict[str, Any], dict[str, Any]]:
        try:
            return self._v10_index.pair(self._episode_id)
        except LegacyArtifactError:
            raise
        except KeyError:
            raise ClaimEpisodeError(
                f"{self._episode_id}: no frozen v10 episode record"
            ) from None

    @property
    def _v10_index(self) -> V10ArtifactIndex:
        return self._source_index_ref.v10_index

    def _ensure_source_assets(
        self, building_id: str, *, binding: Mapping[str, Any] | None = None
    ) -> None:
        """Verify the frozen source/runtime closure before constructing CityLearn.

        Existence and CSV headers are only structural probes.  The native HVAC
        route is claim-facing only when every byte consumed by the runtime is
        still the byte set covered by the frozen v5 manifests *and* the v10
        binding.  In particular, a replacement CSV/model with the same name
        must not pass this gate.  Verification is deliberately done before
        importing or instantiating CityLearn and never implements any physics.
        """
        index = self._source_index_ref
        entry = index.building_entry(building_id)
        if entry is None:
            raise ClaimEpisodeError(
                f"{building_id}: absent from the pinned v5 source schema"
            )
        csv_name = entry.get("energy_simulation")
        if not isinstance(csv_name, str) or index.csv_columns(csv_name) is None:
            raise ClaimEpisodeError(
                f"{building_id}: energy_simulation csv missing from the source cache"
            )
        csv_path = _safe_source_path(index.source_cache_dir, csv_name, "energy_simulation csv")
        dynamics = entry.get("dynamics")
        filename = None
        if isinstance(dynamics, Mapping):
            attributes = dynamics.get("attributes")
            if isinstance(attributes, Mapping):
                filename = attributes.get("filename")
        if not isinstance(filename, str):
            raise ClaimEpisodeError(
                f"{building_id}: thermal dynamics model missing from the source cache"
            )
        model_path = _safe_source_path(index.source_cache_dir, filename, "thermal dynamics model")
        if not index.asset_exists(filename):
            raise ClaimEpisodeError(
                f"{building_id}: thermal dynamics model missing from the source cache"
            )

        if binding is None:
            try:
                _, private = self._resolve_pair()
                binding = private.get("backend_binding")
            except (LegacyArtifactError, KeyError) as exc:
                raise ClaimEpisodeError(
                    f"{self._episode_id}: unable to resolve pinned backend provenance"
                ) from exc
        if not isinstance(binding, Mapping):
            raise ClaimEpisodeError(
                f"{self._episode_id}: malformed CityLearn backend provenance"
            )

        source_trace_digest = _require_digest(
            binding.get("source_trace_sha256"), "source_trace_sha256", self._episode_id
        )
        model_digest = _require_digest(
            binding.get("thermal_model_sha256"), "thermal_model_sha256", self._episode_id
        )
        source_manifest, runtime_manifest = _load_pinned_provenance_manifests()
        source_entries = _source_manifest_entries(source_manifest)
        csv_entry = source_entries.get(csv_name)
        model_entry = source_entries.get(filename)
        schema_entry = source_manifest.get("schema")
        if not isinstance(csv_entry, Mapping) or not isinstance(model_entry, Mapping):
            raise ClaimEpisodeError(
                f"{self._episode_id}: pinned source manifest lacks CSV/model provenance"
            )
        if not isinstance(schema_entry, Mapping):
            raise ClaimEpisodeError(
                f"{self._episode_id}: pinned source manifest lacks schema provenance"
            )
        expected_csv = _require_manifest_digest(csv_entry, csv_name, self._episode_id)
        expected_model = _require_manifest_digest(model_entry, filename, self._episode_id)
        expected_schema = _require_manifest_digest(
            schema_entry, SCHEMA_FILENAME, self._episode_id
        )
        if source_trace_digest != expected_csv:
            raise ClaimEpisodeError(
                f"{self._episode_id}: source CSV provenance digest mismatch "
                f"({source_trace_digest} != {expected_csv})"
            )
        if model_digest != expected_model:
            raise ClaimEpisodeError(
                f"{self._episode_id}: thermal model provenance digest mismatch "
                f"({model_digest} != {expected_model})"
            )

        schema_path = _safe_source_path(
            index.source_cache_dir, SCHEMA_FILENAME, "source schema"
        )
        self._verify_file_digest(schema_path, expected_schema, "source schema")
        self._verify_file_digest(csv_path, expected_csv, "source CSV")
        self._verify_file_digest(model_path, expected_model, "thermal dynamics model")

        compiler_sources = runtime_manifest.get("compiler_sources")
        if not isinstance(compiler_sources, Mapping):
            raise ClaimEpisodeError(
                f"{self._episode_id}: pinned runtime manifest lacks compiler sources"
            )
        for name, path in (
            ("runtime_bootstrap.py", HVAC_RUNTIME_BOOTSTRAP_SOURCE),
            ("citylearn_hvac_probe.py", HVAC_RUNTIME_PROBE_SOURCE),
            ("episode_runtime.py", HVAC_RUNTIME_SOURCE),
        ):
            expected_runtime = _require_digest(
                compiler_sources.get(name), f"runtime {name}", self._episode_id
            )
            self._verify_file_digest(path, expected_runtime, f"runtime {name}")
    @staticmethod
    def _verify_file_digest(path: Path, expected: str, label: str) -> None:
        if not path.is_file():
            if label.startswith("runtime "):
                raise ClaimEpisodeError(f"missing verified CityLearn {label}: {path}")
            raise ClaimEpisodeError(f"pinned {label} missing: {path}")
        actual = _sha256_file(path)
        if not hmac.compare_digest(actual, expected):
            raise ClaimEpisodeError(
                f"pinned {label} digest mismatch ({actual} != {expected})"
            )


class CityLearnClaimAdapter:
    """Claim-facing ``ProcessAdapter`` with a stateful episode runtime facade."""

    backend: ClassVar[str] = "citylearn"

    def __init__(
        self,
        battery_adapter: CityLearnBatteryPVAdapter | None = None,
        shared_adapter: CityLearnSharedAdapter | None = None,
    ) -> None:
        self._battery = battery_adapter if battery_adapter is not None else CityLearnBatteryPVAdapter()
        self._shared = shared_adapter if shared_adapter is not None else CityLearnSharedAdapter()
        self.scan_calls = 0
        self._processes: tuple[PhysicalProcess, ...] | None = None
        self._routes: dict[str, str] = {}

    # -- capability / scan surface (ProcessAdapter) -------------------------

    def capabilities(self) -> Sequence[BackendCapability]:
        caps: list[BackendCapability] = [
            BackendCapability(
                capability_id="citylearn_claim.battery_pv",
                backend=self.backend,
                provides=tuple(sorted(BATTERY_PROVIDES)),
                status=CapabilityStatus.EXECUTABLE_REPLAY_VERIFIED,
                evidence=(
                    _rel(self._battery.process_pool_path),
                    _rel(self._battery.replay_gate_path),
                ),
                metadata={"route": ROUTE_BATTERY_PV},
            )
        ]
        hvac_processes = self._hvac_processes()
        if hvac_processes:
            caps.append(
                BackendCapability(
                    capability_id="citylearn_claim.hvac",
                    backend=self.backend,
                    provides=tuple(sorted(HVAC_PROVIDES)),
                    status=CapabilityStatus.EXECUTABLE_REPLAY_VERIFIED,
                    evidence=(_rel(self._shared.source_index.v10_index.artifact_dir),),
                    metadata={
                        "route": ROUTE_HVAC_NATIVE,
                        "process_count": len(hvac_processes),
                    },
                )
            )
        return caps

    def scan(self, requirements: Sequence[ProcessRequirement]) -> Sequence[PhysicalProcess]:
        """Return verified battery/PV and native HVAC slices matching requirements."""
        self.scan_calls += 1
        self._ensure_processes()
        assert self._processes is not None
        if not requirements:
            return ()
        return tuple(
            process
            for process in self._processes
            if any(req.required_capabilities <= process.provided_capabilities for req in requirements)
        )

    def capability_report(self) -> dict[str, Any]:
        """JSON-serializable, per-subsystem claim status (fail closed elsewhere)."""
        by_status: dict[str, list[str]] = {}
        for cap in self.capabilities():
            by_status.setdefault(cap.status.value, []).extend(cap.provides)
        return {
            "backend": self.backend,
            "battery_pv": {
                "subsystem": SUBSYSTEM_BATTERY_PV,
                "status": CapabilityStatus.EXECUTABLE_REPLAY_VERIFIED.value,
                "route": ROUTE_BATTERY_PV,
            },
            "hvac": {
                "subsystem": SUBSYSTEM_HVAC,
                "status": CapabilityStatus.EXECUTABLE_REPLAY_VERIFIED.value,
                "route": ROUTE_HVAC_NATIVE,
                "available": bool(self._hvac_processes()),
            },
            "unsupported_subsystems": {
                subsystem: "fail_closed_out_of_scope"
                for subsystem in OUT_OF_SCOPE_SUBSYSTEMS
            },
            "provided_capabilities_by_status": by_status,
        }

    # -- episode runtime facade --------------------------------------------

    def open_episode(
        self, process_id: str
    ) -> CityLearnBatteryPVEpisode | CityLearnNativeHvacEpisode:
        """Open a stateful episode facade for one scanned process."""
        process = self._process_by_id(process_id)
        route = self._routes[process_id]
        if process.manifest.get("subsystem") in OUT_OF_SCOPE_SUBSYSTEMS:
            raise UnsupportedSubsystemError(
                f"{process_id}: subsystem {process.manifest.get('subsystem')!r} "
                "is fail-closed out of scope (no DHW/thermal-storage episodes)"
            )
        if route == ROUTE_BATTERY_PV:
            return CityLearnBatteryPVEpisode(process)
        legacy_episode_ids = process.manifest.get("legacy_episode_ids") or ()
        if not legacy_episode_ids:
            raise ClaimEpisodeError(f"{process_id}: no legacy episode to replay")
        return CityLearnNativeHvacEpisode(
            self._shared.source_index,
            episode_id=legacy_episode_ids[0],
            process_id=process_id,
            expected_building_id=str(process.manifest["building_id"]),
            expected_window=process.manifest["window"],
        )

    # -- internals ---------------------------------------------------------

    def _ensure_processes(self) -> None:
        if self._processes is not None:
            return
        routes: dict[str, str] = {}
        processes: list[PhysicalProcess] = []
        for process in self._battery.scan((_MATCH_ALL_REQUIREMENT,)):
            routes[process.process_id] = ROUTE_BATTERY_PV
            processes.append(process)
        for process in self._hvac_processes():
            routes[process.process_id] = ROUTE_HVAC_NATIVE
            processes.append(process)
        self._routes = routes
        self._processes = tuple(processes)

    def _hvac_processes(self) -> tuple[PhysicalProcess, ...]:
        """Legacy HVAC slices from the shared probe layer (probed, not re-run)."""
        return tuple(
            process
            for process in self._shared.processes()
            if process.manifest.get("subsystem") == SUBSYSTEM_HVAC
        )

    def _process_by_id(self, process_id: str) -> PhysicalProcess:
        self._ensure_processes()
        assert self._processes is not None
        if process_id not in self._routes:
            raise UnknownClaimProcessError(process_id)
        for process in self._processes:
            if process.process_id == process_id:
                return process
        raise UnknownClaimProcessError(process_id)  # pragma: no cover
