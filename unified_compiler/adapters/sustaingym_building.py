"""Bounded, claim-facing adapter over SustainGym's RC building environment.

Status: bounded adapter pending full reachability certification. The recorded
causal probe (``backend-survey/results/sustaingym.json``) verified action
feedback, cooling direction, and deterministic replay, but also showed that
the raw ``[-1, 1]`` HVAC interface produces physically impossible zone
temperatures. This adapter therefore never exposes the raw action space: the
public action surface is limited to the cooling envelope the probe actually
exercised (``[-0.05, 0.0]`` per zone, endpoints probed). Heating (positive
actions) is unverified and opt-in only, and is never claimed by any emitted
capability.

Public observations carry the actual environment clock, zone temperatures,
weather fields, and occupancy heat, and never evaluator labels (the upstream
``reward`` / ``reward_breakdown`` payload is dropped). Private state keeps the
full state vector, setpoints, and provenance digests.

The weather/RC/occupancy evolution of ``BuildingEnv`` is treated purely as
exogenous dynamics: no resident schedule is invented, no dataset is generated,
and no environment parameter is fabricated beyond the probe-pinned
configuration. The upstream API is pinned to commit
``eac2a4d5ce4ccf44b13c290edac72532c68e30f5`` (state layout ``(n + 4,)`` =
zone temperatures, outdoor temperature, ground temperature, normalized global
horizontal irradiance, occupancy power in kW; continuous ``Box(n)`` actions,
negative = cooling, masked per zone by ``ac_map``).
"""

from __future__ import annotations

import hashlib
import io
import importlib
import importlib.metadata
import json
import math
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, ClassVar, Mapping, Sequence

import numpy as np

from ..types import (
    BackendCapability,
    CapabilityStatus,
    PhysicalProcess,
    ProcessRequirement,
)
from .legacy_v10 import _canonical_json

V11_ROOT = Path(__file__).resolve().parents[2]
ITT_ROOT = V11_ROOT.parents[2]
DEFAULT_PROBE_RESULT = ITT_ROOT / "backend-survey" / "results" / "sustaingym.json"
DEFAULT_PROBE_SCRIPT = ITT_ROOT / "backend-survey" / "probes" / "sustaingym_building_probe.py"
# The work copy intentionally keeps the historical backend-survey directory
# read-only.  Resolve it explicitly when the isolated clone has no copy.
_ORIGINAL_BACKEND_SURVEY = Path("/Users/shanyingyu/DRPIE/home-design/ideas/implicit-temporal-intent/backend-survey")
if not DEFAULT_PROBE_RESULT.is_file() and (_ORIGINAL_BACKEND_SURVEY / "results/sustaingym.json").is_file():
    DEFAULT_PROBE_RESULT = _ORIGINAL_BACKEND_SURVEY / "results/sustaingym.json"
    DEFAULT_PROBE_SCRIPT = _ORIGINAL_BACKEND_SURVEY / "probes/sustaingym_building_probe.py"
DEFAULT_REPLAY_GATE = V11_ROOT / "generated" / "sustaingym_replay_gate_v1.json"
SUSTAINGYM_FALLBACK_SITE_PACKAGES = (
    V11_ROOT / "shared_runtime" / "sustaingym-site-packages"
)
PINNED_DISTRIBUTION_VERSION = "0.1.7"

PINNED_COMMIT = "eac2a4d5ce4ccf44b13c290edac72532c68e30f5"
PINNED_BUILDING = "ApartmentMidRise"
PINNED_WEATHER = "Hot_Dry"
PINNED_LOCATION = "Tucson"
PINNED_EPISODE_LEN = 288
PINNED_TIME_RESOLUTION_SECONDS = 300
PINNED_SEED = 200

PROBED_COOLING_MIN = -0.05
PROBED_COOLING_MAX = 0.0
UNVERIFIED_HEATING_MAX = 1.0

PROVIDES = frozenset(
    (
        "thermal.zone_temperature",
        "thermal.hvac_cooling_action",
        "weather.exogenous",
        "occupancy.exogenous",
    )
)

