"""Focused checks for the real FDS prefix-replay route."""
from __future__ import annotations

import math

import pytest

from d2_fds_adapter import (
    FDSSmokePropagationAdapter,
    FDSRuntimeUnavailable,
    _interactive_model_text,
    discover_fds_runtime,
)
from unified_compiler.agent_interface import AgentInterfaceError, make_agent_backend


pytestmark = pytest.mark.skipif(
    not discover_fds_runtime()["available"], reason="pinned FDS runtime is unavailable"
)


def test_interactive_deck_uses_official_fds_control_schedule() -> None:
    deck = _interactive_model_text([(0.0, 0.0), (1.0, 1.0)], 2.0)
    assert "FUNCTION_TYPE='CUSTOM'" in deck
    assert "INPUT_ID='STEP_CLOCK'" in deck
    assert "RAMP ID='DOOR_RAMP'" in deck
    assert "CTRL_ID='DOOR_CTRL'" in deck
    assert "SURF_ID='INERT'" in deck


def test_prefix_replay_contract_is_monotone_and_rejects_illegal_actions(tmp_path) -> None:
    adapter = FDSSmokePropagationAdapter(run_root=tmp_path, timeout_s=120)
    reset = adapter.reset(seed=7)
    assert set(reset) == {
        "room_b_temperature_c",
        "room_b_visibility_m",
        "room_b_velocity_mps",
    }
    replay_reset = adapter.reset(seed=7)
    assert replay_reset == reset
    first = adapter.step(0.0, 1.0)
    second = adapter.step(1.0, 1.0)
    assert first["time_seconds"] == 1.0
    assert second["time_seconds"] == 2.0
    assert second["done"] is False
    assert all(math.isfinite(v) for v in second["observation"].values())
    assert second["provenance"]["interactive_step"] is False
    assert second["provenance"]["continuation_mode"] == "full_history_real_backend_replay_not_online"
    assert second["provenance"]["reset_semantics"].startswith("fresh FDS process replay from t=0")
    with pytest.raises(ValueError):
        adapter.step(0.5, 1.0)
    with pytest.raises(ValueError):
        adapter.step(0.0, 0.0)


def test_mid_trajectory_action_switch_changes_real_future(tmp_path) -> None:
    def rollout(actions, root):
        adapter = FDSSmokePropagationAdapter(run_root=root, timeout_s=120)
        adapter.reset()
        result = None
        for action in actions:
            result = adapter.step(action, 1.0)
        return result

    closed = rollout([0.0, 0.0], tmp_path / "closed")
    switched = rollout([0.0, 1.0], tmp_path / "switched")
    assert closed is not None and switched is not None
    assert closed["time_seconds"] == switched["time_seconds"] == 2.0
    assert closed["provenance"]["interactive_step"] is False
    assert switched["provenance"]["interactive_step"] is False
    assert closed["provenance"]["continuation_mode"] == "full_history_real_backend_replay_not_online"
    assert switched["provenance"]["continuation_mode"] == "full_history_real_backend_replay_not_online"
    differences = [
        abs(closed["observation"][role] - switched["observation"][role])
        for role in closed["observation"]
    ]
    assert max(differences) > 1.0e-6


def test_missing_runtime_fails_closed(tmp_path) -> None:
    adapter = FDSSmokePropagationAdapter(
        run_root=tmp_path, runtime=tmp_path / "missing-fds", timeout_s=1
    )
    # The test module is skipped on hosts without the normal runtime; this
    # explicit override verifies that a missing injected runtime is not
    # replaced by a toy transition.
    with pytest.raises(FDSRuntimeUnavailable):
        adapter.reset()


def test_online_only_request_is_rejected_and_replay_capability_is_explicit() -> None:
    with pytest.raises(AgentInterfaceError, match="prefix_replay only"):
        make_agent_backend("fds_smoke_fire", require_online=True)
    backend = make_agent_backend("fds_smoke_fire")
    try:
        assert backend.route.backend.legal_actions()["execution_capability"] == "prefix_replay"
        assert backend.route.backend.legal_actions()["online_step_supported"] is False
    finally:
        backend.close()
