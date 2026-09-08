from __future__ import annotations

from pathlib import Path

import pytest

from d3_citylearn_coupling_adapter import (
    DEFAULT_BUILDING,
    DEFAULT_HORIZON,
    DEFAULT_START,
    D3CouplingError,
    D3CoupledCityLearnAdapter,
    PROVIDES,
    probe_coupling,
)
from unified_compiler.types import PhysicalTopology, ProcessRequirement, ResponsibilityLifecycle


@pytest.fixture(scope="module")
def evidence() -> dict:
    return probe_coupling(DEFAULT_BUILDING, DEFAULT_START, DEFAULT_HORIZON)


def test_one_native_complete_episode_and_exact_boundary(evidence: dict) -> None:
    assert evidence["passed"] is True
    assert evidence["single_native_episode"] is True
    baseline = evidence["baseline"]
    assert len(baseline["records"]) == DEFAULT_HORIZON
    assert [r["source_row"] for r in baseline["records"]] == list(range(DEFAULT_START, DEFAULT_START + DEFAULT_HORIZON))
    assert all(not r["backend_terminated"] for r in baseline["records"][:-1])
    assert baseline["records"][-1]["backend_terminated"] is True


def test_battery_hvac_actions_are_causally_sensitive(evidence: dict) -> None:
    assert evidence["battery_soc_delta"] > 1e-9
    assert evidence["net_electricity_delta"] > 1e-9
    assert evidence["hvac_temperature_delta_c"] > 1e-9


def test_net_electricity_is_coupled_to_all_native_components(evidence: dict) -> None:
    assert evidence["net_energy_balance_error_kwh"] <= 1e-5
    for row in evidence["baseline"]["records"]:
        effect = row["effect"]
        expected = (
            effect["non_shiftable_load_kwh"]
            + effect["hvac_electricity_kwh"]
            + effect["dhw_electricity_kwh"]
            + effect["storage_electricity_kwh"]
            - effect["solar_generation_kwh"]
        )
        assert abs(effect["net_electricity_kwh"] - expected) <= 1e-5


def test_replay_is_deterministic_and_provenance_is_pinned(evidence: dict) -> None:
    baseline = evidence["baseline"]
    assert evidence["deterministic_replay"] is True
    provenance = baseline["provenance"]
    assert provenance["citylearn_version"] == "2.5.0"
    assert provenance["citylearn_tag_commit"] == "29062af6d077409e1c37a3e53a6cac30fd4d02bc"
    for key, value in provenance.items():
        if key.endswith("sha256"):
            assert isinstance(value, str) and len(value) == 64


def test_exogenous_conditions_change_strategy_conditions(evidence: dict) -> None:
    contrast = evidence["exogenous_condition_contrast"]
    assert contrast["strategy_conditions_changed"] is True
    assert set(contrast["deltas"]) == {
        "weather_outdoor_temperature_c", "occupancy_count",
        "non_shiftable_load_kwh", "pv_generation_kwh",
    }
    assert all(value > 1e-9 for value in contrast["deltas"].values())


def test_typed_adapter_exposes_only_verified_coupled_route(evidence: dict) -> None:
    adapter = D3CoupledCityLearnAdapter(
        building_id=DEFAULT_BUILDING, start=DEFAULT_START, horizon=DEFAULT_HORIZON
    )
    capability = adapter.capabilities()[0]
    assert capability.status.value == "EXECUTABLE_REPLAY_VERIFIED"
    assert set(capability.provides) == PROVIDES
    requirement = ProcessRequirement(
        requirement_id="d3",
        responsibility_lifecycle=ResponsibilityLifecycle.MAINTAIN,
        physical_topology=PhysicalTopology.THERMAL_DYNAMICS,
        required_capabilities=PROVIDES,
        state_variables=(),
        action_types=(),
    )
    processes = adapter.scan([requirement])
    assert len(processes) == 1
    assert processes[0].manifest["single_native_episode"] is True
    assert processes[0].source_hash.startswith("sha256:")


def test_fail_closed_on_invalid_window() -> None:
    with pytest.raises(D3CouplingError):
        probe_coupling(DEFAULT_BUILDING, 0, DEFAULT_HORIZON)
