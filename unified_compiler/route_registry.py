"""Single source of truth for the public V11 backend route contract.

This registry describes the public envelope only.  Native action schemas remain
route specific and are returned by ``legal_actions()``.  The table is kept
dependency free so inventory and fail-closed checks work even when a solver is
not installed.
"""
from __future__ import annotations

from copy import deepcopy

ROUTE_METADATA = {
    "d0_exogenous_context": {"layer": "D0", "cadence_seconds": 60.0, "horizon_seconds": 720.0, "native_time_unit": "seconds", "execution_mode": "in_process"},
    "d1_sustaingym_fault": {"layer": "D1", "cadence_seconds": 300.0, "horizon_seconds": 86400.0, "horizon_policy": "native_288_steps", "native_time_unit": "hours", "execution_mode": "process_isolated"},
    "d1_citylearn_battery_fault": {"layer": "D1", "cadence_seconds": 3600.0, "horizon_seconds": 28800.0, "horizon_policy": "native_supported_horizon_measured", "native_time_unit": "hours", "execution_mode": "process_isolated"},
    "d1_ev2gym_fault": {"layer": "D1", "cadence_seconds": 900.0, "horizon_seconds": 38700.0, "horizon_policy": "native_supported_horizon_measured", "native_time_unit": "seconds", "execution_mode": "process_isolated"},
    "d1_discrete_device_fault": {"layer": "D1", "cadence_seconds": 60.0, "horizon_seconds": 600.0, "native_time_unit": "seconds", "execution_mode": "process_isolated"},
    "energyplus_iaq": {"layer": "D2", "cadence_seconds": 600.0, "accepted_dt_seconds": [600.0, 1200.0, 1800.0], "horizon_seconds": 172200.0, "horizon_policy": "native_supported_horizon_measured", "native_time_unit": "seconds", "termination": "native_or_172200s_truncation", "execution_mode": "process_isolated"},
    "wntr_residential_water": {"layer": "D2", "cadence_seconds": 3600.0, "accepted_dt_seconds": [3600.0], "horizon_seconds": 21600.0, "native_time_unit": "seconds", "termination": "native_or_21600s_truncation", "execution_mode": "process_isolated"},
    "fds_smoke_fire": {"layer": "D2", "cadence_seconds": 10.0, "accepted_dt_seconds": [1.0, 10.0], "horizon_seconds": 60.0, "native_time_unit": "seconds", "termination": "native_or_60s_truncation", "execution_mode": "process_isolated", "continuation": "prefix_replay", "online_step_supported": False, "capability_note": "real FDS prefix replay; synchronous native online continuation is unverified"},
    "modelica_buildings_aixlib": {"layer": "D2", "cadence_seconds": 60.0, "accepted_dt_seconds": [60.0], "horizon_seconds": 3600.0, "native_time_unit": "seconds", "termination": "native_or_3600s_truncation", "execution_mode": "process_isolated"},
    "d3_citylearn_multi_system": {"layer": "D3", "cadence_seconds": 3600.0, "horizon_seconds": 86400.0, "native_time_unit": "hours", "execution_mode": "process_isolated", "episode_generation_supported": True, "d3_coupling_supported": False, "capability_note": "independent native battery/HVAC responses; cross-channel coupling unproven"},
    "d3_citylearn_multibuilding_competition": {"layer": "D3", "cadence_seconds": 3600.0, "horizon_seconds": 86400.0, "native_time_unit": "hours", "execution_mode": "process_isolated"},
    "d3_wntr_water_competition": {"layer": "D3", "cadence_seconds": 3600.0, "accepted_dt_seconds": [3600.0], "horizon_seconds": 21600.0, "native_time_unit": "seconds", "termination": "native_or_21600s_truncation", "execution_mode": "process_isolated"},
    "d3_modelica_shared_heat": {"layer": "D3", "cadence_seconds": 60.0, "horizon_seconds": 3600.0, "native_time_unit": "seconds", "execution_mode": "process_isolated"},
    "d3_ev2gym_electric_competition": {"layer": "D3", "cadence_seconds": 900.0, "horizon_seconds": 86400.0, "native_time_unit": "seconds", "execution_mode": "process_isolated"},
    "d3_energyplus_shared_ventilation": {"layer": "D3", "cadence_seconds": 900.0, "accepted_dt_seconds": [900.0], "horizon_seconds": 20700.0, "horizon_policy": "native_elapsed_window_measured", "native_time_unit": "seconds", "termination": "native_or_20700s_elapsed_window", "execution_mode": "process_isolated"},
}

ROUTE_ALIASES = {
    "citylearn_multi_system": "d3_citylearn_multi_system",
    "d3_citylearn_coupling": "d3_citylearn_multi_system",
    "citylearn_multibuilding_competition": "d3_citylearn_multibuilding_competition",
}

PUBLIC_ROUTE_IDS = tuple(ROUTE_METADATA)


def canonical_route_id(route_id: str) -> str:
    return ROUTE_ALIASES.get(route_id, route_id)


def route_metadata(route_id: str) -> dict:
    canonical = canonical_route_id(route_id)
    if canonical not in ROUTE_METADATA:
        raise KeyError(f"unknown public route: {route_id}")
    return deepcopy({"route_id": canonical, **ROUTE_METADATA[canonical]})


def inventory() -> list[dict]:
    return [route_metadata(route_id) for route_id in PUBLIC_ROUTE_IDS]


__all__ = ["PUBLIC_ROUTE_IDS", "ROUTE_ALIASES", "ROUTE_METADATA", "canonical_route_id", "inventory", "route_metadata"]
