"""Authoritative catalog of the first-wave claim-aligned backends.

Exactly four backends are cataloged, each with its distinct route status,
claim-gate semantics, and current blockers:

- ``EV2Gym`` (EV charging): ``EXECUTABLE_REPLAY_VERIFIED``.
- ``CityLearn`` (two routes): battery/PV and native HVAC
  ``EXECUTABLE_REPLAY_VERIFIED``.
- ``SustainGym`` (RC building): ``EXECUTABLE_REPLAY_VERIFIED`` for bounded
  cooling.
- ``BOPTEST`` (twozone apartment): ``API_VERIFIED_NOT_DATA_PROBED``.

The catalog is plain, machine-readable data. Constructors are exposed only as
a lazy mapping (:data:`LAZY_CONSTRUCTORS`) that imports the adapter module on
first call, so importing this module never instantiates a runtime and never
imports optional heavy backend packages. Building the catalog JSON
(``build_claim_backend_catalog.py``) never calls a constructor.

Status claims are not production readiness. ``release_ready`` denotes
claim-route admission after the route's evidence gate, not production
deployment or service-SLA readiness. BOPTEST remains explicitly NOT
release-ready (see its per-route ``blockers``). The T2 household workflow track is explicitly control-only
(state-machine workflow episodes, no physical backend runtime) and is
deliberately absent from the primary constructor mapping.
"""

from __future__ import annotations

import importlib
from copy import deepcopy
from typing import Any, Callable, Mapping

CATALOG_ID = "claim_backend_catalog_v1"

EV2GYM_BACKEND = "ev2gym_claim"
CITYLEARN_BACKEND = "citylearn_claim"
SUSTAINGYM_BACKEND = "sustaingym_building"
BOPTEST_BACKEND = "boptest_rest"

STATUS_EXECUTABLE_REPLAY_VERIFIED = "EXECUTABLE_REPLAY_VERIFIED"
STATUS_LEGACY_EXECUTABLE_PENDING_MIGRATION = "LEGACY_EXECUTABLE_PENDING_MIGRATION"
STATUS_DATA_PROBED_PENDING_REPLAY = "DATA_PROBED_PENDING_REPLAY"
STATUS_API_VERIFIED_NOT_DATA_PROBED = "API_VERIFIED_NOT_DATA_PROBED"

