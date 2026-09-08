"""Build the frozen responsibility-to-backend route and operational Contracts."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from harness_v2.workflow_scenario_registry import WORKFLOW_SCENARIO_REGISTRY, match_current_backend

ROOT = Path(__file__).resolve().parent
CATALOG = ROOT / "responsibility_ai_coding_v1/NATURAL_STANDING_INTENT_QUERY_CATALOG_V2_5.json"
OUTPUT = ROOT / "generated/formal_responsibility_contracts_v1.json"


THERMAL_IDS = [
    "rd_08ee0b241675", "rd_37104b57370a", "rd_5677579cc63f", "rd_80682ef0d394",
    "rd_94d666a58c83", "rd_split_016151c7c038", "rd_split_39d32cc7fd22",
    "rd_split_42d866d7dbea", "rd_split_b8457e559b4d", "rd_split_be2cdd7acdff",
]
AIR_IDS = ["rd_54bdb4d1d881", "rd_670cf48458c9", "rd_b5071d572e3d", "rd_ebd968ad40ce"]
LIGHTING_IDS = [
    "rd_45f013e7ba1d", "rd_b29cce0b1017", "rd_b62dd368cce0", "rd_bc53f8b79068",
    "rd_cf2389fcc9f1", "rd_split_4053e031415b", "rd_split_57dc1a5ccd89",
    "rd_split_5c776896ad46", "rd_split_7b8e77e75605", "rd_split_c828da9fc975",
]
WORKFLOW_SCENARIOS = {
    "rd_05920f8dade6": "laundry_completion_notification",
    "rd_split_94be9427a8c4": "laundry_backlog_management",
    "rd_9fa396d3191a": "weekly_floor_cleaning",
    "rd_4c9f419a786a": "away_intercom_notification",
    "rd_6010f46a03e0": "mail_arrival_notification",
    "rd_782fea3cbba2": "away_visitor_monitoring",
    "rd_fd30c82cb52f": "unoccupied_visitor_acknowledgement",
    "rd_099294a66240": "fridge_door_left_open",
    "rd_1783c33e2747": "unattended_stove_guard",
    "rd_49465c88e9af": "departure_lockdown",
    "rd_5dd0c995ea28": "unoccupied_home_security",
    "rd_89b104a21351": "failed_entry_notification",
    "rd_8b7dca2e69c7": "expected_delivery_gate_and_notice",
    "rd_af2688f9787c": "remote_visitor_gate_access",
    "rd_split_272d269ff0c7": "post_parking_garage_closure",
    "rd_split_a525cb032b19": "bathroom_occupancy_indicator",
    "rd_05eb866a5fcf": "television_curfew",
    "rd_06f5ac8a120d": "keyless_resident_entry",
    "rd_5e0e9919d03e": "full_bin_collection_notice",
    "rd_7f75346b5a7f": "departure_key_reminder",
    "rd_ebe806fd446f": "intended_wake_alarm",
    "rd_5d75d19a4827": "shower_music_availability",
    "rd_bd6dfd6a2c77": "pellet_supply_guard",
    "rd_a07aecceb886": "beer_dispenser_stock",
    "rd_f540da710bc1": "coffee_ready_at_wake",
    "rd_f61b22e412c4": "post_use_toilet_flush",
    "rd_fcd3c198a00d": "toilet_paper_depletion_notice",
    "rd_split_137db9864548": "shower_news_delivery",
    "rd_split_44332c24ebca": "supported_emergency_call",
    "rd_6debc9157765": "authorized_vehicle_gate_entry",
}


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _contract(route: str, source: dict[str, Any]) -> dict[str, Any]:
    lifecycle = source["lifecycle"]
    common = {
        "lifecycle": lifecycle,
        "beneficiary": source["beneficiary"],
        "delegated_outcome": source["delegated_outcome"],
        "authorization": {"mode": "public_profile", "implicit_defaults_forbidden": True},
        "release_condition_required": lifecycle in {"MAINTAIN", "GUARD"},
    }
    if route == "energyplus_thermal":
        return {**common,
            "required_observations": ["time", "zone_temperature_c", "outdoor_temperature_c", "occupancy_or_public_activity_event", "hvac_effective_state"],
            "required_actions": ["thermal_control.set_mode_and_setpoint"],
            "required_dynamics": ["weather_forcing", "building_envelope_heat_transfer", "internal_gains", "hvac_heat_transfer"],
            "required_events": ["public_activation", "public_release"],
            "required_evaluator_primitives": ["active_temperature_band", "release_compliance", "simulated_hvac_energy"],
            "resource_semantics": {"metric": "simulated_hvac_energy_kwh", "claim": "EnergyPlus simulated energy"},
        }
    if route == "energyplus_air":
        return {**common,
            "required_observations": ["time", "zone_relative_humidity_pct", "zone_temperature_c", "outdoor_conditions", "opening_factor", "public_activity_event"],
            "required_actions": ["ventilation_opening.set_fraction"],
            "required_dynamics": ["airflow_network", "moisture_balance", "weather_forcing"],
            "required_events": ["public_activation", "public_release"],
            "required_evaluator_primitives": ["humidity_or_air_band", "release_compliance", "simulated_thermal_side_effect"],
            "resource_semantics": {"metric": "simulated_thermal_side_effect_kwh", "claim": "EnergyPlus simulated quantity"},
        }
    if route == "energyplus_lighting":
        return {**common,
            "required_observations": ["time", "occupancy_or_public_activity_event", "daylight_or_solar_condition", "illuminance_lux", "lighting_power_w", "shade_state"],
            "required_actions": ["lighting.set_fraction", "shading.set_position"],
            "required_dynamics": ["daylight", "electric_lighting", "solar_shading"],
            "required_events": ["public_activation", "public_release"],
            "required_evaluator_primitives": ["task_illuminance_or_privacy", "release_compliance", "simulated_lighting_energy"],
            "resource_semantics": {"metric": "simulated_lighting_energy_kwh", "claim": "EnergyPlus simulated energy"},
        }
    scenario = WORKFLOW_SCENARIOS[source["responsibility_id"]]
    machine = deepcopy(WORKFLOW_SCENARIO_REGISTRY[scenario])
    return {
        **common,
        **machine,
        "resource_semantics": {
            "metrics": ["task_elapsed_seconds", "device_runtime_seconds", "action_cost"],
            "claim": "workflow simulator native cost",
        },
    }


def build() -> dict[str, Any]:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    by_id = {row["responsibility_id"]: row for row in catalog["queries"]}
    selected = [(item, "energyplus_thermal") for item in THERMAL_IDS]
    selected += [(item, "energyplus_air") for item in AIR_IDS]
    selected += [(item, "energyplus_lighting") for item in LIGHTING_IDS]
    selected += [(item, "household_workflow_t2") for item in WORKFLOW_SCENARIOS]
    missing = sorted({item for item, _ in selected} - set(by_id))
    if missing:
        raise RuntimeError(f"selected responsibility IDs absent from source catalog: {missing}")
    if len({item for item, _ in selected}) != len(selected):
        raise RuntimeError("responsibility route contains duplicate IDs")
    routes = []
    for responsibility_id, route in selected:
        source = by_id[responsibility_id]
        contract = _contract(route, source)
        backend_id = {
            "energyplus_thermal": "energyplus_residential_harness_v2",
            "energyplus_air": "energyplus_afn_harness_v2",
            "energyplus_lighting": "energyplus_daylight_harness_v2",
            "household_workflow_t2": "household_workflow_harness_v2",
        }[route]
        workflow_match = match_current_backend(contract["scenario_type"]) if route == "household_workflow_t2" else None
        support_status = workflow_match["status"] if workflow_match is not None else "PARTIAL"
        routes.append({
            "responsibility_id": responsibility_id,
            "source_standing_intent_id": source["standing_intent_id"],
            "source_query": source["natural_query"],
            "source_evidence_ids": source["source_evidence_ids"],
            "family": source["family"],
            "backend_route": route,
            "backend_id": backend_id,
            "backend_fidelity_tier": "T2" if route == "household_workflow_t2" else "T1",
            "contract_status": "MACHINE_CONTRACT_V2" if workflow_match is not None else "FROZEN_PENDING_EXECUTABLE_EVIDENCE",
            "support_status": support_status,
            "capability_match": workflow_match,
            "contract": contract,
            "contract_digest": _sha(contract),
        })
    routes.sort(key=lambda row: row["responsibility_id"])
    return {
        "schema_version": "formal-responsibility-contracts-v1",
        "source_catalog": str(CATALOG.relative_to(ROOT)),
        "source_catalog_sha256": hashlib.sha256(CATALOG.read_bytes()).hexdigest(),
        "responsibility_count": len(routes),
        "route_counts": {
            route: sum(item["backend_route"] == route for item in routes)
            for route in sorted({item["backend_route"] for item in routes})
        },
        "routes": routes,
    }


def main() -> None:
    result = build()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(OUTPUT), "responsibility_count": result["responsibility_count"], "route_counts": result["route_counts"]}, indent=2))


if __name__ == "__main__":
    main()
