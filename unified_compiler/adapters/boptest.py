"""Fail-closed BOPTEST REST adapter (pinned ``twozone_apartment_hydronic``).

The BOPTEST REST contract (advance / initialize / scenario / measurements /
inputs / forecast) is verified by API inspection only.  The bundled FMU is a
Linux binary and no live service is runnable on this machine, so the adapter
stays ``API_VERIFIED_NOT_DATA_PROBED`` and :meth:`BopTestRestAdapter.scan`
emits no release-ready :class:`PhysicalProcess` unless the caller explicitly
supplies a verified manifest.

Public observations never include evaluator labels (KPI-style signals) or any
future realization; forecasts are retrievable through
:meth:`BopTestRestAdapter.get_forecast` as evaluator/exogenous context and are
never merged into public observations.

No credentials are accepted or stored; no dataset generation happens here.
"""

from __future__ import annotations

import hashlib
import json
import math
import socket
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Mapping, Sequence

from ..types import BackendCapability, CapabilityStatus, PhysicalProcess, ProcessRequirement

PINNED_TESTCASE = "twozone_apartment_hydronic"
PINNED_COMMIT = "0f8a467cb1823f005b6512937e9333c65e1e483e"
DEFAULT_BASE_URL = "http://127.0.0.1:5000"
DEFAULT_TIMEOUT_SECONDS = 30.0
BOUNDS_TOLERANCE = 1e-6
SOURCE_HASH_HEX_LENGTH = 64

PROVIDES = frozenset(
    (
        "zone_thermal_dynamics",
        "weather",
        "occupancy_schedule",
        "energy_price_signal",
        "hvac_control_action",
    )
)

EVIDENCE: tuple[str, ...] = (
    "backend-survey/BACKEND_MATRIX.md#boptest--b",
    "backend-survey/CLAIM_BACKEND_SHORTLIST_V1.md",
    f"https://github.com/ibpsa/project1-boptest commit {PINNED_COMMIT}",
)

PRIVATE_MEASUREMENT_MARKERS: tuple[str, ...] = ("kpi",)


class BopTestRestError(RuntimeError):
    """Raised on any contract, transport, or schema violation (fail-closed)."""


@dataclass(frozen=True)
class LegalAction:
    name: str
    minimum: float | None
    maximum: float | None


def _adapter_source_hash() -> str:
    return hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()


