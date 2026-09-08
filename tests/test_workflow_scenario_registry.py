from __future__ import annotations

from copy import deepcopy

import pytest

from harness_v2.workflow_scenario_registry import (
    CURRENT_WORKFLOW_BACKEND_CAPABILITIES,
    REQUIRED_SPEC_FIELDS,
    WORKFLOW_SCENARIO_REGISTRY,
    match_backend,
    match_current_backend,
    validate_registry,
)


def test_registry_has_30_unique_complete_scenarios() -> None:
    validate_registry()
    assert len(WORKFLOW_SCENARIO_REGISTRY) == 30
    assert len({item["responsibility_id"] for item in WORKFLOW_SCENARIO_REGISTRY.values()}) == 30
    assert all(REQUIRED_SPEC_FIELDS <= set(item) for item in WORKFLOW_SCENARIO_REGISTRY.values())


def test_registry_validation_rejects_missing_machine_field() -> None:
    broken = deepcopy(WORKFLOW_SCENARIO_REGISTRY)
    broken["mail_arrival_notification"].pop("release")
    with pytest.raises(ValueError, match="missing fields"):
        validate_registry(broken)


def test_tv_contract_preserves_eleven_thirty_and_needs_real_clock() -> None:
    spec = WORKFLOW_SCENARIO_REGISTRY["television_curfew"]
    assert spec["activation"]["all"][0]["equals"] == "23:30"
    assert spec["query_invariants"]["curfew_local_time"] == "23:30"
    assert "local_calendar_clock" in spec["required_dynamics"]
    assert "television_curfew_ended" in spec["required_events"]
    assert match_current_backend("television_curfew")["status"] == "FULL"


def test_weekly_contract_is_recurrent_not_one_immediate_vacuum_run() -> None:
    spec = WORKFLOW_SCENARIO_REGISTRY["weekly_floor_cleaning"]
    assert spec["window"]["calendar_period"] == "week"
    assert "weekly_calendar_recurrence" in spec["required_dynamics"]
    assert "recurrence_occurrence_check" in spec["required_evaluator_primitives"]
    assert spec["minimum_horizon_seconds"] == 604800


@pytest.mark.parametrize(
    "scenario",
    [
        "away_intercom_notification",
        "away_visitor_monitoring",
        "unoccupied_visitor_acknowledgement",
        "departure_lockdown",
        "unoccupied_home_security",
        "remote_visitor_gate_access",
    ],
)
def test_away_responsibilities_require_occupancy_lifecycle(scenario: str) -> None:
    spec = WORKFLOW_SCENARIO_REGISTRY[scenario]
    assert "occupancy_lifecycle" in spec["required_dynamics"]
    assert any(item in spec["required_events"] for item in ("occupants_returned", "authorized_return"))
    assert match_current_backend(scenario)["status"] == "FULL"


def test_coffee_contract_scores_readiness_at_wake_not_terminal_state() -> None:
    spec = WORKFLOW_SCENARIO_REGISTRY["coffee_ready_at_wake"]
    assert spec["window"]["center_profile"] == "intended_wake_at"
    assert "coffee_freshness_decay" in spec["required_dynamics"]
    assert "readiness_window_check" in spec["required_evaluator_primitives"]
    assert {"intended_wake_at", "coffee_freshness_seconds"} <= set(spec["profile_requirements"])


@pytest.mark.parametrize("scenario,item", [("pellet_supply_guard", "pellets"), ("beer_dispenser_stock", "beer")])
def test_inventory_contract_requires_order_lead_time_and_delivery(scenario: str, item: str) -> None:
    spec = WORKFLOW_SCENARIO_REGISTRY[scenario]
    assert spec["authorization"]["action"] == "supply.order"
    assert {"inventory_consumption", "procurement_order", "delivery_lead_time"} <= set(spec["required_dynamics"])
    assert any(row.get("kind") == "instant_inventory_mutation" for row in spec["forbidden"])
    assert {"supply_order_accepted", "supply_delivered"} <= set(spec["required_events"])
    result = match_current_backend(scenario)
    assert result["status"] == "FULL"
    assert result["missing_requirements"] == {}


def test_authorized_vehicle_contract_has_positive_and_negative_obligations() -> None:
    spec = WORKFLOW_SCENARIO_REGISTRY["authorized_vehicle_gate_entry"]
    assert spec["authorization"]["allowlist_profile"] == "authorized_vehicle_ids"
    assert "unauthorized_vehicle_arrived" in spec["required_events"]
    assert "allowlist_negative_check" in spec["required_evaluator_primitives"]
    assert any(row.get("kind") == "gate_open_for_unauthorized_vehicle" for row in spec["forbidden"])
    assert spec["query_invariants"]["negative"] == "deny"


def test_matcher_is_fail_closed_and_can_reach_full_only_with_every_requirement() -> None:
    spec = WORKFLOW_SCENARIO_REGISTRY["authorized_vehicle_gate_entry"]
    deliberately_incomplete = deepcopy(CURRENT_WORKFLOW_BACKEND_CAPABILITIES)
    deliberately_incomplete["actions"] = set(deliberately_incomplete["actions"]) - {"garage.door"}
    partial = match_backend(spec, deliberately_incomplete)
    assert partial["status"] == "PARTIAL"
    assert partial["missing_requirements"] == {"required_actions": ["garage.door"]}

    complete = {
        "observations": set(spec["required_observations"]),
        "actions": set(spec["required_actions"]),
        "dynamics": set(spec["required_dynamics"]),
        "events": set(spec["required_events"]),
        "evaluator_primitives": set(spec["required_evaluator_primitives"]),
        "profile_requirements": set(spec["profile_requirements"]),
        "max_horizon_seconds": spec["minimum_horizon_seconds"],
    }
    assert match_backend(spec, complete) == {
        "status": "FULL",
        "responsibility_id": spec["responsibility_id"],
        "scenario_type": spec["scenario_type"],
        "missing_requirements": {},
    }


def test_unknown_scenario_is_unsupported() -> None:
    assert match_current_backend("not_a_scenario")["status"] == "UNSUPPORTED"
