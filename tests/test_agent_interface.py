"""Conformance tests for the unified Agent boundary."""

from __future__ import annotations

import pytest

from unified_compiler.agent_interface import (
    AgentActionError,
    AgentInterfaceError,
    D3_AGENT_ROUTE_ALIASES,
    D3_AGENT_ROUTE_IDS,
    make_agent_backend,
)


def test_d3_route_ids_are_stable_and_keep_citylearn_compatibility_aliases() -> None:
    assert D3_AGENT_ROUTE_IDS == (
        "d3_citylearn_multi_system",
        "d3_citylearn_multibuilding_competition",
        "d3_wntr_water_competition",
        "d3_modelica_shared_heat",
        "d3_ev2gym_electric_competition",
        "d3_energyplus_shared_ventilation",
    )
    assert D3_AGENT_ROUTE_ALIASES["citylearn_multi_system"] == "d3_citylearn_multi_system"
    assert D3_AGENT_ROUTE_ALIASES["d3_citylearn_coupling"] == "d3_citylearn_multi_system"


def test_d3_native_routes_construct_through_common_factory() -> None:
    for route_id in D3_AGENT_ROUTE_IDS:
        backend = make_agent_backend(route_id)
        backend.close()


def test_d0_agent_closed_loop_and_exogenous_schedule_boundary() -> None:
    route = make_agent_backend("d0_exogenous_context")
    initial = route.reset(seed=7)
    assert initial["time_seconds"] == 0.0
    assert route.legal_actions()["schedule_agent_modifiable"] is False
    transition = route.step({"kind": "act", "command": {"target": "interior_lights", "operation": "on"}})
    assert transition["time_seconds"] == 60.0
    assert route.observe() == transition["observation"]
    waited = route.step({"kind": "wait"})
    assert waited["time_seconds"] == 120.0
    assert waited["observation"]["devices"]["interior_lights"] == "on"


def test_harness_d1_agent_uses_public_view_and_real_workflow_backend() -> None:
    route = make_agent_backend("d1_discrete_device_fault")
    route.reset(seed=11)
    assert route.legal_actions()["type"] == "harness_agent_action"
    transition = route.step({"kind": "act", "commands": []})
    assert transition["time_seconds"] == 60.0
    assert route.observe() == transition["observation"]
    assert transition["info"]["accepted"] is True


def test_d3_citylearn_route_is_exposed_and_unknown_route_fails_closed() -> None:
    route = make_agent_backend("d3_citylearn_coupling", horizon=2)
    initial = route.reset(seed=5)
    assert initial["time_seconds"] == 0.0
    actions = route.legal_actions()
    assert actions["native"] is True
    assert actions["native_action_names"] == [
        "electrical_storage", "cooling_or_heating_device"
    ]
    assert actions["public_action_names"] == ["battery_rate", "hvac_rate"]
    first = route.step({"battery_rate": 0.0, "hvac_rate": 0.0}, 3600.0)
    assert first["time_seconds"] == 3600.0
    assert route.observe() == first["observation"]
    route.close()
    with pytest.raises(KeyError):
        make_agent_backend("not_a_real_route")
