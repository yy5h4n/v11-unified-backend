"""Live conformance and causal-gate tests for the D3 EnergyPlus route."""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from d3_energyplus_shared_ventilation_adapter import (
    D3EnergyPlusActionError, D3EnergyPlusSharedVentilationRoute, TICK_SECONDS,
)

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "generated/d3_energyplus_shared_ventilation_v1/gate_report.json"


def test_real_runtime_report_and_bidirectional_gate() -> None:
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    assert report["status"] == "REAL_RUNTIME_PROBED"
    assert report["passed"] is True
    assert report["backend_engine"] == "EnergyPlusAPI"
    assert report["energyplus_version"] == "26.1.0"
    assert report["ems_shared_capacity_gate"] is True
    assert report["bidirectional_coupling_gate"] is True
    assert all(value > 1e-6 for value in report["cross_intervention_deltas"].values())
    assert report["provenance"]["surrogate_model_used"] is False


def test_persistent_live_state_observe_and_action_effect() -> None:
    route = D3EnergyPlusSharedVentilationRoute()
    try:
        initial = route.reset(seed=7)
        assert initial == route.observe()
        identity = id(route._state)
        first = route.step({"zone_a_airflow_request": 1.0, "zone_b_airflow_request": 1.0})
        second = route.step({"zone_a_airflow_request": 0.0, "zone_b_airflow_request": 1.0})
        assert id(route._state) == identity
        assert second["time_seconds"] - first["time_seconds"] == TICK_SECONDS
        assert route.observe() == second["observation"]
        assert first["observation"]["zone_b_actual_airflow_m3_s"] != second["observation"]["zone_b_actual_airflow_m3_s"]
        assert all(math.isfinite(value) for value in second["observation"].values())
    finally:
        route.close()


@pytest.mark.parametrize("bad", [
    {}, {"zone_a_airflow_request": 1.0},
    {"zone_a_airflow_request": 1.0, "zone_b_airflow_request": 1.0, "extra": 0.0},
    {"zone_a_airflow_request": -0.1, "zone_b_airflow_request": 1.0},
    {"zone_a_airflow_request": True, "zone_b_airflow_request": 1.0},
    {"zone_a_airflow_request": float("nan"), "zone_b_airflow_request": 1.0},
])
def test_illegal_action_does_not_advance(bad) -> None:
    route = D3EnergyPlusSharedVentilationRoute()
    try:
        route.reset()
        before = (route._time_seconds, route.observe())
        with pytest.raises(D3EnergyPlusActionError):
            route.step(bad)
        assert (route._time_seconds, route.observe()) == before
    finally:
        route.close()


def test_invalid_dt_does_not_advance() -> None:
    route = D3EnergyPlusSharedVentilationRoute()
    try:
        route.reset()
        before = (route._time_seconds, route.observe())
        with pytest.raises(D3EnergyPlusActionError):
            route.step({"zone_a_airflow_request": 1.0, "zone_b_airflow_request": 1.0}, dt_seconds=600.0)
        assert (route._time_seconds, route.observe()) == before
    finally:
        route.close()


def test_reset_is_deterministic() -> None:
    left, right = D3EnergyPlusSharedVentilationRoute(), D3EnergyPlusSharedVentilationRoute()
    action = {"zone_a_airflow_request": 1.0, "zone_b_airflow_request": 0.0}
    try:
        assert left.reset(seed=19) == right.reset(seed=19)
        assert left.step(action) == right.step(action)
    finally:
        left.close(); right.close()
