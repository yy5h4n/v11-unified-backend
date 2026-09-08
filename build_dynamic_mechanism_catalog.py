#!/usr/bin/env python3
"""Build an independent, fail-closed D0--D3 mechanism index.

This index deliberately lives outside the existing claim/catalog surfaces. It
only aggregates already-produced replay gates and evidence; it never upgrades
a mechanism based on a name or on a partial probe.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from unified_compiler.adapters.d1_fault_mechanism import D1_PROFILE_NAMES

ROOT = Path(__file__).resolve().parent
GENERATED = ROOT / "generated"
OUTPUT = GENERATED / "dynamic_mechanism_catalog_v1.json"


class DynamicCatalogError(RuntimeError):
    """Raised when required evidence cannot be proven current and complete."""


def sha256(path: Path) -> str:
    if not path.is_file():
        raise DynamicCatalogError(f"missing evidence file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise DynamicCatalogError(f"missing evidence file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DynamicCatalogError(f"invalid evidence JSON: {path}") from exc
    if not isinstance(value, dict):
        raise DynamicCatalogError(f"evidence is not an object: {path}")
    return value


def relative(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT.resolve()))
    except ValueError:
        # Some pinned source artifacts live beside this prototype (for
        # example, the v5/v10 source caches).  Keep those references
        # relative, while never pretending they are generated evidence.
        return os.path.relpath(path.resolve(), ROOT.resolve())


def evidence_ref(path: Path, **extra: Any) -> dict[str, Any]:
    return {"path": relative(path), "sha256": sha256(path), **extra}


def canonical_digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


BACKEND_REPLAY_VERIFIED = "BACKEND_REPLAY_VERIFIED"
EVIDENCE_PENDING = "EVIDENCE_PENDING"


def _interactive_evidence(report: Mapping[str, Any], external: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Read, but never infer, online-step evidence from a backend gate.

    The historical D2 gates certify whole-trajectory replay.  A route is
    interactive only when its current probe explicitly emits the dedicated
    boolean; a descriptive ``agent_closed_loop`` block alone is not evidence.
    """
    source = external if external is not None else report
    metadata = source.get("agent_closed_loop")
    if not isinstance(metadata, Mapping):
        metadata = {}
    interface = metadata.get("interface", [])
    if not isinstance(interface, list):
        interface = []
    verified = source.get("interactive_step_verified") is True
    conformance = source.get("interactive_conformance_gate") is True
    execution_mode = source.get("interactive_execution_mode", metadata.get("runtime_mechanism"))
    return {
        "interactive_step_verified": verified,
        "interactive_conformance_gate": conformance,
        "execution_mode": execution_mode,
        "interface": list(interface),
        "mid_trajectory_action_switch_verified": source.get("mid_trajectory_action_switch_verified") is True,
        "deterministic_reset_verified": source.get("interactive_deterministic_reset") is True,
        "time_monotone_verified": source.get("interactive_time_monotone") is True,
        "state_continuity_verified": source.get("interactive_state_continuity") is True,
        "illegal_action_fail_closed_verified": source.get("interactive_illegal_action_fail_closed") is True,
        "evidence_ref": source.get("evidence_ref"),
    }


def status(evidence_ok: bool = True) -> str:
    """Return the only two statuses exposed by the backend index."""
    return BACKEND_REPLAY_VERIFIED if evidence_ok else EVIDENCE_PENDING