class BopTestRestAdapter:
    """ProcessAdapter + lifecycle surface over the BOPTEST REST service."""

    backend: ClassVar[str] = "boptest_rest"

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        testcase: str = PINNED_TESTCASE,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        verified_manifest: Mapping[str, Any] | None = None,
    ) -> None:
        if not isinstance(base_url, str) or not base_url.startswith(("http://", "https://")):
            raise BopTestRestError("BOPTEST base_url must be an http(s) URL")
        if testcase != PINNED_TESTCASE:
            raise BopTestRestError(f"only pinned testcase {PINNED_TESTCASE!r} is supported")
        if not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise BopTestRestError("timeout_seconds must be a positive number")
        self.base_url = base_url.rstrip("/")
        self.testcase = testcase
        self.timeout_seconds = float(timeout_seconds)
        self.verified_manifest = verified_manifest
        self.testid: str | None = None
        self.service_version: str | None = None
        self._scenario: dict[str, Any] = {}
        self._seed: int | None = None
        self._start_time: float | None = None
        self._warmup_period: float | None = None
        self._input_metadata: dict[str, dict[str, Any]] | None = None
        self._measurement_metadata: dict[str, dict[str, Any]] | None = None
        self._last_raw: dict[str, float] | None = None

    # ------------------------------------------------------------------
    # ProcessAdapter surface
    # ------------------------------------------------------------------
    def capabilities(self) -> Sequence[BackendCapability]:
        return (
            BackendCapability(
                capability_id=f"boptest_rest.{PINNED_TESTCASE}",
                backend=self.backend,
                provides=tuple(sorted(PROVIDES)),
                status=CapabilityStatus.API_VERIFIED_NOT_DATA_PROBED,
                evidence=EVIDENCE,
                metadata={
                    "testcase": self.testcase,
                    "pinned_commit": PINNED_COMMIT,
                    "adapter_source_sha256": _adapter_source_hash(),
                    "note": "REST contract inspected; no live Linux FMU service probed",
                },
            ),
        )

    def scan(self, requirements: Sequence[ProcessRequirement]) -> Sequence[PhysicalProcess]:
        if self.verified_manifest is None:
            return ()
        process = self._process_from_verified_manifest()
        if not any(req.required_capabilities <= process.provided_capabilities for req in requirements):
            return ()
        return (process,)

    def _process_from_verified_manifest(self) -> PhysicalProcess:
        manifest = self.verified_manifest
        if not isinstance(manifest, Mapping):
            raise BopTestRestError("verified manifest must be a mapping")
        if manifest.get("testcase") != self.testcase:
            raise BopTestRestError("verified manifest testcase mismatch")
        if manifest.get("replay_verified") is not True:
            raise BopTestRestError("verified manifest is not replay_verified")
        source_hash = manifest.get("source_hash")
        if not isinstance(source_hash, str) or len(source_hash) != SOURCE_HASH_HEX_LENGTH:
            raise BopTestRestError("verified manifest source_hash must be a 64-hex digest")
        horizon_steps = manifest.get("horizon_steps")
        if not isinstance(horizon_steps, int) or isinstance(horizon_steps, bool) or horizon_steps <= 0:
            raise BopTestRestError("verified manifest horizon_steps must be a positive int")
        interval = manifest.get("observation_interval_seconds")
        if not isinstance(interval, (int, float)) or isinstance(interval, bool) or interval <= 0:
            raise BopTestRestError("verified manifest observation_interval_seconds must be positive")
        capabilities = manifest.get("provided_capabilities")
        if not isinstance(capabilities, (list, tuple)) or not set(capabilities) <= PROVIDES:
            raise BopTestRestError("verified manifest provided_capabilities exceed advertised set")
        state_variables = manifest.get("state_variables")
        action_types = manifest.get("action_types")
        if not isinstance(state_variables, (list, tuple)) or not all(isinstance(v, str) for v in state_variables):
            raise BopTestRestError("verified manifest state_variables must be strings")
        if not isinstance(action_types, (list, tuple)) or not all(isinstance(v, str) for v in action_types):
            raise BopTestRestError("verified manifest action_types must be strings")
        backend_version = manifest.get("backend_version")
        if not isinstance(backend_version, str) or not backend_version:
            raise BopTestRestError("verified manifest backend_version must be a non-empty string")
        return PhysicalProcess(
            process_id=f"boptest_rest.{self.testcase}.{source_hash[:12]}",
            domain="residential_thermal",
            backend=self.backend,
            backend_version=backend_version,
            source_id=f"{self.testcase}:{manifest.get('source_id', 'pinned')}",
            source_hash=source_hash,
            horizon_steps=horizon_steps,
            observation_interval_seconds=float(interval),
            provided_capabilities=frozenset(capabilities),
            state_variables=tuple(state_variables),
            action_types=tuple(action_types),
            manifest={
                "pinned_commit": PINNED_COMMIT,
                "adapter_source_sha256": _adapter_source_hash(),
                "verified_manifest": dict(manifest),
            },
        )

    # ------------------------------------------------------------------
    # Strict HTTP layer (stdlib only, bounded timeout, no credentials)
    # ------------------------------------------------------------------
    def _request(self, method: str, path: str, body: Mapping[str, Any] | None = None) -> dict[str, Any]:
        url = self.base_url + path
        data = None
        headers = {"Accept": "application/json"}
        if body is not None:
            data = json.dumps(dict(body)).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:512]
            raise BopTestRestError(f"BOPTEST HTTP {exc.code} on {method} {path}: {detail}") from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
            raise BopTestRestError(f"BOPTEST transport failure on {method} {path}: {exc}") from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BopTestRestError(f"BOPTEST non-JSON response on {method} {path}") from exc
        if not isinstance(payload, dict):
            raise BopTestRestError(f"BOPTEST response is not an object on {method} {path}")
        return payload

    @staticmethod
    def _require_number(value: Any, context: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise BopTestRestError(f"BOPTEST {context} must be a finite number")
        return float(value)

    @staticmethod
    def _validate_point_metadata(payload: dict[str, Any], context: str) -> dict[str, dict[str, Any]]:
        metadata: dict[str, dict[str, Any]] = {}
        for name, entry in payload.items():
            if not isinstance(name, str) or not isinstance(entry, dict):
                raise BopTestRestError(f"BOPTEST {context} metadata entry is malformed")
            unit = entry.get("Unit")
            description = entry.get("Description")
            if not isinstance(unit, str) or not isinstance(description, str):
                raise BopTestRestError(f"BOPTEST {context} metadata for {name!r} lacks Unit/Description")
            bounds: dict[str, float | None] = {}
            for bound in ("Minimum", "Maximum"):
                raw = entry.get(bound)
                bounds[bound] = None if raw is None else BopTestRestAdapter._require_number(raw, f"{context} {name} {bound}")
            metadata[name] = {"Unit": unit, "Description": description, **bounds}
        return metadata

    # ------------------------------------------------------------------
    # Lifecycle endpoints
    # ------------------------------------------------------------------
    def fetch_version(self) -> str:
        payload = self._request("GET", "/version")
        version = payload.get("version")
        if not isinstance(version, str) or not version:
            raise BopTestRestError("BOPTEST /version did not advertise a version string")
        self.service_version = version
        return version

    def select_testcase(self, run_name: str | None = None) -> str:
        name = run_name or f"{self.testcase}_run"
        if not isinstance(name, str) or not name or "/" in name:
            raise BopTestRestError("run_name must be a non-empty path-free string")
        payload = self._request("POST", f"/testcases/{self.testcase}/{name}")
        testid = payload.get("testid", name)
        if not isinstance(testid, str) or not testid:
            raise BopTestRestError("BOPTEST testcase selection returned no usable testid")
        self.testid = testid
        return testid

    def _require_testid(self) -> str:
        if self.testid is None:
            raise BopTestRestError("BOPTEST test not instantiated; call select_testcase() first")
        return self.testid

    def initialize(self, start_time: float, warmup_period: float) -> dict[str, Any]:
        testid = self._require_testid()
        start_time = self._require_number(start_time, "start_time")
        warmup_period = self._require_number(warmup_period, "warmup_period")
        if start_time < 0 or warmup_period < 0:
            raise BopTestRestError("start_time and warmup_period must be non-negative")
        payload = self._request(
            "PUT",
            f"/initialize/{testid}",
            {"start_time": start_time, "warmup_period": warmup_period},
        )
        self._require_number(payload.get("time"), "initialize response time")
        self._start_time = start_time
        self._warmup_period = warmup_period
        self._last_raw = None
        return {"time": payload["time"]}

    def set_scenario(self, time_period: str | None = None, electricity_price: str | None = None) -> dict[str, Any]:
        testid = self._require_testid()
        body: dict[str, Any] = {}
        if time_period is not None:
            if not isinstance(time_period, str) or not time_period:
                raise BopTestRestError("time_period must be a non-empty string when provided")
            body["time_period"] = time_period
        if electricity_price is not None:
            if not isinstance(electricity_price, str) or not electricity_price:
                raise BopTestRestError("electricity_price must be a non-empty string when provided")
            body["electricity_price"] = electricity_price
        if not body:
            raise BopTestRestError("set_scenario requires time_period and/or electricity_price")
        payload = self._request("PUT", f"/scenario/{testid}", body)
        scenario: dict[str, Any] = {}
        for key in ("time_period", "electricity_price"):
            if key in payload:
                if not isinstance(payload[key], str):
                    raise BopTestRestError(f"BOPTEST scenario response {key} is not a string")
                scenario[key] = payload[key]
        if not scenario:
            raise BopTestRestError("BOPTEST scenario response echoed no scenario keys")
        self._scenario = scenario
        return dict(scenario)

    def set_seed(self, seed: int) -> dict[str, Any]:
        """Set the service random seed; raises if the service does not echo it."""
        testid = self._require_testid()
        if not isinstance(seed, int) or isinstance(seed, bool) or seed < 0:
            raise BopTestRestError("seed must be a non-negative int")
        payload = self._request("PUT", f"/parameters/{testid}", {"random_seed": seed})
        echoed = payload.get("random_seed")
        if not isinstance(echoed, int) or isinstance(echoed, bool) or echoed != seed:
            raise BopTestRestError("BOPTEST service did not confirm the random seed")
        self._seed = seed
        return {"random_seed": seed}

    def get_measurements(self) -> dict[str, dict[str, Any]]:
        testid = self._require_testid()
        payload = self._request("GET", f"/measurements/{testid}")
        self._measurement_metadata = self._validate_point_metadata(payload, "measurements")
        return {name: dict(entry) for name, entry in self._measurement_metadata.items()}

    def get_inputs(self) -> dict[str, dict[str, Any]]:
        testid = self._require_testid()
        payload = self._request("GET", f"/inputs/{testid}")
        self._input_metadata = self._validate_point_metadata(payload, "inputs")
        return {name: dict(entry) for name, entry in self._input_metadata.items()}

    def get_forecast(self, point_names: Sequence[str], horizon: float, interval: float) -> dict[str, Any]:
        """Evaluator/exogenous forecast retrieval; never merged into observations."""
        testid = self._require_testid()
        if not isinstance(point_names, (list, tuple)) or not point_names:
            raise BopTestRestError("forecast point_names must be a non-empty sequence")
        if not all(isinstance(name, str) and name for name in point_names):
            raise BopTestRestError("forecast point_names must be non-empty strings")
        horizon = self._require_number(horizon, "forecast horizon")
        interval = self._require_number(interval, "forecast interval")
        if horizon <= 0 or interval <= 0:
            raise BopTestRestError("forecast horizon and interval must be positive")
        query = urllib.parse.urlencode(
            {"point_names": json.dumps(list(point_names)), "horizon": horizon, "interval": interval}
        )
        payload = self._request("GET", f"/forecast/{testid}?{query}")
        for name, series in payload.items():
            if not isinstance(name, str) or not isinstance(series, list):
                raise BopTestRestError("BOPTEST forecast response is not a name->series mapping")
        return payload

    # ------------------------------------------------------------------
    # Actions and observations
    # ------------------------------------------------------------------
    def legal_actions(self) -> tuple[LegalAction, ...]:
        if self._input_metadata is None:
            raise BopTestRestError("input metadata not loaded; call get_inputs() first")
        return tuple(
            LegalAction(name=name, minimum=entry["Minimum"], maximum=entry["Maximum"])
            for name, entry in sorted(self._input_metadata.items())
        )

    def step(self, actions: Mapping[str, float]) -> dict[str, float]:
        testid = self._require_testid()
        if not isinstance(actions, Mapping):
            raise BopTestRestError("actions must be a mapping of input name to value")
        if self._input_metadata is None:
            raise BopTestRestError("input metadata not loaded; call get_inputs() before step()")
        body: dict[str, float] = {}
        for name, value in actions.items():
            entry = self._input_metadata.get(name)
            if entry is None:
                raise BopTestRestError(f"action {name!r} is not an advertised BOPTEST input")
            numeric = self._require_number(value, f"action {name}")
            minimum, maximum = entry["Minimum"], entry["Maximum"]
            if minimum is not None and numeric < minimum - BOUNDS_TOLERANCE:
                raise BopTestRestError(f"action {name}={numeric} below advertised minimum {minimum}")
            if maximum is not None and numeric > maximum + BOUNDS_TOLERANCE:
                raise BopTestRestError(f"action {name}={numeric} above advertised maximum {maximum}")
            body[name] = numeric
        payload = self._request("PUT", f"/advance/{testid}", body)
        raw: dict[str, float] = {}
        for name, value in payload.items():
            if not isinstance(name, str):
                raise BopTestRestError("BOPTEST advance response key is not a string")
            raw[name] = self._require_number(value, f"advance measurement {name}")
        self._last_raw = raw
        return self.observe()

    def observe(self) -> dict[str, float]:
        """Public observations: numeric measurements only, no evaluator labels."""
        if self._last_raw is None:
            raise BopTestRestError("no observation available; call step() first")
        return {
            name: value
            for name, value in self._last_raw.items()
            if not any(marker in name.lower() for marker in PRIVATE_MEASUREMENT_MARKERS)
        }

    def private_state(self) -> dict[str, Any]:
        """Evaluator-only view: raw measurements, lifecycle binding, replay id."""
        if self._last_raw is None:
            raise BopTestRestError("no private state available; call step() first")
        return {
            "testid": self.testid,
            "service_version": self.service_version,
            "testcase": self.testcase,
            "scenario": dict(self._scenario),
            "seed": self._seed,
            "start_time": self._start_time,
            "warmup_period": self._warmup_period,
            "raw_measurements": dict(self._last_raw),
            "replay_id": self.replay_id(),
        }

    def replay_id(self) -> str:
        """Deterministic id binding testcase/version/scenario/start/warmup/seed."""
        if self._start_time is None or self._warmup_period is None:
            raise BopTestRestError("replay_id requires an initialized window")
        binding = {
            "adapter_source_sha256": _adapter_source_hash(),
            "backend": self.backend,
            "pinned_commit": PINNED_COMMIT,
            "scenario": dict(self._scenario),
            "seed": self._seed,
            "service_version": self.service_version,
            "start_time": self._start_time,
            "testcase": self.testcase,
            "warmup_period": self._warmup_period,
        }
        canonical = json.dumps(binding, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "BOUNDS_TOLERANCE",
    "DEFAULT_BASE_URL",
    "DEFAULT_TIMEOUT_SECONDS",
    "EVIDENCE",
    "LegalAction",
    "PINNED_COMMIT",
    "PINNED_TESTCASE",
    "PROVIDES",
    "BopTestRestAdapter",
    "BopTestRestError",
]