FORBIDDEN_MANIFEST_TOKENS = ("password", "secret", "token", "credential", "gold")
_NUMERIC_TOLERANCE = 1e-9


class SustainGymAdapterError(RuntimeError):
    """The SustainGym backend, its evidence, or its pinned API is unusable."""


class IllegalActionError(ValueError):
    """A caller-supplied action is outside the probed public action surface."""


class SustainGymBuildingAdapter:
    backend: ClassVar[str] = "sustaingym_building"

    def __init__(
        self,
        probe_result_path: Path | str | None = None,
        replay_gate_path: Path | str | None = None,
        allow_unverified_heating: bool = False,
    ) -> None:
        self.probe_result_path = Path(probe_result_path or DEFAULT_PROBE_RESULT)
        self.replay_gate_path = Path(replay_gate_path or DEFAULT_REPLAY_GATE)
        self.probe_script_path = DEFAULT_PROBE_SCRIPT
        self.allow_unverified_heating = bool(allow_unverified_heating)
        self.scan_calls = 0
        self.adapter_sha256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        self._probe: dict[str, Any] | None = None
        self._process: PhysicalProcess | None = None
        self._env: Any = None
        self._episode_seed: int | None = None
        self._episode_t_initial: tuple[float, ...] | None = None
        self._replay_id: str | None = None
        self._step_index = 0
        self._episode_done = True

    # -- static scan surface (ProcessAdapter protocol) ---------------------

    def capabilities(self) -> Sequence[BackendCapability]:
        self._ensure_probe()
        gate = self._load_replay_gate()
        gate_verified = gate is not None and gate.get("verified") is True
        capability_status = (
            CapabilityStatus.EXECUTABLE_REPLAY_VERIFIED
            if gate_verified
            else CapabilityStatus.DATA_PROBED_PENDING_REPLAY
        )
        evidence = [
            self._relative(self.probe_result_path),
            self._relative(self.probe_script_path),
            f"sustaingym@{PINNED_COMMIT}#sustaingym/envs/building/env.py",
        ]
        if gate is not None:
            evidence.append(self._relative(self.replay_gate_path))
        caps: list[BackendCapability] = [
            BackendCapability(
                capability_id="sustaingym_building.bounded_cooling",
                backend=self.backend,
                provides=tuple(sorted(PROVIDES)),
                status=capability_status,
                evidence=tuple(evidence),
                metadata={
                    "building": PINNED_BUILDING,
                    "weather": PINNED_WEATHER,
                    "location": PINNED_LOCATION,
                    "episode_len": PINNED_EPISODE_LEN,
                    "time_resolution_seconds": PINNED_TIME_RESOLUTION_SECONDS,
                    "probed_cooling_envelope": [PROBED_COOLING_MIN, PROBED_COOLING_MAX],
                    "raw_action_space_exposed": False,
                    "heating_claimed": False,
                    "replay_gate_verified": gate_verified,
                    "replay_gate_failures": (
                        list(gate.get("failure_reasons", ())) if gate else ["MISSING_REPLAY_GATE"]
                    ),
                    "binding": self.binding(),
                },
            )
        ]
        if self.allow_unverified_heating:
            caps.append(
                BackendCapability(
                    capability_id="sustaingym_building.unverified_heating",
                    backend=self.backend,
                    provides=(),
                    status=CapabilityStatus.CAPABILITY_UNVERIFIED,
                    evidence=tuple(evidence),
                    metadata={
                        "note": (
                            "opt-in heating envelope lacks causal probe evidence; "
                            "no certification is claimed"
                        ),
                        "binding": self.binding(),
                    },
                )
            )
        return caps

    def scan(self, requirements: Sequence[ProcessRequirement]) -> Sequence[PhysicalProcess]:
        self.scan_calls += 1
        self._ensure_process()
        if not requirements:
            return ()
        if not any(
            req.required_capabilities <= PROVIDES for req in requirements
        ):
            return ()
        return (self._process,)

    # -- stateful episode surface ------------------------------------------

    def reset(
        self,
        *,
        process_id: str | None = None,
        seed: int = PINNED_SEED,
        t_initial: Sequence[float] | None = None,
    ) -> dict[str, Any]:
        """Start a fresh episode and return the public initial observation."""
        self._ensure_process()
        process = self._process
        assert process is not None
        if process_id is not None and process_id != process.process_id:
            raise SustainGymAdapterError(
                f"unknown process {process_id!r}; expected {process.process_id!r}"
            )
        if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
            raise SustainGymAdapterError("seed must be a non-negative integer")
        t_initial_tuple = self._validate_t_initial(t_initial)

        BuildingEnv, ParameterGenerator = self._import_backend()
        env = self._build_env(BuildingEnv, ParameterGenerator, seed, t_initial_tuple)

        self._env = env
        self._episode_seed = seed
        self._episode_t_initial = t_initial_tuple
        self._replay_id = self._compute_replay_id(seed, t_initial_tuple)
        self._step_index = 0
        self._episode_done = False
        return self.observe()

    def observe(self) -> dict[str, Any]:
        """Return the public observation: clock, zones, weather, occupancy."""
        env = self._require_active_env()
        state = self._validated_state(env)
        return self._public_observation(state)

    def legal_actions(self) -> dict[str, Any]:
        """JSON-serializable schema of the currently legal public action space."""
        env = self._require_active_env()
        ac_map = np.asarray(env.parameters["ac_map"], dtype=float)
        zero_zones = [int(i) for i in np.flatnonzero(ac_map == 0.0)]
        heating_available = self.allow_unverified_heating
        return {
            "type": "hvac_power_per_zone",
            "units": "fraction of max HVAC power; negative = cooling, positive = heating",
            "shape": [int(env.n)],
            "cooling": {
                "minimum": PROBED_COOLING_MIN,
                "maximum": PROBED_COOLING_MAX,
                "verified": True,
                "evidence": [self._relative(self.probe_result_path)],
            },
            "heating": {
                "available": heating_available,
                "minimum": 0.0,
                "maximum": UNVERIFIED_HEATING_MAX if heating_available else 0.0,
                "verified": False,
                "status": "opt_in_pending_certification",
            },
            "zero_required_zones": zero_zones,
            "raw_action_space_exposed": False,
        }

    def step(self, action: Any) -> dict[str, Any]:
        """Validate and apply one action; return a label-free public transition."""
        env = self._require_active_env()
        if self._episode_done:
            raise SustainGymAdapterError("episode is done; call reset() first")
        validated = self._validate_action(action)
        state, _reward, terminated, truncated, _info = env.step(validated)
        self._step_index += 1
        terminated = bool(terminated)
        truncated = bool(truncated)
        self._episode_done = terminated or truncated
        state = self._validated_state(env, state)
        return {
            "step_index": self._step_index,
            "observation": self._public_observation(state),
            "terminated": terminated,
            "truncated": truncated,
            "episode_done": self._episode_done,
            "backend_terminated": terminated,
        }

    def private_state(self) -> dict[str, Any]:
        """Full arrays, setpoints, and provenance for the harness/evaluator."""
        env = self._require_active_env()
        state = self._validated_state(env)
        params = env.parameters
        return {
            "replay_id": self._require_replay_id(),
            "step_index": self._step_index,
            "episode_done": self._episode_done,
            "state_vector": [float(v) for v in state],
            "zone_temperatures_c": [float(v) for v in state[: env.n]],
            "target_setpoints_c": [float(v) for v in np.asarray(params["target"])],
            "ac_map": [float(v) for v in np.asarray(params["ac_map"])],
            "epoch_index": int(env.epoch),
            "episode_seed": self._episode_seed,
            "t_initial": (
                list(self._episode_t_initial)
                if self._episode_t_initial is not None
                else None
            ),
            "pinned": self.pinned_config(),
            "provenance": {
                "adapter_sha256": self.adapter_sha256,
                "probe_result_ref": self._relative(self.probe_result_path),
                "probe_result_sha256": self._probe_result_sha256(),
                "env_params_digest": self._params_digest(env),
                "heating_opt_in": self.allow_unverified_heating,
            },
        }

    def replay_id(self) -> str:
        """Replay identity bound to commit/config/weather/seed/adapter hash."""
        return self._require_replay_id()

    @property
    def done(self) -> bool:
        return self._episode_done

    # -- binding helpers -----------------------------------------------------

    def pinned_config(self) -> dict[str, Any]:
        return {
            "commit": PINNED_COMMIT,
            "building": PINNED_BUILDING,
            "weather": PINNED_WEATHER,
            "location": PINNED_LOCATION,
            "episode_len": PINNED_EPISODE_LEN,
            "time_resolution_seconds": PINNED_TIME_RESOLUTION_SECONDS,
            "cooling_envelope": [PROBED_COOLING_MIN, PROBED_COOLING_MAX],
            "heating_opt_in": self.allow_unverified_heating,
        }

    def binding(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "adapter_sha256": self.adapter_sha256,
            "pinned": self.pinned_config(),
        }

    # -- internals ------------------------------------------------------------

    def _relative(self, path: Path) -> str:
        try:
            return str(path.resolve().relative_to(ITT_ROOT))
        except ValueError:
            return str(path)

    def _ensure_probe(self) -> dict[str, Any]:
        if self._probe is not None:
            return self._probe
        if not self.probe_result_path.is_file():
            raise SustainGymAdapterError(
                f"missing recorded probe evidence: {self.probe_result_path}"
            )
        if not self.probe_script_path.is_file():
            raise SustainGymAdapterError(
                f"missing pinned probe script: {self.probe_script_path}"
            )
        try:
            probe = json.loads(self.probe_result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SustainGymAdapterError(
                f"invalid probe evidence {self.probe_result_path}: {exc}"
            ) from exc
        if not isinstance(probe, dict):
            raise SustainGymAdapterError("probe evidence is not a JSON object")
        if probe.get("repository_commit") != PINNED_COMMIT:
            raise SustainGymAdapterError(
                "probe evidence commit does not match pinned adapter commit"
            )
        for key in (
            "action_feedback",
            "cooling_direction_correct",
            "deterministic_replay",
        ):
            if probe.get(key) is not True:
                raise SustainGymAdapterError(
                    f"probe evidence does not certify {key!r}"
                )
        if probe.get("causal_probe_pass") is not True:
            raise SustainGymAdapterError("probe evidence fails the causal checks")
        if probe.get("raw_action_space_plausible") is not False:
            raise SustainGymAdapterError(
                "probe evidence unexpectedly certifies the raw action space; "
                "adapter binding is stale"
            )
        cool = probe.get("cool")
        if not isinstance(cool, dict) or cool.get("action") != PROBED_COOLING_MIN:
            raise SustainGymAdapterError(
                "probe evidence lacks the probed bounded cooling endpoint"
            )
        self._probe = probe
        return self._probe

    def _load_replay_gate(self) -> dict[str, Any] | None:
        """Read the optional replay gate and reject stale/forged certificates.

        A missing gate deliberately leaves the capability in its existing
        ``DATA_PROBED_PENDING_REPLAY`` state.  A present but malformed or
        stale gate is an error: silently ignoring a certificate that no longer
        describes this adapter would make the status fail open.
        """
        if not self.replay_gate_path.is_file():
            return None
        try:
            gate = json.loads(self.replay_gate_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SustainGymAdapterError(
                f"invalid SustainGym replay gate {self.replay_gate_path}: {exc}"
            ) from exc
        if not isinstance(gate, dict):
            raise SustainGymAdapterError("SustainGym replay gate is not a JSON object")
        if gate.get("schema_version") != "sustaingym-replay-gate-v1":
            raise SustainGymAdapterError("unsupported SustainGym replay gate schema")
        backend = gate.get("backend")
        if not isinstance(backend, Mapping) or backend.get("commit") != PINNED_COMMIT:
            raise SustainGymAdapterError("replay gate backend commit is not pinned")
        if gate.get("probe_result_sha256") != self._probe_result_sha256():
            raise SustainGymAdapterError("replay gate is stale for the pinned probe result")
        if gate.get("probe_script_sha256") != self._probe_script_sha256():
            raise SustainGymAdapterError("replay gate is stale for the pinned probe script")
        if gate.get("adapter_sha256") != self.adapter_sha256:
            raise SustainGymAdapterError("replay gate is stale for this adapter source")
        if not isinstance(gate.get("verified"), bool):
            raise SustainGymAdapterError("replay gate lacks a boolean verified verdict")
        if gate["verified"] and gate.get("backend_runtime_commit") != PINNED_COMMIT:
            raise SustainGymAdapterError(
                "verified replay gate lacks the pinned backend runtime commit"
            )
        if gate["verified"]:
            required = (
                "backend_importable",
                "probe_evidence_valid",
                "all_replays_deterministic",
                "action_sensitive",
                "all_replays_completed",
                "all_temperatures_finite",
                "all_temperatures_physically_plausible",
            )
            if any(gate.get(key) is not True for key in required):
                raise SustainGymAdapterError(
                    "replay gate claims verified without every required check"
                )
        return gate

    def _probe_result_sha256(self) -> str:
        return hashlib.sha256(self.probe_result_path.read_bytes()).hexdigest()

    def _probe_script_sha256(self) -> str:
        return hashlib.sha256(self.probe_script_path.read_bytes()).hexdigest()

    def _ensure_process(self) -> PhysicalProcess:
        self._ensure_probe()
        if self._process is not None:
            return self._process
        binding_json = _canonical_json(self.binding())
        digest = hashlib.sha256(binding_json.encode("utf-8")).hexdigest()
        source_id = (
            f"sustaingym://{PINNED_BUILDING}/{PINNED_WEATHER}/{PINNED_LOCATION}"
            f"/episodes:{PINNED_EPISODE_LEN}"
        )
        manifest = {
            "probe": "sustaingym_building_bounded_v1",
            "pinned": self.pinned_config(),
            "action_surface": {
                "raw_exposed": False,
                "probed_cooling_envelope": [PROBED_COOLING_MIN, PROBED_COOLING_MAX],
                "envelope_note": "endpoints probed; interior is the bounded envelope",
                "heating": "unverified_opt_in_pending_certification",
            },
            "evidence": {
                "probe_result": self._relative(self.probe_result_path),
                "probe_script": self._relative(self.probe_script_path),
                "replay_gate": self._relative(self.replay_gate_path),
                "commit": PINNED_COMMIT,
            },
            "status_note": (
                "bounded adapter is executable only when the pinned replay gate "
                "has a verified verdict; otherwise it remains pending replay"
            ),
            "exogenous_dynamics": (
                "upstream TMY3 weather interpolation, RC thermal model, "
                "EnergyPlus occupancy heat coefficients; no resident schedule "
                "invented or dataset generated here"
            ),
        }
        blob = json.dumps(manifest).lower()
        for token in FORBIDDEN_MANIFEST_TOKENS:
            assert token not in blob, f"manifest token {token!r} leaked"
        self._process = PhysicalProcess(
            process_id=f"hvac_sgbld_{digest[:16]}",
            domain="hvac",
            backend=self.backend,
            backend_version=f"sustaingym@{PINNED_COMMIT}",
            source_id=source_id,
            source_hash=digest,
            horizon_steps=PINNED_EPISODE_LEN,
            observation_interval_seconds=float(PINNED_TIME_RESOLUTION_SECONDS),
            provided_capabilities=PROVIDES,
            state_variables=(
                "zone_temperature",
                "outdoor_temperature",
                "ground_temperature",
                "ghi_normalized",
                "occupancy_power_kw",
            ),
            action_types=("hvac_cooling_power",),
            manifest=manifest,
        )
        return self._process

    def _import_backend(self) -> tuple[Any, Any]:
        try:
            module = importlib.import_module("sustaingym.envs.building")
            self._verify_backend_version()
        except ImportError as normal_exc:
            fallback = SUSTAINGYM_FALLBACK_SITE_PACKAGES
            if not fallback.is_dir():
                raise SustainGymAdapterError(
                    "sustaingym is not importable and the project-local fallback "
                    f"runtime is missing: {fallback}"
                ) from normal_exc
            # The fallback is deliberately consulted only after the ordinary
            # import failed.  Keep it on sys.path because SustainGym imports
            # its data and sibling modules lazily during environment setup.
            for module_name in list(sys.modules):
                if module_name == "sustaingym" or module_name.startswith("sustaingym."):
                    del sys.modules[module_name]
            if str(fallback) not in sys.path:
                sys.path.insert(0, str(fallback))
            importlib.invalidate_caches()
            try:
                module = importlib.import_module("sustaingym.envs.building")
                self._verify_backend_version(path=fallback)
            except Exception as fallback_exc:
                raise SustainGymAdapterError(
                    "project-local SustainGym fallback is unusable or has the "
                    f"wrong version (expected {PINNED_DISTRIBUTION_VERSION}): "
                    f"{fallback}"
                ) from fallback_exc
        except Exception as exc:
            raise SustainGymAdapterError(
                "sustaingym is installed but does not match the pinned "
                f"distribution version {PINNED_DISTRIBUTION_VERSION}"
            ) from exc
        BuildingEnv = getattr(module, "BuildingEnv", None)
        ParameterGenerator = getattr(module, "ParameterGenerator", None)
        if BuildingEnv is None or ParameterGenerator is None:
            raise SustainGymAdapterError(
                "SustainGym building module lacks BuildingEnv or ParameterGenerator"
            )
        return BuildingEnv, ParameterGenerator

    @staticmethod
    def _verify_backend_version(path: Path | None = None) -> None:
        if path is None:
            metadata = importlib.metadata.distribution("sustaingym")
        else:
            candidates = [
                dist
                for dist in importlib.metadata.distributions(path=[str(path)])
                if (dist.metadata.get("Name") or "").lower() == "sustaingym"
            ]
            if len(candidates) != 1:
                raise SustainGymAdapterError(
                    f"expected exactly one sustaingym distribution in {path}, "
                    f"found {len(candidates)}"
                )
            metadata = candidates[0]
        if metadata.version != PINNED_DISTRIBUTION_VERSION:
            raise SustainGymAdapterError(
                "SustainGym distribution version "
                f"{metadata.version!r} does not match pinned "
                f"{PINNED_DISTRIBUTION_VERSION!r}"
            )

    def _build_env(
        self,
        BuildingEnv: Any,
        ParameterGenerator: Any,
        seed: int,
        t_initial: tuple[float, ...] | None,
    ) -> Any:
        with redirect_stdout(io.StringIO()):
            try:
                params = ParameterGenerator(
                    building=PINNED_BUILDING,
                    weather=PINNED_WEATHER,
                    location=PINNED_LOCATION,
                    time_res=PINNED_TIME_RESOLUTION_SECONDS,
                    episode_len=PINNED_EPISODE_LEN,
                )
                env = BuildingEnv(params)
            except Exception as exc:
                raise SustainGymAdapterError(
                    f"failed to construct pinned SustainGym BuildingEnv: {exc}"
                ) from exc
        self._validate_env_structure(env)
        options: dict[str, Any] = {}
        if t_initial is not None:
            options["T_initial"] = np.asarray(t_initial, dtype=np.float64)
        try:
            state, _info = env.reset(seed=seed, options=options or None)
        except Exception as exc:
            raise SustainGymAdapterError(f"backend reset failed: {exc}") from exc
        self._validated_state(env, state)
        return env

    def _validate_env_structure(self, env: Any) -> None:
        n = getattr(env, "n", None)
        if not isinstance(n, int) or n <= 0:
            raise SustainGymAdapterError("backend zone count is not a positive int")
        if getattr(env, "is_continuous_action", None) is not True:
            raise SustainGymAdapterError(
                "backend action space is not the pinned continuous Box(n)"
            )
        action_shape = tuple(getattr(env.action_space, "shape", ()))
        if action_shape != (n,):
            raise SustainGymAdapterError(
                f"backend action shape {action_shape} does not match (n,) with n={n}"
            )
        obs_shape = tuple(getattr(env.observation_space, "shape", ()))
        if obs_shape != (n + 4,):
            raise SustainGymAdapterError(
                f"backend observation shape {obs_shape} does not match pinned "
                f"(n + 4,) layout"
            )
        params = getattr(env, "parameters", None)
        if not isinstance(params, Mapping):
            raise SustainGymAdapterError("backend parameters are not a mapping")
        if int(params.get("time_resolution", -1)) != PINNED_TIME_RESOLUTION_SECONDS:
            raise SustainGymAdapterError(
                "backend time resolution deviates from the pinned configuration"
            )
        if int(params.get("episode_len", -1)) != PINNED_EPISODE_LEN:
            raise SustainGymAdapterError(
                "backend episode length deviates from the pinned configuration"
            )
        ac_map = np.asarray(params.get("ac_map"), dtype=float)
        if ac_map.shape != (n,) or not np.all(np.isfinite(ac_map)):
            raise SustainGymAdapterError("backend ac_map is malformed")
        if not np.all((ac_map == 0.0) | (ac_map == 1.0)):
            raise SustainGymAdapterError("backend ac_map is not a 0/1 mask")
        action_space_low = np.asarray(env.action_space.low, dtype=float)
        action_space_high = np.asarray(env.action_space.high, dtype=float)
        if not np.allclose(action_space_low, -ac_map) or not np.allclose(
            action_space_high, ac_map
        ):
            raise SustainGymAdapterError(
                "backend action bounds do not match the pinned ac_map masking"
            )

    def _validated_state(
        self, env: Any, state: np.ndarray | None = None
    ) -> np.ndarray:
        if state is None:
            state = getattr(env, "state", None)
        if not isinstance(state, np.ndarray):
            raise SustainGymAdapterError("backend state is not an ndarray")
        if state.shape != (env.n + 4,):
            raise SustainGymAdapterError(
                f"backend state shape {state.shape} deviates from pinned (n + 4,)"
            )
        values = np.asarray(state, dtype=float)
        if not np.all(np.isfinite(values)):
            raise SustainGymAdapterError(
                "backend state contains non-finite values; refusing to expose it"
            )
        return values

    def _public_observation(self, state: np.ndarray) -> dict[str, Any]:
        env = self._require_active_env()
        n = int(env.n)
        time_resolution = int(env.parameters["time_resolution"])
        epoch = int(env.epoch)
        elapsed_seconds = epoch * time_resolution
        return {
            "step_index": self._step_index,
            "clock": {
                "epoch_index": epoch,
                "elapsed_seconds": elapsed_seconds,
                "seconds_per_step": time_resolution,
                "hour_of_year": elapsed_seconds // 3600,
            },
            "zone_temperatures_c": [float(v) for v in state[:n]],
            "weather": {
                "outdoor_temperature_c": float(state[n]),
                "ground_temperature_c": float(state[n + 1]),
                "global_horizontal_irradiance_normalized": float(state[n + 2]),
            },
            "occupancy": {
                "occupancy_power_kw": float(state[n + 3]),
            },
            "episode_done": self._episode_done,
        }

    def _validate_t_initial(
        self, t_initial: Sequence[float] | None
    ) -> tuple[float, ...] | None:
        if t_initial is None:
            return None
        try:
            values = tuple(float(v) for v in t_initial)
        except (TypeError, ValueError):
            raise SustainGymAdapterError(
                "t_initial must be a sequence of finite numbers"
            ) from None
        if not all(math.isfinite(v) for v in values):
            raise SustainGymAdapterError("t_initial must contain only finite values")
        env = self._env
        if env is not None:
            expected = int(env.n)
        else:
            expected = self._expected_zone_count()
        if expected is not None and len(values) != expected:
            raise SustainGymAdapterError(
                f"t_initial must have exactly {expected} zone values"
            )
        return values

    def _expected_zone_count(self) -> int | None:
        probe = self._ensure_probe()
        cool = probe.get("cool")
        if isinstance(cool, dict) and isinstance(cool.get("zone_count"), int):
            return int(cool["zone_count"])
        return None

    def _validate_action(self, action: Any) -> np.ndarray:
        env = self._require_active_env()
        n = int(env.n)
        if isinstance(action, Mapping) or isinstance(action, str):
            raise IllegalActionError(
                "action must be a per-zone numeric sequence, not a mapping/string"
            )
        try:
            values = np.asarray(
                [float(v) for v in action], dtype=np.float64
            )
        except (TypeError, ValueError):
            raise IllegalActionError(
                "action must be a per-zone numeric sequence"
            ) from None
        if values.shape != (n,):
            raise IllegalActionError(
                f"action shape {values.shape} does not match zone count {n}"
            )
        if not np.all(np.isfinite(values)):
            raise IllegalActionError("action contains non-finite values")
        ac_map = np.asarray(env.parameters["ac_map"], dtype=float)
        zero_required = ac_map == 0.0
        if np.any(np.abs(values[zero_required]) > _NUMERIC_TOLERANCE):
            raise IllegalActionError(
                "action assigns power to zones without an HVAC unit (ac_map=0)"
            )
        lower = np.where(zero_required, 0.0, PROBED_COOLING_MIN)
        if self.allow_unverified_heating:
            upper = np.where(zero_required, 0.0, UNVERIFIED_HEATING_MAX)
        else:
            upper = np.where(zero_required, 0.0, PROBED_COOLING_MAX)
        if np.any(values < lower - _NUMERIC_TOLERANCE) or np.any(
            values > upper + _NUMERIC_TOLERANCE
        ):
            schema = self.legal_actions()
            raise IllegalActionError(
                f"action outside the legal envelope {schema['cooling']} "
                f"(heating: {schema['heating']})"
            )
        space_low = np.asarray(env.action_space.low, dtype=float)
        space_high = np.asarray(env.action_space.high, dtype=float)
        if np.any(values < space_low - _NUMERIC_TOLERANCE) or np.any(
            values > space_high + _NUMERIC_TOLERANCE
        ):
            raise IllegalActionError("action outside the backend action space")
        return values.astype(np.float32)

    def _params_digest(self, env: Any) -> str:
        params = env.parameters
        hasher = hashlib.sha256()
        for key in ("out_temp", "ground_temp", "ghi", "metabolism"):
            array = np.asarray(params[key], dtype=np.float64)
            hasher.update(key.encode("utf-8"))
            hasher.update(array.tobytes())
        for key in ("target", "ac_map"):
            array = np.asarray(params[key], dtype=np.float64)
            hasher.update(key.encode("utf-8"))
            hasher.update(array.tobytes())
        for key in ("n", "time_resolution", "episode_len", "max_power"):
            if key not in params:
                raise SustainGymAdapterError(
                    f"backend parameters lack pinned key {key!r}"
                )
            hasher.update(f"{key}={params[key]!r}".encode("utf-8"))
        return hasher.hexdigest()

    def _compute_replay_id(
        self, seed: int, t_initial: tuple[float, ...] | None
    ) -> str:
        binding = self.binding()
        binding["episode"] = {
            "seed": seed,
            "t_initial": list(t_initial) if t_initial is not None else None,
            "process_id": self._process.process_id if self._process else None,
        }
        return hashlib.sha256(_canonical_json(binding).encode("utf-8")).hexdigest()

    def _require_active_env(self) -> Any:
        if self._env is None or self._replay_id is None:
            raise SustainGymAdapterError("no active episode; call reset() first")
        return self._env

    def _require_replay_id(self) -> str:
        if self._replay_id is None:
            raise SustainGymAdapterError("no active episode; call reset() first")
        return self._replay_id
