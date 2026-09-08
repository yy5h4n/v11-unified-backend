"""Real CityLearn 2.5 multi-building shared-meter D3 tests."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from d3_citylearn_multibuilding_adapter import (
    DEFAULT_BUILDINGS,
    DEFAULT_HORIZON,
    DEFAULT_START,
    D3CityLearnMultiBuildingAgentRoute,
    D3MultiBuildingActionError,
    D3MultiBuildingAdapter,
    D3MultiBuildingError,
    PROVIDES,
    probe_multibuilding,
)
from unified_compiler.types import PhysicalTopology, ProcessRequirement, ResponsibilityLifecycle


@pytest.fixture(scope="module")
def evidence() -> dict:
    return probe_multibuilding(DEFAULT_BUILDINGS, DEFAULT_START, 4, seed=37)


def _zero() -> dict[str, dict[str, float]]:
    return {building: {"battery_rate": 0.0, "hvac_rate": 0.0} for building in DEFAULT_BUILDINGS}


def test_probe_passes_real_joint_episode_and_distinguishes_capacity_semantics(evidence: dict) -> None:
    assert evidence["passed"] is True
    assert evidence["single_native_episode"] is True
    assert evidence["native_env_instances_per_trajectory"] == 1
    assert evidence["native_multi_building_transition"] is True
    gate = evidence["coupling_gate"]
    assert gate["passed"] is True
    assert gate["native_district_aggregation"] is True
    assert gate["external_shared_meter_capacity_coupling"] is True
    assert gate["native_cross_building_physical_feedback"] is False
    assert "no native transformer clipping" in gate["capacity_semantics"]


def test_cross_system_intervention_changes_other_building_headroom(evidence: dict) -> None:
    assert evidence["native_building_0_battery_soc_delta"] > 1e-9
    assert evidence["district_net_delta"] > 1e-9
    assert evidence["other_building_feasible_headroom_delta"] > 1e-9


def test_evidence_pins_adapter_probe_and_public_runtime(evidence: dict) -> None:
    provenance = evidence["provenance"]
    assert provenance["adapter_path"] == "d3_citylearn_multibuilding_adapter.py"
    assert provenance["probe_path"] == "probe_d3_citylearn_multibuilding.py"
    assert len(provenance["adapter_sha256"]) == 64
    assert len(provenance["probe_sha256"]) == 64
    runtime = provenance["public_runtime"]
    assert runtime["runtime_package"] == "citylearn"
    assert runtime["citylearn_version"] == "2.5.0"
    assert runtime["citylearn_tag_commit"] == "29062af6d077409e1c37a3e53a6cac30fd4d02bc"
    assert len(runtime["runtime_sha256"]) == 64
    assert Path(provenance["adapter_path"]).is_file()
    assert Path(provenance["probe_path"]).is_file()


def test_reset_observe_legal_actions_and_persistent_joint_env() -> None:
    route = D3CityLearnMultiBuildingAgentRoute(DEFAULT_BUILDINGS, DEFAULT_START, 2)
    try:
        first = route.reset(seed=37)
        assert route.env.time_step == 14
        assert all(math.isfinite(value) for value in first.values())
        assert all(f"{building}.net_electricity_consumption" in first for building in DEFAULT_BUILDINGS)
        assert all(f"{building}.electrical_storage_soc" in first for building in DEFAULT_BUILDINGS)
        assert all(f"{building}.indoor_dry_bulb_temperature" in first for building in DEFAULT_BUILDINGS)
        legal = route.legal_actions()
        assert legal["native"] is True
        assert legal["shape"] == [4]
        assert legal["shared_meter_constraint"]["native_clipping"] is False
        assert len(legal["channels"]) == 4
        env_identity = id(route.env)
        transition = route.step(_zero())
        assert id(route.env) == env_identity
        assert transition["info"]["single_native_episode"] is True
        assert transition["info"]["native_multi_building_transition"] is True
        assert route.observe() == transition["observation"]
        assert transition["district"]["district_net_kwh"] == pytest.approx(
            sum(transition["effects"][building]["net_electricity_kwh"] for building in DEFAULT_BUILDINGS)
        )
    finally:
        route.close()


def test_invalid_action_and_dt_do_not_advance_native_env() -> None:
    route = D3CityLearnMultiBuildingAgentRoute(DEFAULT_BUILDINGS, DEFAULT_START, 2)
    try:
        route.reset(seed=37)
        before = (route.env.time_step, route._steps, route.observe())
        bad = _zero()
        del bad[DEFAULT_BUILDINGS[1]]
        with pytest.raises(D3MultiBuildingActionError):
            route.step(bad)
        assert (route.env.time_step, route._steps, route.observe()) == before
        with pytest.raises(D3MultiBuildingActionError):
            route.step(_zero(), dt_seconds=1800)
        assert (route.env.time_step, route._steps, route.observe()) == before
    finally:
        route.close()


def test_joint_episode_terminates_at_exact_horizon() -> None:
    route = D3CityLearnMultiBuildingAgentRoute(DEFAULT_BUILDINGS, DEFAULT_START, 2)
    try:
        route.reset(seed=37)
        first, second = route.step(_zero()), route.step(_zero())
        assert first["terminated"] is False
        assert second["terminated"] is True
        with pytest.raises(D3MultiBuildingActionError):
            route.step(_zero())
    finally:
        route.close()


def test_typed_adapter_exposes_only_verified_route(evidence: dict) -> None:
    adapter = D3MultiBuildingAdapter(building_ids=DEFAULT_BUILDINGS, start=DEFAULT_START, horizon=4, seed=37)
    capability = adapter.capabilities()[0]
    assert capability.status.value == "EXECUTABLE_REPLAY_VERIFIED"
    assert set(capability.provides) == PROVIDES
    requirement = ProcessRequirement(
        requirement_id="d3-multibuilding",
        responsibility_lifecycle=ResponsibilityLifecycle.MAINTAIN,
        physical_topology=PhysicalTopology.STORAGE_DYNAMICS,
        required_capabilities=PROVIDES,
        state_variables=(),
        action_types=(),
    )
    processes = adapter.scan([requirement])
    assert len(processes) == 1
    assert processes[0].manifest["single_native_episode"] is True
    assert processes[0].manifest["external_shared_meter_constraint"]["native_clipping"] is False
    assert processes[0].source_hash.startswith("sha256:")


def test_fail_closed_on_less_than_two_buildings() -> None:
    with pytest.raises(D3MultiBuildingError):
        D3CityLearnMultiBuildingAgentRoute((DEFAULT_BUILDINGS[0],), DEFAULT_START, 2)
