from copy import deepcopy
from decimal import Decimal

import pytest

from harness_v2.golden import START, golden_bundle
from harness_v2.semantic_validator import ConformanceError
from harness_v2.trace_evaluator import evaluate_trace


def fixture():
    bundle = golden_bundle()
    return bundle["evaluator_manifest"], bundle["main_evaluation"]["agent"]["sealed_trace"]


def test_hand_computed_single_interval_loss():
    manifest, trace = fixture()
    result = evaluate_trace(manifest, trace)
    assert result.loss == Decimal("1.5")
    assert result.component_metrics == {"kitchen_error": Decimal("1.5"), "unauthorized_action": Decimal("0")}
    assert result.safety_pass


def test_unequal_duration_mean_is_time_weighted():
    manifest, trace = fixture()
    first = trace["frames"][0]
    first["public_state"]["kitchen_temperature_c"]["value"] = 19
    first["duration_to_next_seconds"] = 1800
    second = deepcopy(first)
    second["frame_index"] = 1
    second["timestamp"] = "2026-01-01T17:30:00+00:00"
    second["duration_to_next_seconds"] = 5400
    second["public_state"]["kitchen_temperature_c"] = {"value": 21, "unit": "C", "quality": "fresh", "observed_at": second["timestamp"]}
    second["public_state"]["living_temperature_c"]["observed_at"] = second["timestamp"]
    second["private_evaluator_primitives"]["unauthorized_action_count"]["observed_at"] = second["timestamp"]
    trace["frames"] = [first, second]
    result = evaluate_trace(manifest, trace)
    assert result.loss == Decimal("1.5")  # (3*1800 + 1*5400) / 7200


def test_rejects_duration_inconsistent_with_timestamps():
    manifest, trace = fixture()
    trace["frames"][0]["duration_to_next_seconds"] = 1
    with pytest.raises(ConformanceError):
        evaluate_trace(manifest, trace)


def test_rejects_unit_mismatch():
    manifest, trace = fixture()
    trace["frames"][0]["public_state"]["kitchen_temperature_c"]["unit"] = "F"
    with pytest.raises(ConformanceError):
        evaluate_trace(manifest, trace)


def test_rejects_stale_input_under_fail_rule():
    manifest, trace = fixture()
    trace["frames"][0]["public_state"]["kitchen_temperature_c"]["quality"] = "stale"
    with pytest.raises(ConformanceError):
        evaluate_trace(manifest, trace)


def test_safety_failure_is_reported():
    manifest, trace = fixture()
    trace["frames"][0]["public_state"]["kitchen_temperature_c"]["value"] = 31
    result = evaluate_trace(manifest, trace)
    assert not result.safety_pass
