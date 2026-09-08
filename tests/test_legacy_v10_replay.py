"""Replay tests against the real frozen v10 artifacts and runtimes.

One real CityLearn HVAC episode and one real EV2Gym episode are driven to
termination through ``V10RuntimeBridge.replay`` with explicit, solver-neutral
policies and explicit action sequences. These tests fail closed: missing
artifacts, missing runtime dependencies, or missing source assets are hard
failures, never silent skips. No files are written; capability status stays
``LEGACY_EXECUTABLE_PENDING_MIGRATION`` regardless of the outcome here.

The frozen v10 runtime module is loaded lazily under a private interpreter
name; an autouse fixture removes it again so the laziness assertions in
``test_legacy_v10_adapters.py`` remain valid in any execution order.
"""

from __future__ import annotations

import json
import sys
from typing import Any, Callable

import pytest

from unified_compiler.adapters import (
    LegacyArtifactError,
    ReplayActionError,
    ReplayError,
    V10ArtifactIndex,
    V10RuntimeBridge,
)
from unified_compiler.adapters.legacy_v10 import (
    DEFAULT_ARTIFACT_DIR,
    PRIVATE_FILENAME,
    _read_jsonl,
)
from unified_compiler.adapters.legacy_v10_runtime import V10_RUNTIME_MODULE_NAME

_SPEC_RUNTIME_MODULES = (
    V10_RUNTIME_MODULE_NAME,
    "v9_runtime_for_v10",
    "v8_runtime_for_v9",
)

HVAC_BACKEND = "CityLearn"
EV_BACKEND = "EV2Gym"


@pytest.fixture(autouse=True)
def _drop_spec_runtime_modules():
    yield
    for name in _SPEC_RUNTIME_MODULES:
        sys.modules.pop(name, None)


@pytest.fixture(scope="module")
def bridge() -> V10RuntimeBridge:
    return V10RuntimeBridge()


def _first_episode_id(backend: str) -> str:
    for record in _read_jsonl(DEFAULT_ARTIFACT_DIR / PRIVATE_FILENAME):
        if record["backend_binding"]["backend"] == backend:
            return record["episode_id"]
    raise AssertionError(f"no {backend} episode in the frozen v10 artifacts")


@pytest.fixture(scope="module")
def hvac_episode(bridge: V10RuntimeBridge) -> tuple[str, dict[str, Any]]:
    episode_id = _first_episode_id(HVAC_BACKEND)
    public, _ = bridge.resolve_pair(episode_id)
    return episode_id, public


@pytest.fixture(scope="module")
def ev_episode(bridge: V10RuntimeBridge) -> tuple[str, dict[str, Any]]:
    episode_id = _first_episode_id(EV_BACKEND)
    public, _ = bridge.resolve_pair(episode_id)
    return episode_id, public


def hvac_occupancy_policy(step_index: int, observation: dict[str, Any]) -> str:
    return "MAINTAIN_COMFORT" if observation["occupant_count"] > 0 else "ECO_OFF"


def ev_max_safe_policy(step_index: int, observation: dict[str, Any]) -> dict[str, Any]:
    headroom = max(
        0.0, observation["household_power_limit_kw"] - observation["household_load_kw"]
    )
    return {
        "type": "SET_CHARGE_POWER",
        "kw": min(observation["charger_max_power_kw"], headroom),
    }


def ev_zero_policy(step_index: int, observation: dict[str, Any]) -> dict[str, Any]:
    return {"type": "SET_CHARGE_POWER", "kw": 0.0}


def _canonical(result: dict[str, Any]) -> str:
    return json.dumps(result, sort_keys=True)


def _assert_well_formed(
    result: dict[str, Any],
    episode_id: str,
    public: dict[str, Any],
    action_source: str,
) -> None:
    assert result["episode_id"] == episode_id
    assert result["action_source"] == action_source
    assert result["horizon_steps"] == public["horizon_steps"]
    assert result["steps_completed"] == public["horizon_steps"]
    assert result["episode_done"] is True
    assert result["completed"] is True
    assert len(result["transitions"]) == result["steps_completed"]
    assert json.loads(_canonical(result)) == result
    blob = _canonical(result).lower()
    for term in ("responsibility_contract", "gold", "selection_lineage"):
        assert term not in blob


