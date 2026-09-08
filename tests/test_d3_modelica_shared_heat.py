from __future__ import annotations

import pytest

from d3_modelica_shared_heat_adapter import (
    ACTION_NAMES,
    D3ModelicaActionError,
    D3ModelicaSharedHeatRoute,
    probe_d3_modelica,
    probe_runtime_d3,
)


@pytest.fixture()
def route():
    instance = D3ModelicaSharedHeatRoute(horizon_seconds=180)
    try:
        yield instance
    finally:
        instance.close()


def test_real_fmu_runtime_is_pinned_and_native():
    evidence = probe_runtime_d3()
    assert evidence["available"] is True
    assert evidence["surrogate_model_used"] is False
    assert evidence["blockers"] == []


def test_agent_contract_uses_two_native_action_channels(route):
    initial = route.reset(seed=9)
    assert set(initial) >= {"room_a_temperature_c", "dhw_temperature_c"}
    legal = route.legal_actions()
    assert legal["native"] is True
    assert set(legal["channels"]) == set(ACTION_NAMES)
    transition = route.step({"space_heating_request": 1.0, "dhw_request": 1.0})
    assert transition["info"]["native_fmi_do_step"] is True
    assert transition["time_seconds"] == 60.0


def test_shared_capacity_is_bidirectionally_action_sensitive(route):
    route.reset(seed=0)
    combined = route.step({"space_heating_request": 1.0, "dhw_request": 1.0})["observation"]
    route.reset(seed=0)
    space_only = route.step({"space_heating_request": 1.0, "dhw_request": 0.0})["observation"]
    route.reset(seed=0)
    dhw_only = route.step({"space_heating_request": 0.0, "dhw_request": 1.0})["observation"]
    assert combined["service_shortfall_w"] > 0.0
    assert combined["allocated_space_heat_w"] < space_only["allocated_space_heat_w"]
    assert combined["allocated_dhw_heat_w"] < dhw_only["allocated_dhw_heat_w"]


def test_reset_is_deterministic_and_step_is_persistent(route):
    first = route.reset(seed=4)
    a = route.step({"space_heating_request": 0.2, "dhw_request": 0.8})
    b = route.step({"space_heating_request": 0.8, "dhw_request": 0.2})
    assert b["time_seconds"] > a["time_seconds"]
    assert route.observe() == b["observation"]
    route.reset(seed=4)
    assert route.observe() == first
    assert route.step({"space_heating_request": 0.2, "dhw_request": 0.8}) == a


@pytest.mark.parametrize(
    "bad",
    [{}, {"space_heating_request": 0.0}, {"space_heating_request": 0.0, "dhw_request": 2.0},
     {"space_heating_request": float("nan"), "dhw_request": 0.0}, [0.0, 0.0]],
)
def test_illegal_actions_do_not_advance(route, bad):
    route.reset()
    before = route.session.time
    with pytest.raises(D3ModelicaActionError):
        route.step(bad)
    assert route.session.time == before


def test_probe_reports_native_causal_evidence():
    report = probe_d3_modelica(steps=3)
    assert report["passed"] is True
    assert report["native_fmi_do_step"] is True
    assert report["shared_capacity_gate"] is True
