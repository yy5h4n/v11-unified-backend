from __future__ import annotations

import json
from pathlib import Path

import pytest

import d2_modelica_buildings_aixlib_adapter as adapter_module
from d2_modelica_buildings_aixlib_adapter import (
    ACTION,
    OBSERVATION_ROLES,
    MODEL_SOURCE,
    ModelicaBuildingsAixLibAdapter,
    ModelicaRuntimeUnavailable,
    probe_runtime,
    _find_fmu_binary,
    validate_action,
)
from probe_d2_modelica_buildings_aixlib import REPORT, build


ROOT = Path(__file__).resolve().parents[1]


def test_generated_gate_is_real_runtime_or_explicitly_fail_closed() -> None:
    gate = json.loads(REPORT.read_text(encoding="utf-8"))
    assert gate["schema_version"] == "d2-modelica-buildings-aixlib-backend-gate-v1"
    assert gate["backend"] == "Modelica"
    assert gate["backend_family"] == "Buildings/AixLib"
    assert gate["status"] in {"REAL_RUNTIME_PROBED", "EVIDENCE_PENDING"}
    assert gate["passed"] is (gate["status"] == "REAL_RUNTIME_PROBED")
    assert gate["surrogate_model_used"] is False
    if gate["status"] == "EVIDENCE_PENDING":
        assert gate["exclusion_reasons"]
        assert gate["runtime_probe"]["available"] is False
    else:
        assert gate["exclusion_reasons"] == []
        assert gate["runtime_probe"]["available"] is True


def test_gate_build_is_current_and_preserves_blockers() -> None:
    expected = build()
    actual = json.loads(REPORT.read_text(encoding="utf-8"))
    assert expected == actual
    assert expected["status"] in {"REAL_RUNTIME_PROBED", "EVIDENCE_PENDING"}
    assert expected["passed"] is (expected["status"] == "REAL_RUNTIME_PROBED")


def test_runtime_probe_never_claims_surrogate_capability() -> None:
    evidence = probe_runtime()
    assert evidence["surrogate_model_used"] is False
    assert set(evidence["required_libraries"]) == {"Buildings", "AixLib"}
    assert evidence["model"]["available"] is True
    if not evidence["available"]:
        assert evidence["blockers"]


def test_missing_real_runtime_fails_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(adapter_module, "probe_runtime", lambda: {
        "available": False,
        "blockers": ["TEST_RUNTIME_MISSING"],
        "compiler": {"path": None},
    })
    runner = ModelicaBuildingsAixLibAdapter(run_root=tmp_path)
    with pytest.raises(ModelicaRuntimeUnavailable, match="TEST_RUNTIME_MISSING"):
        runner.reset(seed=0)
    assert not list(tmp_path.iterdir())


def test_capabilities_reflect_real_runtime_availability() -> None:
    capability = ModelicaBuildingsAixLibAdapter().capabilities()
    assert capability["backend_family"] == "Buildings/AixLib"
    assert capability["surrogate_model_used"] is False
    assert capability["status"] in {"REAL_RUNTIME_PROBED", "EVIDENCE_PENDING"}
    assert capability["runtime_available"] is (capability["status"] == "REAL_RUNTIME_PROBED")


def test_action_contract_is_finite_and_bounded() -> None:
    assert ACTION["legal_range"] == [0.0, 1.0]
    assert validate_action(0.0) == 0.0
    assert validate_action(1.0) == 1.0
    with pytest.raises(ValueError):
        validate_action(-0.01)
    with pytest.raises(ValueError):
        validate_action(1.01)


def test_model_source_is_real_library_bound_and_not_a_surrogate() -> None:
    source = MODEL_SOURCE.read_text(encoding="utf-8")
    assert "import Buildings;" in source
    assert "import AixLib;" in source
    assert "OneElement" in source
    assert "PrescribedHeatFlow" in source
    assert "phi_pTX" in source
    assert "fallback" not in source.lower()


def test_gate_does_not_emit_records_or_shared_evaluation_fields() -> None:
    gate = json.loads(REPORT.read_text(encoding="utf-8"))
    serialized = json.dumps(gate, sort_keys=True).lower()
    for forbidden in ("episode", "evaluator", "responsibility", "threshold"):
        assert forbidden not in serialized


@pytest.mark.skipif(_find_fmu_binary() is None, reason="compiled FMI co-simulation FMU is not present")
def test_interactive_reset_returns_initial_observation_and_legal_actions() -> None:
    runner = ModelicaBuildingsAixLibAdapter()
    initial = runner.reset(seed=7)
    assert set(initial) == set(OBSERVATION_ROLES)
    assert initial["room_a_temperature_c"] == 20.0
    assert runner.legal_actions()["legal_range"] == [0.0, 1.0]
    runner.close()


@pytest.mark.skipif(_find_fmu_binary() is None, reason="compiled FMI co-simulation FMU is not present")
def test_interactive_step_is_monotonic_and_preserves_native_state() -> None:
    runner = ModelicaBuildingsAixLibAdapter()
    runner.reset(seed=0)
    first = runner.step(1.0, dt_seconds=60.0)
    second = runner.step(0.0, dt_seconds=60.0)
    assert first["time_seconds"] == 60.0
    assert second["time_seconds"] == 120.0
    assert first["action"] == {"radiator_valve": 1.0}
    assert second["action"] == {"radiator_valve": 0.0}
    assert first["observation"]["heater_heat_flow_w"] == 2000.0
    assert second["observation"]["heater_heat_flow_w"] == 0.0
    runner.close()


@pytest.mark.skipif(_find_fmu_binary() is None, reason="compiled FMI co-simulation FMU is not present")
def test_mid_trajectory_action_switch_changes_future_native_state() -> None:
    switched = ModelicaBuildingsAixLibAdapter()
    switched.reset(seed=0)
    for _ in range(5):
        switched.step(1.0, 60.0)
    switched_state = switched.step(0.0, 60.0)["observation"]
    continuous = ModelicaBuildingsAixLibAdapter()
    continuous.reset(seed=0)
    for _ in range(6):
        continuous_state = continuous.step(1.0, 60.0)["observation"]
    assert switched_state["room_a_temperature_c"] < continuous_state["room_a_temperature_c"]
    assert switched_state["room_b_temperature_c"] < continuous_state["room_b_temperature_c"]
    switched.close()
    continuous.close()


@pytest.mark.skipif(_find_fmu_binary() is None, reason="compiled FMI co-simulation FMU is not present")
def test_interactive_reset_is_deterministic_and_invalid_action_fails_closed() -> None:
    left = ModelicaBuildingsAixLibAdapter()
    right = ModelicaBuildingsAixLibAdapter()
    left.reset(seed=11)
    right.reset(seed=11)
    assert left.observe() == right.observe()
    with pytest.raises(ValueError):
        left.step(1.01, 60.0)
    assert left.observe() == right.observe()
    left.close()
    right.close()