def _bool(value: Any, field: str, *, allow_string_pass: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if allow_string_pass and value == "PASS":
        return True
    raise DynamicCatalogError(f"evidence field {field} is not boolean")


def _digest(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value.lower()):
        raise DynamicCatalogError(f"evidence field {field} is not a sha256 digest")
    return value.lower()


def _verify_file_hash(path: Path, supplied: Any, field: str) -> str:
    expected = _digest(supplied, field)
    actual = sha256(path)
    if expected != actual:
        raise DynamicCatalogError(f"stale {field}: {path}")
    return expected


def _resolve_ref(raw: Any, field: str) -> Path:
    if not isinstance(raw, str) or not raw or Path(raw).is_absolute():
        raise DynamicCatalogError(f"evidence field {field} is not a relative path")
    candidates = (ROOT / raw, ROOT.parent / raw)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise DynamicCatalogError(f"missing evidence file: {raw}")


def _require_schema(report: Mapping[str, Any], expected: str, field: str) -> None:
    if report.get("schema_version") != expected:
        raise DynamicCatalogError(f"{field} evidence schema is not the pinned backend probe version")


AGENT_INTERFACE_PATH = GENERATED / "agent_interface_d0_d1_v1.json"
AGENT_ROUTE_IDS = (
    "d0_exogenous_context",
    "d1_sustaingym_fault",
    "d1_citylearn_battery_fault",
    "d1_ev2gym_fault",
    "d1_discrete_device_fault",
)
AGENT_CHECKS = (
    "initial_receipt",
    "time_monotone",
    "observe_is_latest_transition",
    "mid_trajectory_action_accepted",
    "illegal_action_fail_closed",
)

D3_AGENT_INTERFACE_PATH = GENERATED / "d3_agent_interface_v1.json"
D3_AGENT_CHECKS = (
    "initial_receipt",
    "legal_actions",
    "deterministic_reset",
    "time_monotone",
    "observe_is_latest",
    "same_prefix_mid_trajectory_switch",
    "hvac_counterfactual_thermal_and_net",
    "battery_counterfactual_soc_and_net",
    "illegal_action_no_advance",
    "invalid_dt_no_advance",
    "same_native_env_instance",
    "termination",
)


def agent_interface_evidence() -> dict[str, Any]:
    """Validate online Agent conformance independently from replay gates."""
    report = load_json(AGENT_INTERFACE_PATH)
    _require_schema(report, "agent-interface-d0-d1-v1", "Agent interface")
    if report.get("verified") is not True:
        raise DynamicCatalogError("Agent interface evidence is not verified")
    probe_path = _resolve_ref(report.get("probe_path"), "Agent interface.probe_path")
    _verify_file_hash(probe_path, report.get("probe_sha256"), "Agent interface.probe_sha256")
    common_path = _resolve_ref(report.get("common_adapter_path"), "Agent interface.common_adapter_path")
    _verify_file_hash(common_path, report.get("common_adapter_sha256"), "Agent interface.common_adapter_sha256")
    routes = report.get("routes")
    paths = report.get("route_adapter_paths")
    hashes = report.get("route_adapter_sha256")
    if not isinstance(routes, Mapping) or set(routes) != set(AGENT_ROUTE_IDS) or not isinstance(paths, Mapping) or not isinstance(hashes, Mapping):
        raise DynamicCatalogError("Agent interface route evidence is incomplete")
    refs = [evidence_ref(AGENT_INTERFACE_PATH, role="agent_interface_conformance"), evidence_ref(probe_path, role="agent_interface_probe"), evidence_ref(common_path, role="agent_interface_common_adapter")]
    validated: dict[str, Any] = {}
    for route_id in AGENT_ROUTE_IDS:
        case = routes.get(route_id)
        if not isinstance(case, Mapping) or case.get("verified") is not True:
            raise DynamicCatalogError(f"Agent interface route is not verified: {route_id}")
        checks = case.get("checks")
        if not isinstance(checks, Mapping) or set(AGENT_CHECKS) - set(checks):
            raise DynamicCatalogError(f"Agent interface checks are incomplete: {route_id}")
        normalized_checks = {key: _bool(checks.get(key), f"Agent interface.{route_id}.{key}") for key in AGENT_CHECKS}
        if not all(normalized_checks.values()):
            raise DynamicCatalogError(f"Agent interface checks failed: {route_id}")
        adapter_path = _resolve_ref(paths.get(route_id), f"Agent interface.{route_id}.adapter_path")
        _verify_file_hash(adapter_path, hashes.get(route_id), f"Agent interface.{route_id}.adapter_sha256")
        refs.append(evidence_ref(adapter_path, role=f"{route_id}_agent_backend_adapter"))
        validated[route_id] = {"verified": True, "checks": normalized_checks, "tick_seconds": case.get("tick_seconds"), "evidence": str(AGENT_INTERFACE_PATH.relative_to(ROOT))}
    return {"verified": True, "status": BACKEND_REPLAY_VERIFIED, "routes": validated, "provenance_refs": refs}


def d3_agent_interface_evidence() -> dict[str, Any]:
    """Validate D3 online conformance from its dedicated live probe.

    This evidence is intentionally independent of the historical D3 replay
    report.  A replay pass cannot manufacture an Agent interface verdict.
    """
    report = load_json(D3_AGENT_INTERFACE_PATH)
    _require_schema(report, "d3-agent-interface-v1", "D3 Agent interface")
    if report.get("route_id") != "d3_citylearn_coupling":
        raise DynamicCatalogError("D3 Agent interface route identity is malformed")
    verified = _bool(report.get("verified"), "D3 Agent interface.verified")
    passed = _bool(report.get("passed"), "D3 Agent interface.passed")
    checks = report.get("checks")
    if not isinstance(checks, Mapping) or set(D3_AGENT_CHECKS) - set(checks):
        raise DynamicCatalogError("D3 Agent interface checks are incomplete")
    normalized = {key: _bool(checks.get(key), f"D3 Agent interface.{key}") for key in D3_AGENT_CHECKS}
    # A well-formed but failing live probe is represented as pending.  This
    # keeps the Agent verdict independent from the historical replay verdict
    # while preserving the catalog's fail-closed status vocabulary.
    interface_verified = verified and passed and all(normalized.values())
    probe_path = _resolve_ref(report.get("probe_path"), "D3 Agent interface.probe_path")
    route_path = _resolve_ref(report.get("route_path"), "D3 Agent interface.route_path")
    interface_path = _resolve_ref(report.get("public_interface_path"), "D3 Agent interface.public_interface_path")
    probe_hash = _verify_file_hash(probe_path, report.get("probe_sha256"), "D3 Agent interface.probe_sha256")
    route_hash = _verify_file_hash(route_path, report.get("route_sha256"), "D3 Agent interface.route_sha256")
    interface_hash = _verify_file_hash(interface_path, report.get("public_interface_sha256"), "D3 Agent interface.public_interface_sha256")
    refs = [
        evidence_ref(D3_AGENT_INTERFACE_PATH, role="d3_agent_interface_conformance"),
        evidence_ref(probe_path, role="d3_agent_interface_probe"),
        evidence_ref(route_path, role="d3_agent_route"),
        evidence_ref(interface_path, role="d3_public_agent_interface"),
    ]
    return {
        "verified": interface_verified,
        "status": status(interface_verified),
        "route_id": report["route_id"],
        "checks": normalized,
        "tick_seconds": report.get("agent_closed_loop", {}).get("dt_seconds"),
        "evidence": str(D3_AGENT_INTERFACE_PATH.relative_to(ROOT)),
        "probe_sha256": probe_hash,
        "route_sha256": route_hash,
        "public_interface_sha256": interface_hash,
        "provenance_refs": refs,
    }


def d0_entry() -> dict[str, Any]:
    path = GENERATED / "d0_exogenous_context_v1" / "gate_report.json"
    report = load_json(path)
    if report.get("schema_version") != "d0-exogenous-context-replay-gate-v1":
        raise DynamicCatalogError("D0 evidence schema is not the pinned backend probe version")
    required = ("runtime_stepping", "deterministic_replay", "action_sensitive", "external_event_sensitive", "provenance_complete")
    checks = {key: _bool(report.get(key), f"D0.{key}") for key in required}
    verified = _bool(report.get("verified"), "D0.verified")
    horizon_steps = report.get("horizon_steps")
    event_count = report.get("event_count")
    schedule_id = report.get("schedule_id")
    if not isinstance(horizon_steps, int) or horizon_steps < 1 or not isinstance(event_count, int) or event_count < 1:
        raise DynamicCatalogError("D0 horizon or event count is missing or malformed")
    if not isinstance(schedule_id, str) or len(schedule_id) != 64:
        raise DynamicCatalogError("D0 schedule_id is missing or malformed")
    runtime_gate = verified and all(checks.values())
    adapter_path = ROOT / "unified_compiler" / "adapters" / "d0_exogenous_context.py"
    if report.get("adapter_sha256") != sha256(adapter_path):
        raise DynamicCatalogError("D0 adapter provenance is stale")
    probe_path = ROOT / "probe_d0_exogenous_context.py"
    if report.get("probe_sha256") != sha256(probe_path):
        raise DynamicCatalogError("D0 probe provenance is stale")
    agent = agent_interface_evidence()
    d0_agent = agent["routes"]["d0_exogenous_context"]
    return {
        "mechanism_id": "D0_exogenous_context_events",
        "mechanism_status": status(runtime_gate),
        "backend": {"name": report.get("backend"), "route": report.get("route")},
        "runtime": {
            "adapter_sha256": report.get("adapter_sha256"),
            "probe_sha256": report.get("probe_sha256"),
            "horizon_steps": horizon_steps,
            "tick_seconds": 60,
            "schedule_id": schedule_id,
        },
        "actions": {
            "agent_action_effect": checks["action_sensitive"],
            "action_surface": "context_device_command",
            "schedule_agent_independent": True,
        },
        "observations": {
            "context_state": ["occupancy_status", "occupancy_count", "weather", "time_context"],
            "device_state": ["front_door", "interior_lights"],
            "external_events": True,
        },
        "mechanism": {"kind": "exogenous_event_and_context_transition", "process": "external schedule applied during backend stepping"},
        "replay": {
            "verified": runtime_gate,
            "deterministic": checks["deterministic_replay"],
            "action_sensitive": checks["action_sensitive"],
            "external_event_sensitive": checks["external_event_sensitive"],
            "runtime_stepping": checks["runtime_stepping"],
            "event_count": event_count,
        },
        "agent_interface_verified": d0_agent["verified"],
        "agent_interface_status": agent["status"],
        "agent_interface": d0_agent,
        "provenance_refs": [
            evidence_ref(path, role="d0_backend_replay_gate"),
            evidence_ref(adapter_path, role="d0_backend_adapter"),
            evidence_ref(probe_path, role="d0_backend_probe"),
            *[ref for ref in agent["provenance_refs"] if ref["path"] != relative(AGENT_INTERFACE_PATH)],
        ],
        "boundary": {
            "evidence_basis": "explicit external-event/context backend replay gate",
            "event_schedule_is_exogenous": True,
            "backend_only": True,
            "does_not_claim": ["physical_dynamics", "device_failure", "weather_or_grid_physics", "notification_success", "authorization", "benchmark readiness"],
        },
    }


def d1_entry() -> dict[str, Any]:
    """Build four independent routes; a route never inherits another route's verdict."""

    routes: list[dict[str, Any]] = []
    agent = agent_interface_evidence()

    # HVAC / SustainGym route.  ``base_probe_sha256`` is retained as opaque
    # runtime provenance because this legacy gate does not provide a path for
    # that digest.  The adapter digest *does* have a local, unambiguous path.
    hvac_path = GENERATED / "d1_fault_replay_gate_v1.json"
    hvac = load_json(hvac_path)
    _require_schema(hvac, "d1-fault-replay-gate-v1", "D1 HVAC")
    hvac_verified = _bool(hvac.get("verified"), "D1.HVAC.verified")
    hvac_checks = {
        key: _bool(hvac.get(key), f"D1.HVAC.{key}")
        for key in ("backend_importable", "all_replays_completed", "same_reset_same_window_replay", "action_sensitive", "fault_changes_future_state", "fault_changes_feasible_strategy")
    }
    witness = hvac.get("witness")
    if not isinstance(witness, Mapping):
        raise DynamicCatalogError("D1 HVAC witness evidence is missing")
    hvac_adapter = ROOT / "unified_compiler" / "adapters" / "d1_fault_mechanism.py"
    _verify_file_hash(hvac_adapter, hvac.get("adapter_sha256"), "D1.HVAC.adapter_sha256")
    _digest(hvac.get("base_probe_sha256"), "D1.HVAC.base_probe_sha256")
    for key in ("fault_trace_sha256", "healthy_trace_sha256", "same_schedule_action_contrast_trace_sha256"):
        _digest(hvac.get(key), f"D1.HVAC.{key}")
    hvac_probe_gate_path = GENERATED / "sustaingym_replay_gate_v1.json"
    hvac_probe_gate = load_json(hvac_probe_gate_path)
    _require_schema(hvac_probe_gate, "sustaingym-replay-gate-v1", "D1 HVAC base probe")
    if hvac_probe_gate.get("probe_result_sha256") != hvac.get("base_probe_sha256"):
        raise DynamicCatalogError("D1 HVAC base probe result hash is stale")
    _digest(hvac_probe_gate.get("probe_script_sha256"), "D1.HVAC.probe_script_sha256")
    hvac_gate = hvac_verified and all(hvac_checks.values()) and hvac_probe_gate.get("verified") is True
    runtime_modes = hvac.get("mechanism_library", {}).get("runtime_replay_verified_modes", [])
    if not isinstance(runtime_modes, list) or sorted(runtime_modes) != sorted(D1_PROFILE_NAMES):
        raise DynamicCatalogError("D1 HVAC runtime replay mode list is incomplete")
    profile_manifest_path = GENERATED / "d1_fault_profiles_v1" / "manifest.json"
    profile_manifest = load_json(profile_manifest_path)
    _require_schema(profile_manifest, "d1-fault-profiles-manifest-v1", "D1 HVAC profile manifest")
    if profile_manifest.get("all_verified") is not True:
        raise DynamicCatalogError("D1 HVAC profile manifest is not fully verified")
    profile_refs = [evidence_ref(profile_manifest_path, role="hvac_fault_profile_manifest")]
    profile_entries = profile_manifest.get("profiles")
    if not isinstance(profile_entries, Mapping):
        raise DynamicCatalogError("D1 HVAC profile manifest entries are missing")
    for mode in D1_PROFILE_NAMES:
        entry = profile_entries.get(mode)
        if not isinstance(entry, Mapping) or entry.get("verified") is not True:
            raise DynamicCatalogError(f"D1 HVAC profile is not verified: {mode}")
        profile_path = _resolve_ref(entry.get("path"), f"D1.HVAC.profile.{mode}.path")
        profile = load_json(profile_path)
        _require_schema(profile, "d1-fault-profile-gate-v1", f"D1 HVAC profile {mode}")
        if profile.get("profile") != mode or profile.get("verified") is not True:
            raise DynamicCatalogError(f"D1 HVAC profile identity/verdict mismatch: {mode}")
        _verify_file_hash(hvac_adapter, profile.get("adapter_sha256"), f"D1.HVAC.profile.{mode}.adapter_sha256")
        _digest(profile.get("fault_trajectory_sha256"), f"D1.HVAC.profile.{mode}.fault_trajectory_sha256")
        if entry.get("trajectory_sha256") != profile.get("fault_trajectory_sha256"):
            raise DynamicCatalogError(f"D1 HVAC profile trajectory hash mismatch: {mode}")
        profile_refs.append(evidence_ref(profile_path, role=f"hvac_fault_profile_{mode}"))
    hvac_modes = list(D1_PROFILE_NAMES)
    routes.append({
        "route_id": "hvac",
        "device_family": "hvac",
        "mechanism_status": status(hvac_gate),
        "backend": {"name": hvac.get("backend"), "building": hvac.get("backend_building"), "weather": hvac.get("backend_weather")},
        "runtime": {"backend_commit": hvac.get("backend_commit"), "adapter_sha256": hvac.get("adapter_sha256"), "probe_sha256": hvac.get("base_probe_sha256")},
        "actions": {"fault_schedule": hvac.get("fault_schedule"), "fault_modes": hvac_modes, "same_schedule_contrast": hvac.get("same_schedule_action_contrast_trace_sha256")},
        "observations": {"fault_observable_after_onset": bool(witness.get("fault_observable_after_onset")), "sensor_fault_scope": "public_observation_only"},
        "mechanism": {"kind": "fault_aware_hvac_control", "process": "HVAC control under actuator faults and public-sensor faults"},
        "replay": {"verified": hvac_gate, **hvac_checks, "runtime_replay_verified_modes": hvac.get("mechanism_library", {}).get("runtime_replay_verified_modes", []), "unit_tested_modes": hvac.get("mechanism_library", {}).get("unit_tested_modes", [])},
        "agent_interface_verified": agent["routes"]["d1_sustaingym_fault"]["verified"],
        "agent_interface_status": agent["status"],
        "agent_interface": agent["routes"]["d1_sustaingym_fault"],
        "provenance_refs": [evidence_ref(hvac_path, role="hvac_fault_replay_gate"), evidence_ref(hvac_probe_gate_path, role="hvac_base_probe_gate"), evidence_ref(hvac_adapter, role="hvac_backend_adapter"), *profile_refs],
        "boundary": {"evidence_basis": "SustainGym BuildingEnv fault replay gate", "does_not_claim": ["repair_diagnostics", "real_device_failure_rate", "notification_success", "production readiness"]},
    })

    # CityLearn battery route.  The manifest binds exactly four profile files;
    # each profile must independently pass every causal check.
    battery_dir = GENERATED / "d1_citylearn_battery_fault_profiles_v1"
    battery_manifest_path = battery_dir / "manifest.json"
    battery_manifest = load_json(battery_manifest_path)
    _require_schema(battery_manifest, "d1-citylearn-battery-fault-profiles-v1", "D1 battery manifest")
    manifest_verified = _bool(battery_manifest.get("all_verified"), "D1.battery.manifest.all_verified")
    manifest_backend_only = _bool(battery_manifest.get("backend_only"), "D1.battery.manifest.backend_only")
    if battery_manifest.get("backend_version") != "2.5.0":
        raise DynamicCatalogError("D1 battery manifest backend version is not pinned")
    profile_refs = battery_manifest.get("profiles")
    if not isinstance(profile_refs, Mapping) or not profile_refs:
        raise DynamicCatalogError("D1 battery profile manifest is missing profiles")
    battery_profiles: dict[str, dict[str, Any]] = {}
    battery_refs = [evidence_ref(battery_manifest_path, role="battery_profile_manifest")]
    battery_adapter = ROOT / "unified_compiler" / "adapters" / "citylearn_battery_fault.py"
    for profile_name, manifest_ref in sorted(profile_refs.items()):
        if not isinstance(profile_name, str) or not isinstance(manifest_ref, Mapping):
            raise DynamicCatalogError("D1 battery profile manifest entry is malformed")
        profile_path = _resolve_ref(manifest_ref.get("path"), f"D1.battery.{profile_name}.path")
        manifest_profile_verified = _bool(manifest_ref.get("verified"), f"D1.battery.{profile_name}.manifest.verified")
        _digest(manifest_ref.get("trajectory_sha256"), f"D1.battery.{profile_name}.manifest.trajectory_sha256")
        profile = load_json(profile_path)
        _require_schema(profile, "d1-citylearn-battery-fault-replay-gate-v1", f"D1 battery {profile_name}")
        if profile.get("profile") != profile_name:
            raise DynamicCatalogError(f"D1 battery profile name mismatch: {profile_name}")
        profile_verified = _bool(profile.get("verified"), f"D1.battery.{profile_name}.verified")
        checks = profile.get("checks")
        if not isinstance(checks, Mapping) or not checks:
            raise DynamicCatalogError(f"D1 battery checks missing: {profile_name}")
        profile_checks = {key: _bool(value, f"D1.battery.{profile_name}.checks.{key}") for key, value in checks.items()}
        if any(profile.get(key) is not True for key in ("deterministic_replay", "healthy_fault_counterfactual", "soc_divergence", "net_electricity_divergence", "provenance_complete", "all_replays_completed", "backend_importable", "effective_action_contrast")):
            profile_checks["required_top_level_checks"] = False
        else:
            profile_checks["required_top_level_checks"] = True
        _verify_file_hash(battery_adapter, profile.get("adapter_sha256"), f"D1.battery.{profile_name}.adapter_sha256")
        _digest(profile.get("fault_trajectory_sha256"), f"D1.battery.{profile_name}.fault_trajectory_sha256")
        if manifest_ref.get("trajectory_sha256") != profile.get("fault_trajectory_sha256"):
            raise DynamicCatalogError(f"D1 battery manifest trajectory hash mismatch: {profile_name}")
        provenance = profile.get("provenance")
        if not isinstance(provenance, Mapping):
            raise DynamicCatalogError(f"D1 battery provenance missing: {profile_name}")
        for key in ("asset_manifest_sha256", "battery_catalog_sha256", "pv_catalog_sha256", "source_trace_sha256", "runtime_sha256"):
            _digest(provenance.get(key), f"D1.battery.{profile_name}.provenance.{key}")
        source_path = _resolve_ref(provenance.get("source_trace"), f"D1.battery.{profile_name}.provenance.source_trace")
        _verify_file_hash(source_path, provenance.get("source_trace_sha256"), f"D1.battery.{profile_name}.source_trace_sha256")
        for raw, digest_value, label in (("shared_assets/citylearn_v2.5.0/asset_manifest.json", provenance.get("asset_manifest_sha256"), "asset_manifest_sha256"), ("shared_assets/citylearn_v2.5.0/misc/battery_choices.yaml", provenance.get("battery_catalog_sha256"), "battery_catalog_sha256"), ("shared_assets/citylearn_v2.5.0/misc/lbl-tracking_the_sun-res-pv.csv", provenance.get("pv_catalog_sha256"), "pv_catalog_sha256")):
            _verify_file_hash(_resolve_ref(raw, f"D1.battery.{profile_name}.{label}.path"), digest_value, f"D1.battery.{profile_name}.{label}")
        profile_gate = profile_verified and all(profile_checks.values()) and manifest_profile_verified and manifest_verified and manifest_backend_only
        battery_profiles[profile_name] = {"verified": profile_gate, "checks": profile_checks, "schedule": profile.get("fault_schedule"), "trajectory_sha256": profile.get("fault_trajectory_sha256"), "source_window": profile.get("source_window")}
        battery_refs.append(evidence_ref(profile_path, role=f"battery_profile_{profile_name}"))
    battery_refs.append(evidence_ref(battery_adapter, role="battery_backend_adapter"))
    # Add the pinned source/asset refs once; their hashes were checked above.
    for raw in ("shared_assets/citylearn_v2.5.0/asset_manifest.json", "shared_assets/citylearn_v2.5.0/misc/battery_choices.yaml", "shared_assets/citylearn_v2.5.0/misc/lbl-tracking_the_sun-res-pv.csv"):
        battery_refs.append(evidence_ref(_resolve_ref(raw, "D1.battery.asset"), role="battery_source_asset"))
    battery_modes = sorted(battery_profiles)
    battery_gate = all(item["verified"] for item in battery_profiles.values())
    routes.append({
        "route_id": "citylearn_battery",
        "device_family": "battery",
        "mechanism_status": status(battery_gate),
        "backend": {"name": "CityLearn", "version": battery_manifest.get("backend_version"), "building": "resstock-amy2018-2021-release-1-102040"},
        "runtime": {"adapter_sha256": sha256(battery_adapter), "backend_version": battery_manifest.get("backend_version"), "profile_count": len(battery_profiles)},
        "actions": {"fault_modes": battery_modes, "profile_schedules": {name: item["schedule"] for name, item in battery_profiles.items()}},
        "observations": {"signals": ["battery_soc", "net_electricity", "effective_action"]},
        "mechanism": {"kind": "battery_health_fault", "process": "CityLearn battery command and state under health faults"},
        "replay": {"verified": battery_gate, "profiles": battery_profiles},
        "agent_interface_verified": agent["routes"]["d1_citylearn_battery_fault"]["verified"],
        "agent_interface_status": agent["status"],
        "agent_interface": agent["routes"]["d1_citylearn_battery_fault"],
        "provenance_refs": battery_refs,
        "boundary": {"evidence_basis": "four independent CityLearn 2.5.0 battery profile replay gates", "backend_only": True, "does_not_claim": ["responsibility_alignment", "evaluator_validity", "notification_success", "multi_device_physics"]},
    })

    # EV2Gym charger route.  Native artifact digests are checked against the
    # exact frozen v10 files; no probe digest is invented because this gate
    # does not expose one.
    ev_path = GENERATED / "ev2gym_fault_replay_gate_v1.json"
    ev = load_json(ev_path)
    _require_schema(ev, "ev2gym-d1-fault-replay-gate-v1", "D1 EV2Gym")
    ev_verified = _bool(ev.get("verified"), "D1.EV2Gym.verified")
    ev_adapter = ROOT / "unified_compiler" / "adapters" / "ev2gym_fault.py"
    _verify_file_hash(ev_adapter, ev.get("adapter_sha256"), "D1.EV2Gym.adapter_sha256")
    if ev.get("adapter_id") != "unified_compiler.adapters.ev2gym_fault.v1" or ev.get("base_adapter_id") != "unified_compiler.adapters.ev2gym_claim.v1":
        raise DynamicCatalogError("D1 EV2Gym adapter binding is not pinned")
    deterministic = ev.get("deterministic_replay")
    if not isinstance(deterministic, Mapping) or not deterministic:
        raise DynamicCatalogError("D1 EV2Gym deterministic replay checks are missing")
    ev_checks = {mode: _bool(value, f"D1.EV2Gym.deterministic_replay.{mode}") for mode, value in deterministic.items()}
    counterfactual = ev.get("healthy_vs_fault_counterfactual")
    profiles = ev.get("profiles")
    if not isinstance(counterfactual, Mapping) or not isinstance(profiles, Mapping) or set(counterfactual) != set(profiles):
        raise DynamicCatalogError("D1 EV2Gym profiles/counterfactual evidence is malformed")
    for mode, item in counterfactual.items():
        if not isinstance(item, Mapping):
            raise DynamicCatalogError(f"D1 EV2Gym counterfactual is malformed: {mode}")
        ev_checks[f"{mode}.effective_action_differs"] = _bool(item.get("effective_action_differs"), f"D1.EV2Gym.{mode}.effective_action_differs")
    healthy = ev.get("healthy")
    if not isinstance(healthy, Mapping):
        raise DynamicCatalogError("D1 EV2Gym healthy replay evidence is missing")
    _digest(healthy.get("trajectory_sha256"), "D1.EV2Gym.healthy.trajectory_sha256")
    for mode, profile in profiles.items():
        if not isinstance(profile, Mapping):
            raise DynamicCatalogError(f"D1 EV2Gym profile is malformed: {mode}")
        _digest(profile.get("trajectory_sha256"), f"D1.EV2Gym.{mode}.trajectory_sha256")
    native = ev.get("native_verification")
    if not isinstance(native, Mapping):
        raise DynamicCatalogError("D1 EV2Gym native verification is missing")
    artifact_refs = native.get("artifact_refs")
    artifact_digests = native.get("artifact_digests")
    if not isinstance(artifact_refs, list) or not isinstance(artifact_digests, Mapping):
        raise DynamicCatalogError("D1 EV2Gym native artifact provenance is malformed")
    if native.get("status") != "EXECUTABLE_REPLAY_VERIFIED":
        raise DynamicCatalogError("D1 EV2Gym native verification is not executable")
    ev_refs = [evidence_ref(ev_path, role="ev2gym_fault_replay_gate"), evidence_ref(ev_adapter, role="ev2gym_backend_adapter")]
    for raw in artifact_refs:
        if not isinstance(raw, str):
            raise DynamicCatalogError("D1 EV2Gym artifact path is malformed")
        name = Path(raw).name
        if name not in artifact_digests:
            raise DynamicCatalogError(f"D1 EV2Gym artifact digest missing: {name}")
        artifact_path = _resolve_ref(raw, f"D1.EV2Gym.{name}.path")
        ev_refs.append(evidence_ref(artifact_path, role=f"ev2gym_native_{name.removesuffix('.jsonl')}"))
        _verify_file_hash(artifact_path, artifact_digests[name], f"D1.EV2Gym.native.{name}")
    ev_gate = ev_verified and all(ev_checks.values()) and native.get("status") == "EXECUTABLE_REPLAY_VERIFIED"
    ev_modes = sorted(profiles)
    routes.append({
        "route_id": "ev2gym_charger",
        "device_family": "ev_charger",
        "mechanism_status": status(ev_gate),
        "backend": {"name": ev.get("backend"), "route": ev.get("adapter_id")},
        "runtime": {"adapter_id": ev.get("adapter_id"), "adapter_sha256": ev.get("adapter_sha256"), "probe_process_id": ev.get("probe_process_id"), "probe_horizon_steps": ev.get("probe_horizon_steps")},
        "actions": {"fault_modes": ev_modes, "schedule_ids": ev.get("schedule_ids"), "action_surface": "SET_CHARGE_POWER/WAIT"},
        "observations": {"keys": native.get("probe_observation_keys", [])},
        "mechanism": {"kind": "charger_health_fault", "process": "EV2Gym charging under charger derating, intermittent availability, and outage"},
        "replay": {"verified": ev_gate, "deterministic": ev_checks, "counterfactuals": counterfactual},
        "agent_interface_verified": agent["routes"]["d1_ev2gym_fault"]["verified"],
        "agent_interface_status": agent["status"],
        "agent_interface": agent["routes"]["d1_ev2gym_fault"],
        "provenance_refs": ev_refs,
        "boundary": {"evidence_basis": "EV2Gym native charger fault trajectory replay gate", "backend_only": True, "does_not_claim": ["responsibility_alignment", "benchmark_validity", "query_or_evaluator_behavior"]},
    })

    # Harness V2 discrete route.  Every device/mode case contributes its own
    # verdict; a single positive case cannot certify the family.
    discrete_path = GENERATED / "d1_discrete_device_fault_v1" / "replay_gate.json"
    discrete = load_json(discrete_path)
    _require_schema(discrete, "d1-discrete-device-fault-replay-gate-v1", "D1 discrete")
    discrete_verified = _bool(discrete.get("verified"), "D1.discrete.verified")
    top_checks = discrete.get("checks")
    if not isinstance(top_checks, Mapping) or not top_checks:
        raise DynamicCatalogError("D1 discrete checks are missing")
    discrete_checks = {key: _bool(value, f"D1.discrete.checks.{key}") for key, value in top_checks.items()}
    cases = discrete.get("cases")
    devices = discrete.get("devices")
    modes = discrete.get("fault_modes")
    if not isinstance(cases, Mapping) or not isinstance(devices, list) or not isinstance(modes, list) or not cases:
        raise DynamicCatalogError("D1 discrete cases/devices/fault_modes are malformed")
    for case_id, case in cases.items():
        if not isinstance(case, Mapping) or not isinstance(case.get("checks"), Mapping):
            raise DynamicCatalogError(f"D1 discrete case is malformed: {case_id}")
        for key, value in case["checks"].items():
            discrete_checks[f"{case_id}.{key}"] = _bool(value, f"D1.discrete.cases.{case_id}.checks.{key}")
    provenance = discrete.get("provenance")
    if not isinstance(provenance, Mapping) or _bool(provenance.get("backend_only"), "D1.discrete.provenance.backend_only") is not True:
        raise DynamicCatalogError("D1 discrete backend-only provenance is missing")
    discrete_refs = [evidence_ref(discrete_path, role="discrete_fault_replay_gate")]
    for key, role in (("adapter_path", "discrete_backend_adapter"), ("probe_path", "discrete_backend_probe"), ("workflow_backend_path", "discrete_workflow_backend")):
        source_path = _resolve_ref(provenance.get(key), f"D1.discrete.provenance.{key}")
        hash_key = {"adapter_path": "adapter_sha256", "probe_path": "probe_sha256", "workflow_backend_path": "workflow_backend_sha256"}[key]
        _verify_file_hash(source_path, provenance.get(hash_key), f"D1.discrete.provenance.{hash_key}")
        discrete_refs.append(evidence_ref(source_path, role=role))
    discrete_gate = discrete_verified and all(discrete_checks.values())
    routes.append({
        "route_id": "harness_discrete",
        "device_family": "discrete_household",
        "mechanism_status": status(discrete_gate),
        "backend": {"name": "harness_v2_workflow_discrete_faults", "workflow_state_machine": provenance.get("workflow_backend_class")},
        "runtime": {"adapter_sha256": provenance.get("adapter_sha256"), "probe_sha256": provenance.get("probe_sha256"), "workflow_backend_sha256": provenance.get("workflow_backend_sha256"), "case_count": len(cases)},
        "actions": {"fault_modes": sorted(modes), "devices": sorted(devices)},
        "observations": {"fault_state": "active_device_faults", "device_state": "workflow_backend public device state"},
        "mechanism": {"kind": "discrete_device_health_fault", "process": "Harness V2 household workflow under device offline/stuck/jammed/slowdown faults"},
        "replay": {"verified": discrete_gate, "checks": discrete_checks, "case_count": len(cases)},
        "agent_interface_verified": agent["routes"]["d1_discrete_device_fault"]["verified"],
        "agent_interface_status": agent["status"],
        "agent_interface": agent["routes"]["d1_discrete_device_fault"],
        "provenance_refs": discrete_refs,
        "boundary": {"evidence_basis": "Harness V2 WorkflowBackend discrete-device replay cases", "backend_only": True, "does_not_claim": ["responsibility_alignment", "gold_action", "dataset_quality", "evaluator_validity", "hardware_diagnostics", "failure_rate"]},
    })

    family_summary = [route["device_family"] for route in routes]
    mode_summary = {route["device_family"]: route["actions"]["fault_modes"] for route in routes}
    all_verified = all(route["mechanism_status"] == BACKEND_REPLAY_VERIFIED for route in routes)
    all_refs = [ref for route in routes for ref in route["provenance_refs"]]
    all_refs.extend(ref for ref in agent["provenance_refs"] if ref not in all_refs)
    return {
        "mechanism_id": "D1_fault_aware_building_control",
        "mechanism_status": status(all_verified),
        "agent_interface_verified": all(route["agent_interface_verified"] for route in routes),
        "agent_interface_status": agent["status"],
        "agent_interface": agent,
        "backend": {"name": "multi_backend_device_fault_routes", "route_count": len(routes)},
        "runtime": {"route_count": len(routes), "verified_route_count": sum(route["mechanism_status"] == BACKEND_REPLAY_VERIFIED for route in routes)},
        "actions": {"device_families": family_summary, "fault_modes": mode_summary},
        "observations": {"device_families": family_summary},
        "mechanism": {"kind": "fault_aware_multi_device_control", "process": "independent backend-only fault routes for HVAC, battery, EV charger, and discrete household devices"},
        "replay": {"verified": all_verified, "routes": {route["route_id"]: route["replay"] for route in routes}},
        "provenance_refs": all_refs,
        "boundary": {"evidence_basis": "four independently verified backend-only D1 device routes", "backend_only": True, "does_not_claim": ["repair_diagnostics", "real_device_failure_rate", "notification_success", "responsibility_alignment", "evaluator_validity", "production readiness"]},
        "device_routes": routes,
        "device_families": family_summary,
        "fault_modes": mode_summary,
    }


def d2_entry() -> dict[str, Any]:
    route_specs = (
        {
            "route_id": "energyplus_iaq",
            "gate": GENERATED / "d2_humidity_air_quality_v1" / "gate_report.json",
            "schema": "d2-humidity-air-quality-energyplus-backend-gate-v2",
            "adapter": ROOT / "d2_humidity_air_quality_adapter.py",
            "probe": ROOT / "probe_d2_humidity_air_quality.py",
            "kind": "iaq_humidity_ventilation",
            "basis": "EnergyPlus IAQ and humidity real-runtime replay gate",
        },
        {
            "route_id": "wntr_residential_water",
            "gate": GENERATED / "d2_wntr_v1" / "gate_report.json",
            "schema": "d2-wntr-backend-gate-v1",
            "adapter": ROOT / "unified_compiler" / "adapters" / "d2_wntr.py",
            "probe": ROOT / "probe_d2_wntr.py",
            "kind": "residential_water_pressure_flow_and_leak",
            "basis": "WNTR 1.3.0 real-runtime residential water-network replay gate",
        },
        {
            "route_id": "fds_smoke_fire",
            "gate": GENERATED / "d2_fds_v1" / "gate_report.json",
            "schema": "d2-fds-smoke-fire-door-backend-gate-v1",
            "adapter": ROOT / "d2_fds_adapter.py",
            "probe": ROOT / "probe_d2_fds.py",
            "kind": "smoke_fire_ventilation_and_door_propagation",
            "basis": "FDS 6.11.1 real-runtime smoke/fire propagation replay gate",
        },
        {
            "route_id": "modelica_buildings_aixlib",
            "gate": GENERATED / "d2_modelica_buildings_aixlib_v1" / "gate_report.json",
            "schema": "d2-modelica-buildings-aixlib-backend-gate-v1",
            "adapter": ROOT / "d2_modelica_buildings_aixlib_adapter.py",
            "probe": ROOT / "probe_d2_modelica_buildings_aixlib.py",
            "kind": "multi_room_thermo_hygrometric_heating",
            "basis": "OpenModelica Buildings/AixLib real-runtime thermo-hygrometric replay gate",
        },
    )

    routes: list[dict[str, Any]] = []
    for spec in route_specs:
        report = load_json(spec["gate"])
        _require_schema(report, spec["schema"], f"D2 {spec['route_id']}")
        checks = {
            key: _bool(report.get(key), f"D2.{spec['route_id']}.{key}")
            for key in (
                "runtime_stepping_gate",
                "action_available_gate",
                "action_sensitivity_gate",
                "determinism_gate",
                "time_alignment_gate",
                "provenance_gate",
            )
        }
        passed = _bool(report.get("passed"), f"D2.{spec['route_id']}.passed")
        route_gate = passed and all(checks.values())
        if report.get("adapter_sha256") != sha256(spec["adapter"]):
            raise DynamicCatalogError(f"D2 {spec['route_id']} adapter provenance is stale")
        if report.get("probe_sha256") != sha256(spec["probe"]):
            raise DynamicCatalogError(f"D2 {spec['route_id']} probe provenance is stale")
        trace_refs = report.get("causal_trace_refs", {})
        if not isinstance(trace_refs, Mapping) or (route_gate and not trace_refs):
            raise DynamicCatalogError(f"D2 {spec['route_id']} causal trace refs are missing")
        refs = [
            evidence_ref(spec["gate"], role=f"{spec['route_id']}_backend_replay_gate"),
            evidence_ref(spec["adapter"], role=f"{spec['route_id']}_backend_adapter"),
            evidence_ref(spec["probe"], role=f"{spec['route_id']}_backend_probe"),
        ]
        for name, ref in trace_refs.items():
            if not isinstance(ref, Mapping) or not isinstance(ref.get("path"), str):
                raise DynamicCatalogError(f"D2 {spec['route_id']} malformed trace ref: {name}")
            trace_path = ROOT / ref["path"]
            if ref.get("sha256") != sha256(trace_path):
                raise DynamicCatalogError(f"D2 {spec['route_id']} trace ref is stale: {name}")
            refs.append(evidence_ref(trace_path, role=f"{spec['route_id']}_causal_trace_{name}"))
        interactive_path = GENERATED / "d2_closed_loop_v1" / f"{spec['route_id']}.json"
        interactive_report: Mapping[str, Any] | None = None
        if interactive_path.is_file():
            interactive_report = load_json(interactive_path)
            _require_schema(interactive_report, "d2-closed-loop-conformance-v1", f"D2 {spec['route_id']} interactive")
            if interactive_report.get("adapter_sha256") != sha256(spec["adapter"]):
                raise DynamicCatalogError(f"D2 {spec['route_id']} interactive adapter provenance is stale")
            refs.append(evidence_ref(interactive_path, role=f"{spec['route_id']}_interactive_conformance"))
        interactive = _interactive_evidence(report, interactive_report)
        routes.append({
            "route_id": spec["route_id"],
            "mechanism_status": status(route_gate),
            "backend": {
                "name": report.get("backend"),
                "version": report.get("backend_version"),
                "family": report.get("backend_family"),
            },
            "runtime": {
                "adapter_sha256": report.get("adapter_sha256"),
                "probe_sha256": report.get("probe_sha256"),
                "runtime_probe": report.get("runtime_probe"),
            },
            "actions": {"actuator": report.get("action_adapter"), "available": checks["action_available_gate"]},
            "observations": report.get("observation_roles"),
            "mechanism": {"kind": spec["kind"], "process": report.get("physical_process_id")},
            "replay": {
                "verified": route_gate,
                "deterministic": checks["determinism_gate"],
                "action_sensitive": checks["action_sensitivity_gate"],
                "time_aligned": checks["time_alignment_gate"],
                "replay_arm_count": report.get("replay_arm_count"),
            },
            # Kept separate from replay: a successful fixed-action trajectory
            # must never silently certify Agent online control.
            "interactive": interactive,
            "provenance_refs": refs,
            "blockers": list(report.get("exclusion_reasons", [])),
            "boundary": {
                "evidence_basis": spec["basis"],
                "surrogate_used": bool(report.get("surrogate_model_used", report.get("surrogate_fallback", False))),
                "does_not_claim": ["responsibility alignment", "benchmark Episode readiness", "household measured-data generalization"],
            },
        })

    verified_routes = [route for route in routes if route["mechanism_status"] == BACKEND_REPLAY_VERIFIED]
    interactive_routes = [route for route in routes if route["interactive"]["interactive_step_verified"] and route["interactive"]["interactive_conformance_gate"]]
    all_refs = [ref for route in routes for ref in route["provenance_refs"]]
    return {
        "mechanism_id": "D2_continuous_physical_dynamics",
        # D2 is a four-route claim: one passing route cannot certify the
        # aggregate mechanism.
        "mechanism_status": status(len(verified_routes) == len(routes)),
        "interactive_status": status(len(interactive_routes) == len(routes)),
        "backend": {"name": "multi_backend_continuous_physics_routes", "route_count": len(routes)},
        "runtime": {
            "route_count": len(routes),
            "verified_route_count": len(verified_routes),
            "pending_route_count": len(routes) - len(verified_routes),
            "interactive_verified_route_count": len(interactive_routes),
            "interactive_pending_route_count": len(routes) - len(interactive_routes),
        },
        "actions": {"route_ids": [route["route_id"] for route in routes]},
        "observations": {"physical_domains": [route["mechanism"]["kind"] for route in routes]},
        "mechanism": {"kind": "continuous_physical_dynamics", "process": "independent air-quality, water-network, smoke/fire, and thermo-hygrometric routes"},
        "replay": {"verified": len(verified_routes) == len(routes), "routes": {route["route_id"]: route["replay"] for route in routes}},
        "interactive": {
            "verified": len(interactive_routes) == len(routes),
            "verification_rule": "all four D2 routes must independently pass interactive conformance; replay evidence is insufficient",
            "routes": {route["route_id"]: route["interactive"] for route in routes},
        },
        "provenance_refs": all_refs,
        "boundary": {
            "evidence_basis": "each D2 route is gated independently; pending routes do not inherit verified status",
            "does_not_claim": ["responsibility alignment", "benchmark Episode readiness", "production readiness", "cross-platform runtime equivalence before Linux replay"],
        },
        "physical_routes": routes,
    }


def d3_entry() -> dict[str, Any]:
    """Build D3 as independently gated strong-coupling routes.

    D3 evidence was produced by several native backends with deliberately
    different report schemas.  This function normalizes only the public
    contract (replay, Agent, and cross-intervention); every verdict remains
    local to its route.  In particular, a passing CityLearn route cannot
    upgrade a pending water, thermal, electric, or ventilation route.
    """

    city_path = GENERATED / "d3_citylearn_coupling_evidence.json"
    city = load_json(city_path)
    if city.get("schema_version") != "d3-citylearn-coupling-probe-v1":
        raise DynamicCatalogError("D3 CityLearn evidence schema is not pinned")
    city_passed = _bool(city.get("passed"), "D3.citylearn_multi_system.passed")
    city_baseline = city.get("baseline")
    city_provenance = city_baseline.get("provenance") if isinstance(city_baseline, Mapping) else None
    if not isinstance(city_baseline, Mapping) or not isinstance(city_provenance, Mapping) or city_provenance.get("citylearn_version") != "2.5.0":
        raise DynamicCatalogError("D3 CityLearn runtime provenance is missing")
    if city_baseline.get("trajectory_sha256") != canonical_digest(city_baseline.get("records")):
        raise DynamicCatalogError("D3 CityLearn baseline trajectory digest is stale")
    city_condition = city.get("exogenous_condition_contrast")
    if not isinstance(city_condition, Mapping) or city_condition.get("strategy_conditions_changed") is not True:
        raise DynamicCatalogError("D3 CityLearn cross-condition evidence is missing")
    city_deterministic = _bool(city.get("deterministic_replay"), "D3.citylearn_multi_system.deterministic_replay")
    city_cross = {
        "battery_soc_delta": float(city.get("battery_soc_delta", 0.0)),
        "hvac_temperature_delta_c": float(city.get("hvac_temperature_delta_c", 0.0)),
        "net_electricity_delta": float(city.get("net_electricity_delta", 0.0)),
    }
    city_strong_checks = {
        "battery_counterfactual": city_cross["battery_soc_delta"] > 1e-9,
        "hvac_counterfactual": city_cross["hvac_temperature_delta_c"] > 1e-9,
        "net_electricity_counterfactual": city_cross["net_electricity_delta"] > 1e-9,
        "exogenous_condition_contrast": True,
    }
    city_agent = d3_agent_interface_evidence()
    city_replay = {
        "verified": city_passed and city_deterministic and all(city_strong_checks.values()),
        "probe_passed": city_passed,
        "deterministic": city_deterministic,
        "action_sensitive": {"battery": city_strong_checks["battery_counterfactual"], "hvac": city_strong_checks["hvac_counterfactual"], "net_electricity": city_strong_checks["net_electricity_counterfactual"]},
        "trajectory_scope": "single_native_trajectory",
        "horizon_steps": city.get("full_episode_steps"),
    }
    city_adapter = ROOT / "d3_citylearn_coupling_adapter.py"
    city_refs = [evidence_ref(city_path, role="citylearn_multi_system_evidence"), evidence_ref(city_adapter, role="citylearn_multi_system_adapter"), *city_agent["provenance_refs"]]
    # Independent native channel responses do not establish cross-channel
    # coupling; keep Episode evidence while excluding verified coupling.
    city_gate = False
    city_route = {
        "route_id": "citylearn_multi_system",
        "mechanism_status": status(city_gate),
        "episode_generation_supported": True,
        "d3_coupling_supported": False,
        "backend": {"name": city_baseline.get("backend", city.get("backend", "CityLearn")), "version": city_baseline.get("backend_version", city.get("backend_version", "2.5.0")), "route": "single_native_trajectory"},
        "runtime": {"citylearn_version": city_provenance.get("citylearn_version"), "runtime_sha256": city_provenance.get("runtime_sha256"), "adapter_sha256": sha256(city_adapter)},
        "actions": {"channels": city_baseline.get("action_sensitive_channels"), "battery": True, "hvac": True},
        "observations": {"native_observation_names": ["weather", "occupancy", "HVAC", "PV", "battery", "net_electricity"]},
        "mechanism": {"kind": "multi-system building-energy Episode generation", "process": "independent native HVAC and battery responses; cross-channel coupling unproven"},
        "replay": city_replay,
        "agent": {"verified": city_agent["verified"], "status": city_agent["status"], "evidence": city_agent},
        "strong_coupling": {"verified": False, "checks": city_strong_checks, "shared_resource": "native CityLearn building net-electricity balance across HVAC, PV, and battery", "cross_intervention": city_cross, "unsupported_reason": "independent channel effects are not a cross-channel effect"},
        "shared_resource": "native CityLearn building net-electricity balance across HVAC, PV, and battery",
        "cross_intervention": city_cross,
        "agent_interface_verified": city_agent["verified"], "agent_interface_status": city_agent["status"], "agent_interface": city_agent,
        "provenance": {"runtime": dict(city_provenance), "refs": city_refs}, "provenance_refs": city_refs,
        "boundary": {"evidence_basis": "single native CityLearn trajectory", "exogenous_condition_deltas": city_condition.get("deltas"), "does_not_claim": ["native D3 cross-channel coupling", "native clipping", "notification_success", "authorization", "multi-building coupling", "generalization beyond cited source windows"]},
    }

    # CityLearn multi-building route: native building transitions and
    # district aggregation are real, while the shared-meter threshold is an
    # explicit external feasibility constraint.  Keep the negative evidence
    # (no native cross-building physical feedback and no native clipping)
    # visible so this route is not mislabeled as a physical district network.
    multi_path = GENERATED / "d3_citylearn_multibuilding_evidence.json"
    multi = load_json(multi_path)
    _require_schema(multi, "d3-citylearn-multibuilding-probe-v1", "D3 CityLearn multi-building")
    multi_passed = _bool(multi.get("passed"), "D3.citylearn_multibuilding_competition.passed")
    multi_baseline = multi.get("baseline")
    multi_gate = multi.get("coupling_gate")
    multi_constraint = multi.get("shared_meter_constraint")
    if not isinstance(multi_baseline, Mapping) or not isinstance(multi_gate, Mapping) or not isinstance(multi_constraint, Mapping):
        raise DynamicCatalogError("D3 CityLearn multi-building evidence is incomplete")
    multi_prov = multi_baseline.get("provenance")
    if not isinstance(multi_prov, Mapping) or multi_prov.get("citylearn_version") != "2.5.0":
        raise DynamicCatalogError("D3 CityLearn multi-building provenance is missing")
    if not isinstance(multi.get("building_ids"), list) or len(multi["building_ids"]) < 2:
        raise DynamicCatalogError("D3 CityLearn multi-building route has fewer than two buildings")
    multi_records = multi_baseline.get("records")
    if not isinstance(multi_records, list) or not multi_records or multi_baseline.get("trajectory_sha256") != canonical_digest(multi_records):
        raise DynamicCatalogError("D3 CityLearn multi-building baseline digest is stale")
    if multi.get("native_env_instances_per_trajectory") != 1 or multi.get("single_native_episode") is not True:
        raise DynamicCatalogError("D3 CityLearn multi-building native episode binding is missing")
    multi_replay_checks = {
        "probe_passed": multi_passed,
        "deterministic_replay": _bool(multi.get("deterministic_replay"), "D3.citylearn_multibuilding_competition.deterministic_replay"),
        "native_multi_building_transition": _bool(multi.get("native_multi_building_transition"), "D3.citylearn_multibuilding_competition.native_multi_building_transition"),
    }
    multi_strong_checks = {
        "coupling_gate_passed": _bool(multi_gate.get("passed"), "D3.citylearn_multibuilding_competition.coupling_gate.passed"),
        "native_multi_building_dynamics": _bool(multi_gate.get("native_multi_building_dynamics"), "D3.citylearn_multibuilding_competition.native_multi_building_dynamics"),
        "native_district_aggregation": _bool(multi_gate.get("native_district_aggregation"), "D3.citylearn_multibuilding_competition.native_district_aggregation"),
        "external_shared_meter_capacity_coupling": _bool(multi_gate.get("external_shared_meter_capacity_coupling"), "D3.citylearn_multibuilding_competition.external_shared_meter_capacity_coupling"),
        "headroom_intervention_nonzero": float(multi.get("other_building_feasible_headroom_delta", 0.0)) > 1e-9,
        # These are required boundary facts, not pass conditions to be
        # inverted: the route is valid precisely because it does not claim
        # native physical feedback or native action clipping.
        "native_cross_building_physical_feedback_absent": multi_gate.get("native_cross_building_physical_feedback") is False,
        "native_clipping_absent": multi_constraint.get("native_clipping") is False,
    }
    multi_actions = multi_records[0].get("action") if isinstance(multi_records[0], Mapping) else None
    multi_agent = {
        "verified": all((multi_replay_checks["probe_passed"], multi_replay_checks["deterministic_replay"], multi_replay_checks["native_multi_building_transition"], multi.get("single_native_episode") is True, isinstance(multi_actions, Mapping))),
        "status": status(all((multi_replay_checks["probe_passed"], multi_replay_checks["deterministic_replay"], multi_replay_checks["native_multi_building_transition"], multi.get("single_native_episode") is True, isinstance(multi_actions, Mapping)))),
        "checks": {"reset_and_replay": multi_replay_checks["deterministic_replay"], "native_multi_building_step": multi_replay_checks["native_multi_building_transition"], "joint_building_action": isinstance(multi_actions, Mapping), "single_native_episode": multi.get("single_native_episode") is True},
        "source": "d3_citylearn_multibuilding_evidence.json",
    }
    multi_replay = {"verified": all(multi_replay_checks.values()), **multi_replay_checks, "trajectory_scope": "single_native_multi_building_episode", "horizon_steps": multi.get("full_episode_steps")}
    multi_strong = {"verified": all(multi_strong_checks.values()), "checks": multi_strong_checks, "shared_resource": multi_constraint.get("semantics"), "cross_intervention": {"other_building_feasible_headroom_delta": multi.get("other_building_feasible_headroom_delta"), "other_building_native_net_delta": multi.get("other_building_native_net_delta")}}
    multi_adapter = ROOT / "d3_citylearn_multibuilding_adapter.py"; multi_probe = ROOT / "probe_d3_citylearn_multibuilding.py"
    multi_asset_paths = [ROOT / "shared_assets/citylearn_v2.5.0/asset_manifest.json", ROOT / "shared_assets/citylearn_v2.5.0/misc/battery_choices.yaml", ROOT / "shared_assets/citylearn_v2.5.0/misc/lbl-tracking_the_sun-res-pv.csv", ROOT / "shared_assets/citylearn_v2.5.0/dataset/weather.epw", ROOT.parent / "v5_scenario_compiler/source_cache/schema.json"]
    for source_path, digest_key in ((multi_asset_paths[0], "asset_manifest_sha256"), (multi_asset_paths[1], "battery_catalog_sha256"), (multi_asset_paths[2], "pv_catalog_sha256"), (multi_asset_paths[3], "weather_sha256"), (multi_asset_paths[4], "source_schema_sha256")):
        _verify_file_hash(source_path, multi_prov.get(digest_key), f"D3.citylearn_multibuilding_competition.{digest_key}")
    for building_id, digest_value in multi_prov.get("source_trace_sha256_by_building", {}).items():
        _verify_file_hash(multi_asset_paths[4].parent / f"{building_id}.csv", digest_value, f"D3.citylearn_multibuilding_competition.{building_id}.csv")
    for building_id, digest_value in multi_prov.get("thermal_model_sha256_by_building", {}).items():
        _verify_file_hash(multi_asset_paths[4].parent / f"{building_id}.pth", digest_value, f"D3.citylearn_multibuilding_competition.{building_id}.pth")
    multi_refs = [evidence_ref(multi_path, role="citylearn_multibuilding_evidence"), evidence_ref(multi_adapter, role="citylearn_multibuilding_adapter"), evidence_ref(multi_probe, role="citylearn_multibuilding_probe"), *[evidence_ref(path, role="citylearn_multibuilding_asset") for path in multi_asset_paths]]
    multi_gate_final = multi_replay["verified"] and multi_agent["verified"] and multi_strong["verified"]
    multi_route = {
        "route_id": "citylearn_multibuilding_competition",
        "mechanism_status": status(multi_gate_final),
        "backend": {"name": multi.get("backend"), "version": multi.get("baseline", {}).get("backend_version"), "route": "single_native_multi_building_episode"},
        "runtime": {"building_ids": multi.get("building_ids"), "citylearn_version": multi_prov.get("citylearn_version"), "runtime_sha256": multi_prov.get("runtime_sha256"), "native_env_instances_per_trajectory": multi.get("native_env_instances_per_trajectory")},
        "actions": {"channels": [f"{building}.{action}" for building in multi.get("building_ids", []) for action in ("battery_rate", "hvac_rate")], "native_clipping": False},
        "observations": {"district": ["district_net_kwh", "district_peak_kwh", "shared_meter_headroom_kwh"], "per_building": ["battery_soc", "indoor_temperature_c", "net_electricity_kwh"]},
        "mechanism": {"kind": "multi-building shared-meter feasibility coupling", "process": "native CityLearn multi-building transition plus external shared-meter threshold"},
        "replay": multi_replay,
        "agent": multi_agent,
        "strong_coupling": multi_strong,
        "shared_resource": multi_constraint.get("semantics"),
        "cross_intervention": multi_strong["cross_intervention"],
        "provenance": {"runtime": dict(multi_prov), "refs": multi_refs}, "provenance_refs": multi_refs,
        "boundary": {"evidence_basis": "native multi-building CityLearn dynamics and district aggregation with external shared-meter feasibility constraint", "native_cross_building_physical_feedback": False, "external_shared_meter_capacity_coupling": True, "native_clipping": False, "does_not_claim": ["native cross-building physical feedback", "native transformer clipping", "physical district network", "production readiness"]},
    }

    def _simple_agent(report: Mapping[str, Any], checks: Mapping[str, bool], source: str) -> dict[str, Any]:
        normalized = {name: bool(value) for name, value in checks.items()}
        verified = all(normalized.values())
        return {"verified": verified, "status": status(verified), "checks": normalized, "source": source, "interface": report.get("agent_closed_loop", {}).get("interface", []) if isinstance(report.get("agent_closed_loop"), Mapping) else []}

    def _route_ref(path: Path, role: str) -> dict[str, Any]:
        return evidence_ref(path, role=role)

    # WNTR route: the gate already contains both native online-contract and
    # bidirectional hydraulic intervention evidence.
    water_path = GENERATED / "d3_wntr_water_competition_v1" / "gate_report.json"
    water = load_json(water_path)
    _require_schema(water, "d3-wntr-water-competition-gate-v1", "D3 WNTR")
    water_replay_checks = {key: _bool(water.get(key), f"D3.wntr_water_competition.{key}") for key in ("passed", "runtime_stepping_gate", "determinism_gate", "state_continuity_gate")}
    water_strong_checks = {"bidirectional_coupling_gate": _bool(water.get("bidirectional_coupling_gate"), "D3.wntr_water_competition.bidirectional_coupling_gate"), "cross_intervention_nonzero": all(float(v) > 1e-9 for v in water.get("cross_intervention_deltas", {}).values())}
    water_agent = _simple_agent(water, {"reset_and_step": water_replay_checks["passed"], "legal_actions": water.get("action_available_gate") is True, "persistent_state": water.get("agent_closed_loop", {}).get("prefix_rerun_per_step") is False, "time_monotone": water_replay_checks["runtime_stepping_gate"], "state_continuity": water_replay_checks["state_continuity_gate"]}, "d3_wntr_water_competition_v1/gate_report.json")
    water_replay = {"verified": all(water_replay_checks.values()), **water_replay_checks}
    water_gate = water_replay["verified"] and water_agent["verified"] and all(water_strong_checks.values())
    water_adapter = ROOT / "d3_wntr_water_competition_adapter.py"; water_probe = ROOT / "probe_d3_wntr_water_competition.py"
    _verify_file_hash(water_adapter, water.get("adapter_sha256"), "D3.wntr_water_competition.adapter_sha256")
    _verify_file_hash(water_probe, water.get("probe_sha256"), "D3.wntr_water_competition.probe_sha256")
    water_refs = [_route_ref(water_path, "wntr_water_competition_gate"), _route_ref(water_adapter, "wntr_water_competition_adapter"), _route_ref(water_probe, "wntr_water_competition_probe")]
    for name, item in water.get("trace_refs", {}).items():
        trace = _resolve_ref(item.get("path"), f"D3.wntr_water_competition.trace.{name}")
        _verify_file_hash(trace, item.get("sha256"), f"D3.wntr_water_competition.trace.{name}.sha256")
        water_refs.append(_route_ref(trace, f"wntr_water_competition_trace_{name}"))
    water_route = {"route_id": "wntr_water_competition", "mechanism_status": status(water_gate), "backend": {"name": water.get("backend"), "engine": water.get("backend_engine"), "version": water.get("backend_version")}, "runtime": {"adapter_sha256": water.get("adapter_sha256"), "probe_sha256": water.get("probe_sha256"), "runtime": water.get("provenance")}, "actions": {"channels": water.get("action_channels")}, "observations": water.get("observation_roles"), "mechanism": {"kind": "shared water-service hydraulics", "process": water.get("physical_process_id")}, "replay": water_replay, "agent": water_agent, "strong_coupling": {"verified": all(water_strong_checks.values()), "checks": water_strong_checks, "shared_resource": water.get("shared_resource"), "cross_intervention": water.get("cross_intervention_deltas")}, "shared_resource": water.get("shared_resource"), "cross_intervention": water.get("cross_intervention_deltas"), "provenance": {"report": water.get("provenance"), "refs": water_refs}, "provenance_refs": water_refs, "boundary": {"evidence_basis": "WNTR 1.3.0 native hydraulic replay and live route gate", "does_not_claim": ["water-quality chemistry", "household measured-data generalization"]}}

    # Modelica route: the FMU gate is the replay and Agent evidence source;
    # action-channel acceptance and native doStep are checked from the pinned
    # causal witnesses emitted by the probe.
    heat_path = GENERATED / "d3_modelica_shared_heat_v1" / "gate_report.json"; heat = load_json(heat_path)
    _require_schema(heat, "d3-modelica-shared-heat-probe-v1", "D3 Modelica")
    heat_combined = heat.get("combined", {}); heat_space = heat.get("space_only", {}); heat_dhw = heat.get("dhw_only", {})
    heat_replay_checks = {"passed": _bool(heat.get("passed"), "D3.modelica_shared_heat.passed"), "native_fmi_do_step": _bool(heat.get("native_fmi_do_step"), "D3.modelica_shared_heat.native_fmi_do_step"), "deterministic_reset": _bool(heat.get("deterministic_reset"), "D3.modelica_shared_heat.deterministic_reset")}
    heat_strong_checks = {"shared_capacity_gate": _bool(heat.get("shared_capacity_gate"), "D3.modelica_shared_heat.shared_capacity_gate"), "space_request_changes_dhw_allocation": abs(float(heat_combined.get("observation", {}).get("allocated_dhw_heat_w", 0.0)) - float(heat_dhw.get("observation", {}).get("allocated_dhw_heat_w", 0.0))) > 1e-9, "dhw_request_changes_space_allocation": abs(float(heat_combined.get("observation", {}).get("allocated_space_heat_w", 0.0)) - float(heat_space.get("observation", {}).get("allocated_space_heat_w", 0.0))) > 1e-9}
    heat_agent = _simple_agent(heat, {"reset": heat_replay_checks["deterministic_reset"], "legal_actions": set(heat_combined.get("action", {})) == {"space_heating_request", "dhw_request"}, "native_step": heat_replay_checks["native_fmi_do_step"], "time_progress": float(heat_combined.get("time_seconds", 0.0)) > 0.0}, "d3_modelica_shared_heat_v1/gate_report.json")
    heat_replay = {"verified": all(heat_replay_checks.values()), **heat_replay_checks}; heat_gate = heat_replay["verified"] and heat_agent["verified"] and all(heat_strong_checks.values())
    heat_adapter = ROOT / "d3_modelica_shared_heat_adapter.py"; heat_source = ROOT / "shared_assets/modelica_d3_shared_heat_v1/D3SharedHeat.mo"; heat_compile = ROOT / "compile_d3_modelica_shared_heat_fmu.py"
    heat_provenance = heat.get("provenance", {}); heat_refs = [_route_ref(heat_path, "modelica_shared_heat_gate"), _route_ref(heat_adapter, "modelica_shared_heat_adapter"), _route_ref(heat_source, "modelica_shared_heat_model"), _route_ref(heat_compile, "modelica_shared_heat_compiler")]
    if not isinstance(heat_provenance, Mapping):
        raise DynamicCatalogError("D3 Modelica provenance is missing")
    _verify_file_hash(heat_adapter, heat_provenance.get("adapter_sha256"), "D3.modelica_shared_heat.adapter_sha256")
    _verify_file_hash(heat_source, heat_provenance.get("model_source_sha256"), "D3.modelica_shared_heat.model_source_sha256")
    _verify_file_hash(heat_compile, heat_provenance.get("compiler_sha256"), "D3.modelica_shared_heat.compiler_sha256")
    heat_route = {"route_id": "modelica_shared_heat", "mechanism_status": status(heat_gate), "backend": {"name": "Modelica", "model": heat_provenance.get("model_source"), "fmi": heat_provenance.get("fmu_root")}, "runtime": {"adapter_sha256": heat_provenance.get("adapter_sha256"), "fmu_sha256": heat_provenance.get("fmu_sha256"), "runtime": heat_provenance.get("runtime")}, "actions": {"channels": ["space_heating_request", "dhw_request"]}, "observations": {"signals": ["room_a_temperature_c", "dhw_temperature_c", "allocated_space_heat_w", "allocated_dhw_heat_w", "shared_heat_pump_capacity_used_w", "service_shortfall_w"]}, "mechanism": {"kind": "shared heat-pump service allocation", "process": "Modelica FMI co-simulation with space-heating and DHW demands"}, "replay": heat_replay, "agent": heat_agent, "strong_coupling": {"verified": all(heat_strong_checks.values()), "checks": heat_strong_checks, "shared_resource": "finite native heat-pump capacity", "cross_intervention": {"combined_vs_dhw_only_dhw_w": abs(float(heat_combined.get("observation", {}).get("allocated_dhw_heat_w", 0.0)) - float(heat_dhw.get("observation", {}).get("allocated_dhw_heat_w", 0.0))), "combined_vs_space_only_space_w": abs(float(heat_combined.get("observation", {}).get("allocated_space_heat_w", 0.0)) - float(heat_space.get("observation", {}).get("allocated_space_heat_w", 0.0)))}}, "shared_resource": "finite native heat-pump capacity", "cross_intervention": {"combined_vs_dhw_only_dhw_w": abs(float(heat_combined.get("observation", {}).get("allocated_dhw_heat_w", 0.0)) - float(heat_dhw.get("observation", {}).get("allocated_dhw_heat_w", 0.0))), "combined_vs_space_only_space_w": abs(float(heat_combined.get("observation", {}).get("allocated_space_heat_w", 0.0)) - float(heat_space.get("observation", {}).get("allocated_space_heat_w", 0.0)))}, "provenance": {"report": heat_provenance, "refs": heat_refs}, "provenance_refs": heat_refs, "boundary": {"evidence_basis": "native FMI 2.0 D3SharedHeat FMU probe", "does_not_claim": ["hardware heat-pump diagnostics", "multi-building thermal coupling"]}}

    def _native_competition_route(route_id: str, path: Path, report: Mapping[str, Any], adapter: Path, probe: Path, *, schema: str, backend: str, engine: str, channels: list[str], shared: str, process: str, extra_refs: list[Path]) -> dict[str, Any]:
        _require_schema(report, schema, f"D3 {route_id}")
        checks = report.get("checks", {})
        if not isinstance(checks, Mapping) or not checks:
            # WNTR and EnergyPlus gates expose the same contract as named
            # top-level gate fields rather than a nested ``checks`` map.
            checks = {
                "passed": report.get("passed"),
                "runtime_stepping_gate": report.get("runtime_stepping_gate"),
                "determinism_gate": report.get("determinism_gate"),
                "bidirectional_coupling_gate": report.get("bidirectional_coupling_gate"),
            }
        if any(value is None for value in checks.values()):
            raise DynamicCatalogError(f"D3 {route_id} checks are missing")
        normalized = {str(key): _bool(value, f"D3.{route_id}.checks.{key}") for key, value in checks.items()}
        replay = {"verified": all(normalized.values()), "checks": normalized}
        coupling = {"bidirectional_intervention": normalized.get("bidirectional_intervention", report.get("bidirectional_coupling_gate") is True), "shared_resource_observable": normalized.get("shared_transformer_capacity_observable", report.get("ems_shared_capacity_gate", False) is True)}
        # EV2Gym stores its agent-contract checks under ``checks``; EnergyPlus
        # stores the equivalent native online contract in its gate fields.
        if route_id.startswith("ev2gym"):
            agent_checks = {key: normalized.get(key, False) for key in ("native_reset", "two_competing_channels", "deterministic_reset", "time_monotone", "illegal_action_no_advance", "invalid_dt_no_advance")}
            cross = report.get("details", {}).get("power_witness", {})
        else:
            agent_meta = report.get("agent_closed_loop", {})
            agent_checks = {"persistent_state": agent_meta.get("persistent_state") is True, "reset_and_step": report.get("passed") is True, "time_monotone": report.get("runtime_stepping_gate") is True, "legal_actions": bool(report.get("action_channels")), "no_prefix_rerun": agent_meta.get("prefix_rerun_per_step") is False}
            cross = report.get("cross_intervention_deltas", {})
        agent = _simple_agent(report, agent_checks, str(path.relative_to(ROOT)))
        coupling["cross_intervention_nonzero"] = bool(cross) and all(float(value) > 1e-9 for value in cross.values() if isinstance(value, (int, float)))
        strong = {"verified": all(bool(value) for value in coupling.values()), "checks": coupling, "shared_resource": shared, "cross_intervention": cross}
        refs = [_route_ref(path, f"{route_id}_evidence"), _route_ref(adapter, f"{route_id}_adapter"), _route_ref(probe, f"{route_id}_probe")]
        for ref_path in extra_refs:
            if ref_path.is_file(): refs.append(_route_ref(ref_path, f"{route_id}_provenance"))
        gate = replay["verified"] and agent["verified"] and strong["verified"]
        return {"route_id": route_id, "mechanism_status": status(gate), "backend": {"name": backend, "engine": engine, "version": report.get("energyplus_version", report.get("backend_version"))}, "runtime": {"adapter_sha256": report.get("adapter_sha256"), "probe_sha256": report.get("probe_sha256"), "report_status": report.get("status")}, "actions": {"channels": channels}, "observations": {"native": True}, "mechanism": {"kind": process, "process": report.get("physical_process_id")}, "replay": replay, "agent": agent, "strong_coupling": strong, "shared_resource": shared, "cross_intervention": cross, "provenance": {"runtime": report.get("provenance", {}), "refs": refs}, "provenance_refs": refs, "boundary": {"evidence_basis": f"{backend} native D3 route gate", "does_not_claim": ["production readiness", "cross-platform runtime equivalence"]}}

    ev_path = GENERATED / "d3_ev2gym_electric_competition_evidence.json"; ev = load_json(ev_path); ev_adapter = ROOT / "d3_ev2gym_electric_competition_adapter.py"; ev_probe = ROOT / "probe_d3_ev2gym_electric_competition.py"
    ev_config = ROOT / "d3_ev2gym_assets/competition_config.yaml"; ev_topology = ROOT / "d3_ev2gym_assets/shared_transformer_topology.json"; ev_lock = ROOT / "d3_ev2gym_assets/runtime_requirements.lock"; ev_native = ROOT.parent / "v10_diversity_aware_compiler/.runtime/ev2gym/ev2gym/models/ev2gym_env.py"
    ev_provenance = ev.get("provenance")
    if not isinstance(ev_provenance, Mapping) or ev_provenance.get("adapter_id") != ev.get("adapter_id"):
        raise DynamicCatalogError("D3 EV2Gym provenance is missing or mismatched")
    for source_path, digest_key in ((ev_config, "config_template_sha256"), (ev_topology, "topology_sha256"), (ev_lock, "runtime_lockfile_sha256"), (ev_native, "native_checkout_sha256")):
        _verify_file_hash(source_path, ev_provenance.get(digest_key), f"D3.ev2gym_electric_competition.{digest_key}")
    ev_refs = [ev_config, ev_topology, ev_lock, ev_native]
    ev_route = _native_competition_route("ev2gym_electric_competition", ev_path, ev, ev_adapter, ev_probe, schema="d3-ev2gym-electric-competition-v1", backend="EV2Gym", engine="official_native_transformer", channels=["charger_0_rate", "charger_1_rate"], shared="one native transformer shared by two charging ports", process="native EV2Gym shared-transformer competition", extra_refs=ev_refs)

    ep_path = GENERATED / "d3_energyplus_shared_ventilation_v1" / "gate_report.json"; ep = load_json(ep_path); ep_adapter = ROOT / "d3_energyplus_shared_ventilation_adapter.py"; ep_probe = ROOT / "probe_d3_energyplus_shared_ventilation.py"
    ep_model = ROOT / "generated/d3_energyplus_shared_ventilation_v1/D3SharedVentilation.idf"; ep_official = ROOT / "shared_assets/energyplus_v26.1.0/EnergyPlus-26.1.0-6f2e40d102-Darwin-macOS13-arm64/ExampleFiles/VentilationSimpleTest.idf"; ep_binary = ROOT / "shared_assets/energyplus_v26.1.0/EnergyPlus-26.1.0-6f2e40d102-Darwin-macOS13-arm64/energyplus"
    ep_provenance = ep.get("provenance")
    if not isinstance(ep_provenance, Mapping):
        raise DynamicCatalogError("D3 EnergyPlus provenance is missing")
    _verify_file_hash(ep_adapter, ep.get("adapter_sha256"), "D3.energyplus_shared_ventilation.adapter_sha256")
    _verify_file_hash(ep_probe, ep.get("probe_sha256"), "D3.energyplus_shared_ventilation.probe_sha256")
    _verify_file_hash(ep_model, ep_provenance.get("working_model_sha256"), "D3.energyplus_shared_ventilation.working_model_sha256")
    _verify_file_hash(ep_official, ep_provenance.get("official_source_model_sha256"), "D3.energyplus_shared_ventilation.official_source_model_sha256")
    _verify_file_hash(ep_binary, ep_provenance.get("energyplus_binary_sha256"), "D3.energyplus_shared_ventilation.energyplus_binary_sha256")
    ep_refs = [ep_model, ep_official, ep_binary]
    ep_route = _native_competition_route("energyplus_shared_ventilation", ep_path, ep, ep_adapter, ep_probe, schema="d3-energyplus-shared-ventilation-gate-v1", backend="EnergyPlus", engine="EnergyPlusAPI", channels=["zone_a_airflow_request", "zone_b_airflow_request"], shared=ep.get("shared_resource", "one native EMS-limited fan capacity"), process="native finite shared ventilation", extra_refs=ep_refs)

    routes = [city_route, multi_route, water_route, heat_route, ev_route, ep_route]
    verified_routes = [route for route in routes if route["mechanism_status"] == BACKEND_REPLAY_VERIFIED]
    pending_routes = [route for route in routes if route["mechanism_status"] == EVIDENCE_PENDING]
    all_refs = [ref for route in routes for ref in route["provenance_refs"]]
    all_verified = len(verified_routes) == len(routes)
    return {
        "mechanism_id": "D3_multi_backend_strong_coupling",
        "mechanism_status": status(all_verified),
        "backend": {"name": "independent_native_strong_coupling_routes", "route_count": len(routes)},
        "runtime": {"route_count": len(routes), "verified_route_count": len(verified_routes), "pending_route_count": len(pending_routes)},
        "summary": {"route_count": len(routes), "verified_route_count": len(verified_routes), "pending_route_count": len(pending_routes), "route_ids": [route["route_id"] for route in routes]},
        "replay": {"verified": all_verified, "routes": {route["route_id"]: route["replay"] for route in routes}},
        "agent": {"verified": all(route["agent"]["verified"] for route in routes), "routes": {route["route_id"]: route["agent"] for route in routes}},
        "strong_coupling": {"verified": all(route["strong_coupling"]["verified"] for route in routes), "routes": {route["route_id"]: route["strong_coupling"] for route in routes}},
        "mechanism": {"kind": "independent strong coupling", "process": "native CityLearn, WNTR, Modelica, EV2Gym, and EnergyPlus routes with shared resources"},
        "provenance_refs": all_refs,
        "boundary": {"evidence_basis": "each D3 route is gated independently; route status and summary counts are explicit", "pending_routes_do_not_inherit": True, "does_not_claim": ["one passing route certifies all routes", "production readiness", "cross-platform equivalence"]},
        "coupling_routes": routes,
        "routes": routes,
    }


def build_catalog() -> dict[str, Any]:
    entries = [d0_entry(), d1_entry(), d2_entry(), d3_entry()]
    return {
        "schema_version": "dynamic-mechanism-catalog-v1",
        "catalog_scope": "independent D0-D3 backend mechanism index",
        "fail_closed_rule": "missing/false/stale evidence yields EVIDENCE_PENDING and never BACKEND_REPLAY_VERIFIED",
        "mechanisms": entries,
        "summary": {
            "mechanism_count": len(entries),
            "backend_replay_verified_count": sum(e["mechanism_status"] == BACKEND_REPLAY_VERIFIED for e in entries),
            "evidence_pending_count": sum(e["mechanism_status"] == EVIDENCE_PENDING for e in entries),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        payload = json.dumps(build_catalog(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    except DynamicCatalogError as exc:
        raise SystemExit(f"dynamic catalog FAIL-CLOSED: {exc}") from exc
    if args.check:
        if not args.output.is_file() or args.output.read_text(encoding="utf-8") != payload:
            raise SystemExit(f"stale dynamic catalog: {args.output}")
        return
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + ".part")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({"output": str(args.output), **build_catalog()["summary"]}, sort_keys=True))


if __name__ == "__main__":
    main()