BACKENDS: tuple[Mapping[str, Any], ...] = (
    {
        "backend_id": EV2GYM_BACKEND,
        "backend_label": "EV2Gym",
        "domain": "ev_charging",
        "adapter_module": "unified_compiler.adapters.ev2gym_claim",
        "adapter_class": "EV2GymClaimAdapter",
        "error_class": "EV2GymClaimError",
        "routes": [
            {
                "route_id": "ev2gym_ev_pinned_replay",
                "subsystem": "ev_charging",
                "capabilities": ("ev.soc", "ev.charge_action"),
                "status": STATUS_EXECUTABLE_REPLAY_VERIFIED,
                "claim_gate": {
                    "semantics": (
                        "EXECUTABLE_REPLAY_VERIFIED may only be reported after the "
                        "adapter's pinned verification passes; any miss raises "
                        "EV2GymClaimError (fail closed), never a downgrade"
                    ),
                    "required_checks": (
                        "frozen diversity_pilot_v1 public/private artifact pairs load "
                        "and are mutually consistent",
                        "every EV2Gym binding carries valid pinned source/config "
                        "digests (config_sha256, load_source_sha256, price_source_sha256)",
                        "one consistent EV2Gym commit pinned across all processes",
                        "pinned checkout HEAD equals the binding commit",
                        "pinned runtime venv interpreter exists",
                        "probe replay succeeds: fresh runtime + observe + one legal "
                        "WAIT step, with exogenous observation keys present and no "
                        "evaluator-label keys leaked",
                    ),
                    "evidence_refs": (
                        "prototypes/v10_diversity_aware_compiler/generated/diversity_pilot_v1/",
                        "prototypes/v10_diversity_aware_compiler/.runtime/ev2gym",
                        "prototypes/v10_diversity_aware_compiler/.runtime/venv/bin/python",
                    ),
                },
                "blockers": (),
                "release_ready": True,
            }
        ],
    },
    {
        "backend_id": CITYLEARN_BACKEND,
        "backend_label": "CityLearn",
        "domain": "building_energy",
        "adapter_module": "unified_compiler.adapters.citylearn_claim",
        "adapter_class": "CityLearnClaimAdapter",
        "error_class": "ClaimEpisodeError",
        "routes": [
            {
                "route_id": "citylearn_battery_pv_replay_verified",
                "subsystem": "battery_pv",
                "capabilities": (
                    "storage.soc",
                    "storage.charge_discharge_action",
                    "pv.generation_profile",
                ),
                "status": STATUS_EXECUTABLE_REPLAY_VERIFIED,
                "claim_gate": {
                    "semantics": (
                        "battery/PV status is EXECUTABLE_REPLAY_VERIFIED via the "
                        "replay-gate-verified process pool; the episode route lazily "
                        "reuses the exact probe/replay helpers the verified replay "
                        "gate ran (CityLearn's own device code, one consumption "
                        "record per step, reset never double-counted)"
                    ),
                    "required_checks": (
                        "verified battery/PV process pool artifact loads and is "
                        "consistent (BatteryPoolError otherwise)",
                        "verified replay gate artifact present",
                        "CityLearn battery assets available to the probe layer",
                    ),
                    "evidence_refs": (
                        "generated/battery_pv_process_pool.json",
                        "generated/battery_pv_process_replay_gate.json",
                        "probe_citylearn_battery_replay.py",
                    ),
                },
                "blockers": (),
                "release_ready": True,
            },
            {
                "route_id": "citylearn_hvac_native_replay_verified",
                "subsystem": "hvac",
                "capabilities": ("thermal.zone_temperature", "thermal.hvac_action"),
                "status": STATUS_EXECUTABLE_REPLAY_VERIFIED,
                "claim_gate": {
                    "semantics": (
                        "HVAC is EXECUTABLE_REPLAY_VERIFIED: a native v11 "
                        "episode facade drives the pinned CityLearn 2.5.0 runtime "
                        "through a complete episode; frozen v10 records supply "
                        "data bindings only and V10RuntimeBridge is not used"
                    ),
                    "required_checks": (
                        "frozen v10 public/private artifacts resolve as data and "
                        "the binding window matches the scanned process",
                        "native CityLearn runtime completes the pinned full episode "
                        "with source-row alignment and a finite terminal effect",
                        "pinned CityLearn runtime reports version 2.5.x",
                        "SHA-256 provenance gate verifies the source schema, source "
                        "CSV, thermal model, runtime bootstrap, HVAC probe, and "
                        "episode runtime against the pinned manifests",
                        "native HVAC replay does not import or execute the frozen "
                        "v10 runtime chain",
                    ),
                    "evidence_refs": (
                        "tests/test_citylearn_claim_native_hvac.py::test_native_hvac_full_episode_on_pinned_runtime",
                        "tests/test_citylearn_claim_native_hvac.py::test_native_hvac_rejects_changed_pinned_data_assets",
                        "tests/test_citylearn_claim_native_hvac.py::test_native_hvac_rejects_changed_runtime_asset",
                        "unified_compiler/adapters/citylearn_claim.py",
                        "prototypes/v5_scenario_compiler/generated/source_manifest.json",
                        "prototypes/v5_scenario_compiler/generated/production_v1/run_manifest.json",
                    ),
                },
                "blockers": (),
                "release_ready": True,
            },
        ],
        "fail_closed_routes": {
            "dhw_storage": "out of scope: no capabilities, no processes, "
            "open_episode refuses (a dhw_demand column never implies a "
            "controllable DHW tank)",
            "thermal_storage": "out of scope: no capabilities, no processes, "
            "open_episode refuses",
        },
    },
    {
        "backend_id": SUSTAINGYM_BACKEND,
        "backend_label": "SustainGym",
        "domain": "hvac",
        "adapter_module": "unified_compiler.adapters.sustaingym_building",
        "adapter_class": "SustainGymBuildingAdapter",
        "error_class": "SustainGymAdapterError",
        "routes": [
            {
                "route_id": "sustaingym_bounded_cooling_replay_verified",
                "subsystem": "hvac_cooling",
                "capabilities": (
                    "thermal.zone_temperature",
                    "thermal.hvac_cooling_action",
                    "weather.exogenous",
                    "occupancy.exogenous",
                ),
                "status": STATUS_EXECUTABLE_REPLAY_VERIFIED,
                "claim_gate": {
                    "semantics": (
                        "EXECUTABLE_REPLAY_VERIFIED for the bounded cooling route: "
                        "the generated replay gate is verified and completes four "
                        "deterministic 288-step replays; the public action surface "
                        "remains limited to the probed cooling envelope [-0.05, 0.0] "
                        "per zone (raw [-1, 1] space is never exposed; heating is "
                        "unverified opt-in and never claimed)"
                    ),
                    "required_checks": (
                        "generated replay gate is present with verified=true and "
                        "status EXECUTABLE_REPLAY_VERIFIED",
                        "four bounded cooling replays (cooling_a, cooling_b, off_a, "
                        "off_b) complete at 288 steps and are deterministic",
                        "all replay temperatures are finite and physically plausible "
                        "and the action-sensitive contrast passes",
                        "pinned BuildingEnv commit is "
                        "eac2a4d5ce4ccf44b13c290edac72532c68e30f5",
                        "public action envelope remains [-0.05, 0.0] and heating "
                        "is not claimed",
                    ),
                    "evidence_refs": (
                        "generated/sustaingym_replay_gate_v1.json",
                        "backend-survey/results/sustaingym.json",
                        "backend-survey/probes/sustaingym_building_probe.py",
                    ),
                },
                "blockers": (
                    "heating envelope lacks causal probe evidence (opt-in only, "
                    "never claimed)",
                    "bounded cooling route evidence does not claim production "
                    "deployment readiness",
                ),
                "release_ready": True,
            }
        ],
    },
    {
        "backend_id": BOPTEST_BACKEND,
        "backend_label": "BOPTEST",
        "domain": "residential_thermal",
        "adapter_module": "unified_compiler.adapters.boptest",
        "adapter_class": "BopTestRestAdapter",
        "error_class": "BopTestRestError",
        "routes": [
            {
                "route_id": "boptest_rest_api_verified",
                "subsystem": "twozone_apartment_hydronic",
                "capabilities": (
                    "zone_thermal_dynamics",
                    "weather",
                    "occupancy_schedule",
                    "energy_price_signal",
                    "hvac_control_action",
                ),
                "status": STATUS_API_VERIFIED_NOT_DATA_PROBED,
                "claim_gate": {
                    "semantics": (
                        "API_VERIFIED_NOT_DATA_PROBED: the BOPTEST REST contract "
                        "(advance / initialize / scenario / measurements / inputs / "
                        "forecast) is verified by API inspection only; no live "
                        "service has been probed and no data has been replayed"
                    ),
                    "required_checks": (
                        "REST contract inspected against the pinned project1-boptest "
                        "commit 0f8a467cb1823f005b6512937e9333c65e1e483e",
                        "fail-closed transport/schema validation on every response",
                    ),
                    "evidence_refs": (
                        "backend-survey/BACKEND_MATRIX.md#boptest--b",
                        "backend-survey/CLAIM_BACKEND_SHORTLIST_V1.md",
                    ),
                },
                "blockers": (
                    "no live BOPTEST service probed; the bundled FMU is a Linux "
                    "binary and no service is runnable on this machine",
                    "scan emits no PhysicalProcess unless the caller explicitly "
                    "supplies a replay-verified manifest",
                    "not release-ready: release readiness is NOT claimed",
                ),
                "release_ready": False,
            }
        ],
    },
)