def test_hvac_episode_replays_to_completion_with_policy(
    bridge: V10RuntimeBridge, hvac_episode: tuple[str, dict[str, Any]]
) -> None:
    episode_id, public = hvac_episode
    result = bridge.replay(episode_id, policy=hvac_occupancy_policy)
    _assert_well_formed(result, episode_id, public, "policy")
    assert result["backend"] == HVAC_BACKEND
    modes = {transition["requested_mode"] for transition in result["transitions"]}
    assert modes <= {"MAINTAIN_COMFORT", "ECO_OFF", "WAIT"}
    energies = [t["effect"]["hvac_energy_kwh"] for t in result["transitions"]]
    assert any(energy > 0 for energy in energies)


def test_hvac_replay_is_deterministic_across_two_runs(
    bridge: V10RuntimeBridge, hvac_episode: tuple[str, dict[str, Any]]
) -> None:
    episode_id, _ = hvac_episode
    first = bridge.replay(episode_id, policy=hvac_occupancy_policy)
    second = bridge.replay(episode_id, policy=hvac_occupancy_policy)
    assert _canonical(first) == _canonical(second)


def test_hvac_actions_produce_physical_feedback(
    bridge: V10RuntimeBridge, hvac_episode: tuple[str, dict[str, Any]]
) -> None:
    episode_id, _ = hvac_episode
    off = bridge.replay(episode_id, policy=lambda step, obs: "ECO_OFF")
    maintain = bridge.replay(episode_id, policy=lambda step, obs: "MAINTAIN_COMFORT")

    def signature(result: dict[str, Any]) -> list[tuple[Any, Any]]:
        return [
            (t["effect"]["indoor_temperature_c"], t["effect"]["hvac_energy_kwh"])
            for t in result["transitions"]
        ]

    assert signature(off) != signature(maintain)
    assert any(
        transition["effect"]["hvac_energy_kwh"] > 0
        for transition in maintain["transitions"]
    )


def test_hvac_explicit_action_sequence_replays_to_completion(
    bridge: V10RuntimeBridge, hvac_episode: tuple[str, dict[str, Any]]
) -> None:
    episode_id, public = hvac_episode
    horizon = public["horizon_steps"]
    assert set(public["allowed_actions"]) >= {"ECO_OFF", "MAINTAIN_COMFORT"}
    sequence = ["MAINTAIN_COMFORT" if i % 2 == 0 else "ECO_OFF" for i in range(horizon)]
    result = bridge.replay(episode_id, actions=sequence)
    _assert_well_formed(result, episode_id, public, "actions")
    modes = [transition["requested_mode"] for transition in result["transitions"]]
    assert modes == sequence


def test_ev_episode_replays_to_completion_with_policy(
    bridge: V10RuntimeBridge, ev_episode: tuple[str, dict[str, Any]]
) -> None:
    episode_id, public = ev_episode
    result = bridge.replay(episode_id, policy=ev_max_safe_policy)
    _assert_well_formed(result, episode_id, public, "policy")
    assert result["backend"] == EV_BACKEND
    departure_soc = result["transitions"][-1]["effect"]["departure_soc"]
    assert departure_soc is not None
    charged = sum(t["effect"]["charged_energy_kwh"] for t in result["transitions"])
    assert charged > 0
    assert not any(t["effect"]["overloaded"] for t in result["transitions"])


def test_ev_replay_is_deterministic_across_two_runs(
    bridge: V10RuntimeBridge, ev_episode: tuple[str, dict[str, Any]]
) -> None:
    episode_id, _ = ev_episode
    first = bridge.replay(episode_id, policy=ev_max_safe_policy)
    second = bridge.replay(episode_id, policy=ev_max_safe_policy)
    assert _canonical(first) == _canonical(second)


def test_ev_actions_produce_physical_feedback(
    bridge: V10RuntimeBridge, ev_episode: tuple[str, dict[str, Any]]
) -> None:
    episode_id, _ = ev_episode
    zero = bridge.replay(episode_id, policy=ev_zero_policy)
    maximum = bridge.replay(episode_id, policy=ev_max_safe_policy)
    assert all(
        transition["effect"]["charged_energy_kwh"] == 0.0
        for transition in zero["transitions"]
    )
    assert (
        sum(t["effect"]["charged_energy_kwh"] for t in maximum["transitions"]) > 0
    )
    zero_soc = [t["effect"]["vehicle_soc_after"] for t in zero["transitions"]]
    maximum_soc = [t["effect"]["vehicle_soc_after"] for t in maximum["transitions"]]
    assert zero_soc != maximum_soc


