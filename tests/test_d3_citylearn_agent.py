"""Live D3 Agent contract tests against the pinned CityLearn runtime."""

from __future__ import annotations

import math

import pytest

from d3_citylearn_coupling_adapter import (
    DEFAULT_BUILDING,
    DEFAULT_START,
    D3ActionError,
    D3CityLearnAgentRoute,
    OBSERVATIONS,
)


def _route(*, horizon: int = 3) -> D3CityLearnAgentRoute:
    return D3CityLearnAgentRoute(
        building_id=DEFAULT_BUILDING,
        start=DEFAULT_START,
        horizon=horizon,
    )


def test_reset_is_deterministic_and_returns_all_public_observations() -> None:
    route = _route(horizon=2)
    try:
        first = route.reset(seed=37)
        assert set(first) == set(OBSERVATIONS)
        assert all(math.isfinite(value) for value in first.values())
        assert route.env.time_step == 14
        assert route.observe() == first

        route.close()
        second = route.reset(seed=37)
        assert second == first
    finally:
        route.close()


def test_legal_actions_expose_exact_public_channels_and_native_bounds() -> None:
    route = _route(horizon=1)
    try:
        route.reset(seed=0)
        legal = route.legal_actions()
        assert legal["native"] is True
        assert set(legal["channels"]) == {"battery_rate", "hvac_rate"}
        assert all(channel["range"] == [-1.0, 1.0] for channel in legal["channels"].values())
        assert legal["native_action_names"] == [
            "electrical_storage",
            "cooling_or_heating_device",
        ]
    finally:
        route.close()


def test_live_step_is_monotone_persistent_and_observe_matches_post_action_state() -> None:
    route = _route(horizon=3)
    try:
        route.reset(seed=5)
        env_identity = id(route.env)
        transitions = [
            route.step({"battery_rate": 0.0, "hvac_rate": 0.0}),
            route.step({"battery_rate": 1.0, "hvac_rate": 1.0}),
            route.step({"battery_rate": -1.0, "hvac_rate": -1.0}),
        ]
        assert [item["time_seconds"] for item in transitions] == [3600.0, 7200.0, 10800.0]
        assert all(
            later["time_seconds"] > earlier["time_seconds"]
            for earlier, later in zip(transitions, transitions[1:])
        )
        assert all(route.observe() == transitions[-1]["observation"] for _ in range(2))
        assert id(route.env) == env_identity
        assert transitions[-1]["done"] is True
        assert transitions[-1]["terminated"] is True
    finally:
        route.close()


@pytest.mark.parametrize(
    "bad_action",
    [
        {},
        {"battery_rate": 0.0},
        {"battery_rate": 0.0, "hvac_rate": 0.0, "extra": 0.0},
        {"electrical_storage": 0.0, "cooling_or_heating_device": 0.0},
        {"battery_rate": float("nan"), "hvac_rate": 0.0},
        {"battery_rate": 0.0, "hvac_rate": float("inf")},
        {"battery_rate": 1.01, "hvac_rate": 0.0},
        [0.0, 0.0],
    ],
)
def test_illegal_action_does_not_advance_native_env(bad_action) -> None:
    route = _route(horizon=2)
    try:
        route.reset(seed=3)
        before = (route.env.time_step, route._steps, route.observe())
        with pytest.raises(D3ActionError):
            route.step(bad_action)
        assert (route.env.time_step, route._steps, route.observe()) == before
    finally:
        route.close()


def test_invalid_dt_does_not_advance_native_env() -> None:
    route = _route(horizon=2)
    try:
        route.reset(seed=3)
        before = (route.env.time_step, route._steps, route.observe())
        with pytest.raises(D3ActionError):
            route.step({"battery_rate": 0.0, "hvac_rate": 0.0}, dt_seconds=1800)
        assert (route.env.time_step, route._steps, route.observe()) == before
    finally:
        route.close()


def test_hvac_counterfactual_changes_thermal_and_net_effect() -> None:
    baseline = _route(horizon=1)
    hvac = _route(horizon=1)
    try:
        baseline.reset(seed=11)
        hvac.reset(seed=11)
        left = baseline.step({"battery_rate": 0.0, "hvac_rate": 0.0})["effect"]
        right = hvac.step({"battery_rate": 0.0, "hvac_rate": 1.0})["effect"]
        assert abs(left["indoor_temperature_c"] - right["indoor_temperature_c"]) > 1e-9
        assert abs(left["net_electricity_kwh"] - right["net_electricity_kwh"]) > 1e-9
    finally:
        baseline.close()
        hvac.close()


def test_battery_counterfactual_changes_soc_and_net_effect() -> None:
    baseline = _route(horizon=1)
    charge = _route(horizon=1)
    try:
        baseline.reset(seed=11)
        charge.reset(seed=11)
        left = baseline.step({"battery_rate": 0.0, "hvac_rate": 0.0})["effect"]
        right = charge.step({"battery_rate": 1.0, "hvac_rate": 0.0})["effect"]
        assert abs(left["battery_soc"] - right["battery_soc"]) > 1e-9
        assert abs(left["net_electricity_kwh"] - right["net_electricity_kwh"]) > 1e-9
    finally:
        baseline.close()
        charge.close()


def test_effect_contains_coupled_components_and_energy_balance() -> None:
    route = _route(horizon=1)
    try:
        route.reset(seed=11)
        transition = route.step({"battery_rate": 0.0, "hvac_rate": 0.0})
        assert transition["effect"] == transition["info"]["effect"]
        effect = transition["effect"]
        required = {
            "indoor_temperature_c",
            "battery_soc",
            "non_shiftable_load_kwh",
            "solar_generation_kwh",
            "hvac_electricity_kwh",
            "storage_electricity_kwh",
            "net_electricity_kwh",
            "net_energy_balance_error_kwh",
        }
        assert required <= set(effect)
        expected = (
            effect["non_shiftable_load_kwh"]
            + effect["hvac_electricity_kwh"]
            + effect["dhw_electricity_kwh"]
            + effect["storage_electricity_kwh"]
            - effect["solar_generation_kwh"]
        )
        assert abs(effect["net_electricity_kwh"] - expected) <= 1e-5
        assert abs(effect["net_energy_balance_error_kwh"]) <= 1e-5
    finally:
        route.close()


def test_episode_terminates_at_horizon_and_rejects_steps_after_done() -> None:
    route = _route(horizon=2)
    try:
        route.reset(seed=19)
        first = route.step({"battery_rate": 0.0, "hvac_rate": 0.0})
        second = route.step({"battery_rate": 0.0, "hvac_rate": 0.0})
        assert first["done"] is False
        assert second["done"] is True
        with pytest.raises(D3ActionError):
            route.step({"battery_rate": 0.0, "hvac_rate": 0.0})
    finally:
        route.close()
