"""Match reviewed responsibility capability contracts to backend manifests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


DIMENSIONS = ("observations", "actions", "dynamics", "events", "evaluators", "spatial", "temporal")
MATCH_STATUSES = frozenset({"FULL", "PARTIAL", "UNSUPPORTED"})


@dataclass(frozen=True)
class CapabilityVector:
    observations: frozenset[str] = frozenset()
    actions: frozenset[str] = frozenset()
    dynamics: frozenset[str] = frozenset()
    events: frozenset[str] = frozenset()
    evaluators: frozenset[str] = frozenset()
    spatial: frozenset[str] = frozenset()
    temporal: frozenset[str] = frozenset()

    def as_dict(self) -> dict[str, list[str]]:
        return {name: sorted(getattr(self, name)) for name in DIMENSIONS}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "CapabilityVector":
        return cls(**{name: frozenset(value.get(name, ())) for name in DIMENSIONS})

    def missing_from(self, provided: "CapabilityVector") -> dict[str, list[str]]:
        return {
            name: sorted(getattr(self, name) - getattr(provided, name))
            for name in DIMENSIONS
            if getattr(self, name) - getattr(provided, name)
        }


@dataclass(frozen=True)
class BackendProfile:
    backend_id: str
    domains: frozenset[str]
    capabilities: CapabilityVector
    verification_status: str
    harness_v2_verified: bool
    evidence_refs: tuple[str, ...]
    process_count_source: tuple[str, str] | None = None


@dataclass(frozen=True)
class ResponsibilityRequirement:
    responsibility_id: str
    domain: str | None
    capabilities: CapabilityVector
    episode_inputs: tuple[str, ...]
    contract_status: str
    source_refs: tuple[str, ...]


def _vector(**values: Iterable[str]) -> CapabilityVector:
    return CapabilityVector(**{name: frozenset(values.get(name, ())) for name in DIMENSIONS})


def default_backend_profiles() -> tuple[BackendProfile, ...]:
    """Only locally present or probed backend implementations."""

    return (
        BackendProfile(
            backend_id="simuhome_thermal_harness_v2",
            domains=frozenset({"thermal_temperature"}),
            capabilities=_vector(
                observations=("clock", "room.temperature"),
                actions=("thermal.setpoint", "rule.install", "rule.cancel", "wait"),
                dynamics=("thermal.multiroom",),
                events=("clock.window", "rule.lifecycle"),
                evaluators=("temperature.trajectory", "command.ack", "trajectory.complete"),
                spatial=("room.addressable", "multiroom.addressable"),
                temporal=("closed_loop", "scheduled.window"),
            ),
            verification_status="HARNESS_V2_REVIEWED",
            harness_v2_verified=True,
            evidence_refs=(
                "project://harness_v2/simuhome_adapter.py",
                "project://HARNESS_V2_SIMUHOME_REVIEW.md",
                "project://tests/test_harness_v2_simuhome_adapter.py",
            ),
        ),
        BackendProfile(
            backend_id="energyplus_residential_thermal_legacy",
            domains=frozenset({"thermal_temperature"}),
            capabilities=_vector(
                observations=("clock", "room.temperature", "outdoor.temperature", "occupancy"),
                actions=("thermal.setpoint", "wait"),
                dynamics=("thermal.single_zone", "weather"),
                events=("clock.window", "occupancy.change"),
                evaluators=("temperature.trajectory", "energy.integral", "trajectory.complete"),
                spatial=("single_zone.addressable",), temporal=("closed_loop",),
            ),
            verification_status="LEGACY_REPLAY_VERIFIED_NOT_HARNESS_V2",
            harness_v2_verified=False,
            evidence_refs=(
                "project://generated/energyplus_responsibility_release_v1/build_report.json",
                "project://generated/energyplus_responsibility_release_v1/replay_gate.json",
            ),
            process_count_source=("project://generated/energyplus_responsibility_release_v1/replay_gate.json", "unique_physical_process_id"),
        ),
        BackendProfile(
            backend_id="simuhome_lighting_raw",
            domains=frozenset({"lighting"}),
            capabilities=_vector(
                observations=("clock", "room.illuminance", "device.light_state"), actions=("light.on_off", "light.level"),
                dynamics=("lighting.state_machine",), events=("clock.window",), evaluators=("illuminance.trajectory", "command.ack"),
                spatial=("room.addressable", "multiroom.addressable"), temporal=("closed_loop", "scheduled.window"),
            ),
            verification_status="SIMULATOR_CODE_PRESENT_NO_HARNESS_V2_ADAPTER",
            harness_v2_verified=False,
            evidence_refs=(
                "workspace://external/SimuHome/src/simulator/domain/aggregators/illuminance.py",
                "workspace://external/SimuHome/src/simulator/application/device_factory.py",
            ),
        ),
        BackendProfile(
            backend_id="simuhome_humidity_raw",
            domains=frozenset({"humidity_air_quality"}),
            capabilities=_vector(
                observations=("clock", "room.humidity_or_air_quality", "device.air_control_state"), actions=("air_quality.control",),
                dynamics=("air_quality.multiroom",), events=("clock.window",), evaluators=("air_quality.trajectory", "command.ack"),
                spatial=("room.addressable", "multiroom.addressable"), temporal=("closed_loop", "scheduled.window"),
            ),
            verification_status="SIMULATOR_CODE_PRESENT_NO_HARNESS_V2_ADAPTER",
            harness_v2_verified=False,
            evidence_refs=("workspace://external/SimuHome/src/simulator/domain/aggregators/humidity.py",),
        ),
        BackendProfile(
            backend_id="citylearn_battery_pv_v1",
            domains=frozenset({"energy"}),
            capabilities=_vector(
                observations=("clock", "battery.soc", "household.load", "pv.generation", "grid.net_energy"),
                actions=("battery.charge_discharge", "wait"), dynamics=("battery.storage", "household.load_trace", "pv.trace"),
                events=("clock.window",), evaluators=("battery.soc_invariant", "grid.import_integral", "grid.export_integral", "trajectory.complete"),
                spatial=("house.addressable",), temporal=("closed_loop",),
            ),
            verification_status="REPLAY_VERIFIED_NOT_HARNESS_V2",
            harness_v2_verified=False,
            evidence_refs=(
                "project://generated/battery_pv_process_replay_gate.json",
                "project://generated/battery_pv_release/build_report.json",
            ),
            process_count_source=("project://generated/battery_pv_release/build_report.json", "process_count"),
        ),
    )


def compile_requirement(row: Mapping[str, Any], contract: Mapping[str, Any]) -> ResponsibilityRequirement:
    """Join identity to a frozen structured contract; never parse language."""

    if row["responsibility_id"] != contract["responsibility_id"]:
        raise ValueError("responsibility/contract identity mismatch")
    return ResponsibilityRequirement(
        responsibility_id=str(row["responsibility_id"]),
        domain=contract.get("domain"),
        capabilities=CapabilityVector.from_dict(contract.get("capabilities", {})),
        episode_inputs=tuple(contract.get("episode_inputs", ())),
        contract_status=str(contract["contract_status"]),
        source_refs=tuple(contract.get("source_refs", ())),
    )


def _has_core_overlap(requirement: CapabilityVector, backend: CapabilityVector) -> bool:
    """PARTIAL means a backend covers each causal core, not merely a domain."""

    substantive_observations = requirement.observations - {"clock"}
    substantive_actions = requirement.actions - {"wait", "rule.install", "rule.cancel"}
    return all((
        bool(substantive_observations & backend.observations),
        bool(substantive_actions & backend.actions),
        bool(requirement.dynamics & backend.dynamics),
        bool(requirement.evaluators & backend.evaluators),
    ))


def match_requirement(requirement: ResponsibilityRequirement, backends: Iterable[BackendProfile]) -> dict[str, Any]:
    if requirement.domain is None or requirement.contract_status == "MISSING_OPERATIONAL_CONTRACT":
        return {"status": "UNSUPPORTED", "selected_backend": None, "backend_assessments": [], "reason_code": "MISSING_OPERATIONAL_CONTRACT"}

    assessments = []
    for backend in sorted((item for item in backends if requirement.domain in item.domains), key=lambda item: item.backend_id):
        missing = requirement.capabilities.missing_from(backend.capabilities)
        core_overlap = _has_core_overlap(requirement.capabilities, backend.capabilities)
        readiness_gaps = []
        if requirement.contract_status != "REVIEWED":
            readiness_gaps.append("reviewed_responsibility_contract")
        if not backend.harness_v2_verified:
            readiness_gaps.append("harness_v2_verified_backend")
        assessments.append({
            "backend_id": backend.backend_id,
            "verification_status": backend.verification_status,
            "harness_v2_verified": backend.harness_v2_verified,
            "core_overlap": core_overlap,
            "missing": missing,
            "readiness_gaps": readiness_gaps,
            "evidence_refs": list(backend.evidence_refs),
        })

    full = [item for item in assessments if item["core_overlap"] and not item["missing"] and not item["readiness_gaps"]]
    partial = [item for item in assessments if item["core_overlap"]]
    if full:
        return {"status": "FULL", "selected_backend": full[0]["backend_id"], "backend_assessments": assessments, "reason_code": "EXACT_REVIEWED_MATCH"}
    if partial:
        selected = min(partial, key=lambda item: (
            sum(len(values) for values in item["missing"].values()) + len(item["readiness_gaps"]), item["backend_id"]
        ))
        return {"status": "PARTIAL", "selected_backend": selected["backend_id"], "backend_assessments": assessments, "reason_code": "CAUSAL_CORE_OVERLAP_WITH_GAPS"}
    return {"status": "UNSUPPORTED", "selected_backend": None, "backend_assessments": assessments, "reason_code": "NO_CAUSAL_CORE_OVERLAP"}
