from __future__ import annotations

import pytest

from unified_compiler.d2_closed_loop import (
    D2ClosedLoopAdapter,
    D2ProtocolError,
    normalize_reset,
    normalize_transition,
)


class FakePhysicalRoute:
    """Tiny protocol fixture; no physical claim is inferred from it."""

    def __init__(self) -> None:
        self.t = 0.0
        self.x = 0.0

    def reset(self, seed: int = 0):
        self.t = 0.0
        self.x = float(seed)
        return {"x": self.x}

    def observe(self):
        return {"x": self.x}

    def legal_actions(self):
        return {"u": {"type": "continuous", "range": [-1.0, 1.0]}}

    def step(self, action, dt_seconds):
        if not -1.0 <= float(action) <= 1.0:
            raise ValueError("illegal action")
        self.t += float(dt_seconds)
        self.x += float(action) * float(dt_seconds)
        return {"time_seconds": self.t, "observation": {"x": self.x}, "action": action, "terminal": self.t >= 3}


def test_common_receipt_normalizes_reset_and_terminal_alias() -> None:
    initial = normalize_reset({"observation": {"x": 1.0}, "backend_state": "fresh"}, seed=3)
    assert initial == {
        "time_seconds": 0.0,
        "observation": {"x": 1.0},
        "action": None,
        "done": False,
        "terminated": False,
        "truncated": False,
        "info": {"seed": 3},
    }
    transition = normalize_transition(
        {"time_seconds": 1, "observation": {"x": 2}, "terminal": True},
        action=1,
        previous_time=0,
    )
    assert transition["done"] is True and transition["terminated"] is True
    assert transition["action"] == 1


def test_wrapper_enforces_reset_monotonicity_and_mid_trajectory_switch() -> None:
    route = D2ClosedLoopAdapter(FakePhysicalRoute())
    with pytest.raises(RuntimeError):
        route.observe()
    first = route.reset(seed=0)
    assert first["time_seconds"] == 0.0 and first["action"] is None
    assert route.legal_actions()["u"]["range"] == [-1.0, 1.0]
    a = route.step(1.0, 1.0)
    b = route.step(-1.0, 1.0)
    assert a["time_seconds"] == 1.0 and b["time_seconds"] == 2.0
    assert a["observation"] != b["observation"]
    assert route.observe() == b["observation"]


def test_wrapper_fails_closed_for_bad_receipts_and_illegal_actions() -> None:
    route = D2ClosedLoopAdapter(FakePhysicalRoute())
    route.reset()
    with pytest.raises(ValueError):
        route.step(2.0, 1.0)
    with pytest.raises(D2ProtocolError):
        normalize_transition({"time_seconds": 0, "observation": {"x": 0}}, action=0, previous_time=0)
    with pytest.raises(D2ProtocolError):
        normalize_transition({"time_seconds": 1, "observation": {"x": float("nan")}}, action=0, previous_time=0)