def test_ev_explicit_action_sequence_replays_to_completion(
    bridge: V10RuntimeBridge, ev_episode: tuple[str, dict[str, Any]]
) -> None:
    episode_id, public = ev_episode
    horizon = public["horizon_steps"]
    sequence = [
        {"type": "SET_CHARGE_POWER", "kw": 1.5} if i % 2 == 0 else {"type": "WAIT"}
        for i in range(horizon)
    ]
    result = bridge.replay(episode_id, actions=sequence)
    _assert_well_formed(result, episode_id, public, "actions")
    powers = [t["action"]["charge_power_kw"] for t in result["transitions"]]
    assert powers[0] == 1.5
    assert powers[1] == 1.5
    assert any(power == 1.5 for power in powers)


def test_replay_requires_exactly_one_action_driver(
    bridge: V10RuntimeBridge, hvac_episode: tuple[str, dict[str, Any]]
) -> None:
    episode_id, _ = hvac_episode
    with pytest.raises(ReplayActionError):
        bridge.replay(episode_id)
    with pytest.raises(ReplayActionError):
        bridge.replay(
            episode_id, policy=hvac_occupancy_policy, actions=["ECO_OFF"]
        )


def test_replay_rejects_undeclared_action_types(
    bridge: V10RuntimeBridge,
    hvac_episode: tuple[str, dict[str, Any]],
    ev_episode: tuple[str, dict[str, Any]],
) -> None:
    hvac_id, _ = hvac_episode
    ev_id, _ = ev_episode
    with pytest.raises(ReplayActionError):
        bridge.replay(hvac_id, policy=lambda step, obs: "BLAST_HEAT")
    with pytest.raises(ReplayActionError):
        bridge.replay(ev_id, actions=[{"type": "LAUNCH_ROCKET", "kw": 1.0}])
    with pytest.raises(ReplayActionError):
        bridge.replay(hvac_id, actions=[{"type": "ECO_OFF"}])


def test_replay_rejects_out_of_range_parameters_without_loading_runtime(
    bridge: V10RuntimeBridge, ev_episode: tuple[str, dict[str, Any]]
) -> None:
    episode_id, public = ev_episode
    maximum = public["allowed_actions"]["SET_CHARGE_POWER"]["kw"]["maximum"]
    illegal = [
        {"type": "SET_CHARGE_POWER", "kw": maximum * 10}
        for _ in range(public["horizon_steps"])
    ]
    with pytest.raises(ReplayActionError, match="above maximum"):
        bridge.replay(episode_id, actions=illegal)
    assert V10_RUNTIME_MODULE_NAME not in sys.modules


def test_replay_rejects_wrong_length_action_sequences(
    bridge: V10RuntimeBridge, hvac_episode: tuple[str, dict[str, Any]]
) -> None:
    episode_id, public = hvac_episode
    horizon = public["horizon_steps"]
    with pytest.raises(ReplayActionError, match="exhausted"):
        bridge.replay(episode_id, actions=["ECO_OFF"] * (horizon - 1))
    with pytest.raises(ReplayActionError, match="terminated after"):
        bridge.replay(episode_id, actions=["ECO_OFF"] * (horizon + 1))


def test_replay_fails_when_termination_not_reached_within_step_limit(
    bridge: V10RuntimeBridge, ev_episode: tuple[str, dict[str, Any]]
) -> None:
    episode_id, _ = ev_episode
    with pytest.raises(ReplayError, match="not done after 1 steps"):
        bridge.replay(episode_id, policy=ev_zero_policy, max_steps=1)


def test_replay_fails_closed_when_artifacts_absent(tmp_path) -> None:
    bridge = V10RuntimeBridge(index=V10ArtifactIndex(tmp_path / "nope"))
    with pytest.raises(LegacyArtifactError):
        bridge.replay("any-episode", policy=lambda step, obs: "ECO_OFF")
    assert V10_RUNTIME_MODULE_NAME not in sys.modules


def test_replay_result_metadata_matches_inventory(
    bridge: V10RuntimeBridge,
    hvac_episode: tuple[str, dict[str, Any]],
    ev_episode: tuple[str, dict[str, Any]],
) -> None:
    for episode_id, _public in (hvac_episode, ev_episode):
        public, private = bridge.resolve_pair(episode_id)
        policy: Callable[[int, dict[str, Any]], Any] = (
            hvac_occupancy_policy
            if private["backend_binding"]["backend"] == HVAC_BACKEND
            else ev_max_safe_policy
        )
        result = bridge.replay(episode_id, policy=policy)
        assert result["physical_process_id"] == private["physical_process_id"]
        assert result["backend"] == private["backend_binding"]["backend"]
        assert not result["backend_terminated"] or result["episode_done"]