T2_WORKFLOW = {
    "track_id": "household_workflow_t2",
    "kind": "control_only_workflow",
    "semantics": (
        "The T2 household workflow track is explicitly control-only: "
        "state-machine workflow episodes with no physical backend runtime, no "
        "backend probing, and no physical-process scan participation"
    ),
    "primary_constructor": None,
    "note": (
        "Deliberately absent from LAZY_CONSTRUCTORS and from every backend's "
        "primary route; T2 episodes never compile against these backends"
    ),
}

_BACKEND_BY_ID: dict[str, Mapping[str, Any]] = {
    entry["backend_id"]: entry for entry in BACKENDS
}


def _import_adapter_class(backend_id: str) -> type:
    """Import the adapter class lazily; never called at catalog-build time."""
    entry = _BACKEND_BY_ID[backend_id]
    module = importlib.import_module(entry["adapter_module"])
    return getattr(module, entry["adapter_class"])


def _ev2gym_constructor() -> Any:
    return _import_adapter_class(EV2GYM_BACKEND)()


def _citylearn_constructor() -> Any:
    return _import_adapter_class(CITYLEARN_BACKEND)()


def _sustaingym_constructor() -> Any:
    return _import_adapter_class(SUSTAINGYM_BACKEND)()


def _boptest_constructor() -> Any:
    return _import_adapter_class(BOPTEST_BACKEND)()


LAZY_CONSTRUCTORS: Mapping[str, Callable[[], Any]] = {
    EV2GYM_BACKEND: _ev2gym_constructor,
    CITYLEARN_BACKEND: _citylearn_constructor,
    SUSTAINGYM_BACKEND: _sustaingym_constructor,
    BOPTEST_BACKEND: _boptest_constructor,
}

CONSTRUCTOR_BACKEND_IDS: tuple[str, ...] = tuple(LAZY_CONSTRUCTORS)


def catalog_document() -> dict[str, Any]:
    """Return the machine-readable catalog as plain, JSON-serializable data.

    Deterministic: no timestamps, no probing, no constructor invocation.
    """
    return {
        "catalog_id": CATALOG_ID,
        "description": (
            "Authoritative catalog of the first-wave claim-aligned backends "
            "with distinct per-route capability statuses, claim-gate "
            "semantics, and blockers"
        ),
        "backends": [deepcopy(dict(entry)) for entry in BACKENDS],
        "t2_workflow": deepcopy(T2_WORKFLOW),
        "constructor_backend_ids": list(CONSTRUCTOR_BACKEND_IDS),
    }
