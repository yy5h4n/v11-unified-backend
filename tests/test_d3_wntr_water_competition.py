"""Conformance and causal-gate tests for the D3 WNTR water route."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest

from d3_wntr_water_competition_adapter import (
    D3WNTRActionError,
    D3WNTRWaterCompetitionRoute,
    TICK_SECONDS,
)


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "generated/d3_wntr_water_competition_v1/gate_report.json"


def _route() -> D3WNTRWaterCompetitionRoute:
    return D3WNTRWaterCompetitionRoute()


def test_real_runtime_and_bidirectional_coupling_gate() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert report["status"] == "REAL_RUNTIME_PROBED"
    assert report["backend_engine"] == "WNTRSimulator"
    assert report["backend_version"] == "1.3.0"
    assert report["bidirectional_coupling_gate"] is True
    assert all(value > 1e-9 for value in report["cross_intervention_deltas"].values())
    assert report["provenance"]["wntr_importable"] is True


def test_live_agent_contract_is_persistent_and_state_continuous() -> None:
    route = _route()
    try:
        initial = route.reset(seed=7)
        assert initial == route.observe()
        assert all(math.isfinite(value) for value in initial.values())
        native_identity = id(route._proc)
        first = route.step({"shower_valve_open": 1.0, "laundry_valve_open": 1.0})
        second = route.step({"shower_valve_open": 0.0, "laundry_valve_open": 1.0})
        assert id(route._proc) == native_identity
        assert [first["time_seconds"], second["time_seconds"]] == [3600, 7200]
        assert first["delta_t_seconds"] == second["delta_t_seconds"] == TICK_SECONDS
        assert route.observe() == second["observation"]
        assert first["observation"]["pressure_laundry_m"] != second["observation"]["pressure_laundry_m"]
    finally:
        route.close()


def test_both_action_channels_are_legal_and_observed() -> None:
    route = _route()
    try:
        route.reset()
        legal = route.legal_actions()
        assert legal["native"] is True
        assert set(legal["channels"]) == {"shower_valve_open", "laundry_valve_open"}
        transition = route.step({"shower_valve_open": 0.0, "laundry_valve_open": 1.0})
        assert transition["action"] == {"laundry_valve_open": 1.0, "shower_valve_open": 0.0}
    finally:
        route.close()


@pytest.mark.parametrize(
    "bad_action",
    [
        {},
        {"shower_valve_open": 1.0},
        {"shower_valve_open": 1.0, "laundry_valve_open": 1.0, "extra": 0.0},
        {"shower_valve_open": 0.5, "laundry_valve_open": 1.0},
        {"shower_valve_open": True, "laundry_valve_open": 1.0},
        {"shower_valve_open": float("nan"), "laundry_valve_open": 1.0},
    ],
)
def test_illegal_action_fails_closed_without_advancing(bad_action) -> None:
    route = _route()
    try:
        route.reset()
        before = (route._time_seconds, route.observe())
        with pytest.raises(D3WNTRActionError):
            route.step(bad_action)
        assert (route._time_seconds, route.observe()) == before
    finally:
        route.close()


def test_invalid_dt_fails_closed_without_advancing() -> None:
    route = _route()
    try:
        route.reset()
        before = (route._time_seconds, route.observe())
        with pytest.raises(D3WNTRActionError):
            route.step({"shower_valve_open": 1.0, "laundry_valve_open": 1.0}, dt_seconds=1800)
        assert (route._time_seconds, route.observe()) == before
    finally:
        route.close()


def test_reset_is_deterministic_and_trace_files_are_hash_pinned() -> None:
    left = _route()
    right = _route()
    try:
        action = {"shower_valve_open": 1.0, "laundry_valve_open": 0.0}
        left.reset(seed=19)
        right.reset(seed=19)
        assert left.step(action) == right.step(action)
    finally:
        left.close()
        right.close()
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    for ref in report["trace_refs"].values():
        path = ROOT / ref["path"]
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == ref["sha256"]
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        assert len(rows) == ref["row_count"]


def test_terminal_route_rejects_post_horizon_action() -> None:
    route = _route()
    try:
        route.reset()
        result = None
        action = {"shower_valve_open": 1.0, "laundry_valve_open": 1.0}
        for _ in range(6):
            result = route.step(action)
        assert result is not None and result["done"] is True
        with pytest.raises(D3WNTRActionError):
            route.step(action)
    finally:
        route.close()
