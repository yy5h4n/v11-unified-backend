from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from harness_v2.evaluator_v3 import evaluate_thermal_arm, evaluate_thermal_v3
from harness_v2.semantic_validator import ConformanceError


START = datetime(2026, 1, 1, 17, 0, tzinfo=timezone.utc)


def iso(minutes: int) -> str:
    return (START + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def manifest() -> dict:
    return {
        "manifest_version": "thermal-metrics-3.0.0",
        "thermal_evaluator_v3": {
            "protocol_version": "thermal_evaluator_v3",
            "room_bindings": {
                "kitchen": "/public_state/temperature.kitchen",
                "bedroom": "/public_state/temperature.bedroom",
            },
            "active_window": {"start_inclusive": iso(5), "release_exclusive": iso(30)},
            "temperature_unit": "C",
            "acceptable_band_c": {"lower": "20", "upper": "24"},
            "max_staleness_seconds": "600",
        },
        "public_preferences": {"visibility": "public", "target_c": "22"},
        "nrg": {"definition_version": "band-sat-nrg-1", "gamma": "0.10", "min_discrete_units": 4},
        "optional_metrics": {},
    }


def datum(value: float, observed_at: str, unit: str = "C", quality: str = "fresh") -> dict:
    return {"value": value, "unit": unit, "quality": quality, "observed_at": observed_at}


def trace(values=((21, 25), (19, 22)), durations=(600, 1200)) -> dict:
    frames = []
    minute = 0
    for index, (pair, duration) in enumerate(zip(values, durations)):
        stamp = iso(minute)
        frames.append({
            "frame_index": index,
            "timestamp": stamp,
            "duration_to_next_seconds": duration,
            "public_state": {
                "temperature.kitchen": datum(pair[0], stamp),
                "temperature.bedroom": datum(pair[1], stamp),
            },
            "applied_commands": [],
        })
        minute += duration // 60
    return {"frames": frames}


def test_time_weighted_multiroom_half_open_window_and_room_mae():
    result = evaluate_thermal_arm(manifest(), trace())
    # Active overlap is 5 min of frame 0 and 20 min of frame 1, per room.
    assert result.active_room_seconds == Decimal(3000)
    assert result.discrete_units == 4
    assert result.band_satisfaction == Decimal("0.5")
    assert result.room_band_satisfaction == {"kitchen": Decimal("0.2"), "bedroom": Decimal("0.8")}
    assert result.band_maintenance_pass is False
    assert result.worst_room_soft_mae_c.value == Decimal("2.6")
    assert result.mean_room_soft_mae_c.value == Decimal("1.6")


def test_band_boundaries_are_inclusive_and_release_is_exclusive():
    cfg = manifest()
    cfg["thermal_evaluator_v3"]["active_window"] = {"start_inclusive": iso(0), "release_exclusive": iso(10)}
    result = evaluate_thermal_arm(cfg, trace(values=((20, 24),), durations=(600,)))
    assert result.band_satisfaction == 1
    assert result.band_maintenance_pass


def test_partial_active_window_coverage_fails_closed():
    cfg = manifest()
    cfg["thermal_evaluator_v3"]["active_window"] = {"start_inclusive": iso(0), "release_exclusive": iso(31)}
    with pytest.raises(ConformanceError, match="complete active window"):
        evaluate_thermal_arm(cfg, trace())


@pytest.mark.parametrize("mutation, message", [
    (lambda t: t["frames"][0]["public_state"].pop("temperature.kitchen"), "does not resolve"),
    (lambda t: t["frames"][0]["public_state"]["temperature.kitchen"].update(unit="F"), "unit mismatch"),
    (lambda t: t["frames"][0]["public_state"]["temperature.kitchen"].update(quality="stale"), "missing or stale"),
    (lambda t: t["frames"][0].update(duration_to_next_seconds=0), "duration must be positive"),
])
def test_missing_room_wrong_unit_stale_and_zero_duration_fail_closed(mutation, message):
    data = trace()
    mutation(data)
    with pytest.raises(ConformanceError, match=message):
        evaluate_thermal_arm(manifest(), data)


def test_observation_older_than_manifest_staleness_limit_is_rejected():
    data = trace()
    cfg = manifest()
    cfg["thermal_evaluator_v3"]["max_staleness_seconds"] = "599"
    data["frames"][1]["public_state"]["temperature.kitchen"]["observed_at"] = iso(0)
    with pytest.raises(ConformanceError, match="stale temperature"):
        evaluate_thermal_arm(cfg, data)


def test_private_soft_target_is_rejected_and_absent_public_target_is_unavailable():
    cfg = manifest()
    del cfg["public_preferences"]
    cfg["thermal_evaluator_v3"]["private_target_c"] = 22
    with pytest.raises(ConformanceError, match="public_preferences"):
        evaluate_thermal_arm(cfg, trace())
    cfg["thermal_evaluator_v3"].pop("private_target_c")
    result = evaluate_thermal_arm(cfg, trace())
    assert result.worst_room_soft_mae_c.availability == "unavailable"


def test_delta_sat_and_formal_nrg_require_strict_gap_and_units():
    cfg = manifest()
    cfg["thermal_evaluator_v3"]["active_window"] = {"start_inclusive": iso(0), "release_exclusive": iso(20)}
    noop = trace(values=((19, 19), (19, 19)))
    agent = trace(values=((21, 19), (21, 19)))
    ref = trace(values=((21, 21), (21, 21)))
    result = evaluate_thermal_v3(cfg, agent, noop, ref)
    assert result.delta_satisfaction == Decimal("0.5")
    assert result.formal_nrg.value == Decimal("0.5")
    cfg["nrg"]["gamma"] = "1.0"
    assert evaluate_thermal_v3(cfg, agent, noop, ref).formal_nrg.reason == "reference_gap_not_strictly_above_gamma"
    cfg["nrg"].update(gamma="0.10", min_discrete_units=5)
    assert evaluate_thermal_v3(cfg, agent, noop, ref).formal_nrg.reason == "insufficient_discrete_units"


def test_nrg_manifest_has_no_hidden_default_gamma():
    cfg = manifest()
    del cfg["nrg"]["gamma"]
    with pytest.raises(ConformanceError, match="must be explicit"):
        evaluate_thermal_v3(cfg, trace(), trace(), trace())


def test_optional_metrics_are_unavailable_without_bindings_not_implicit_passes():
    result = evaluate_thermal_arm(manifest(), trace())
    assert result.lifecycle.availability == "unavailable"
    assert result.energy.availability == "unavailable"
    assert result.formal_safety.availability == "unavailable"
    assert result.realized_collateral.availability == "unavailable"


def test_realized_collateral_counts_only_committed_or_coalesced_commands():
    cfg = manifest()
    cfg["optional_metrics"]["realized_collateral"] = {
        "device_scope_map": {"hvac.kitchen": "in_scope", "hvac.hall": "out_of_scope"}
    }
    data = trace()
    data["frames"][0]["applied_commands"] = [
        {"device_id": "hvac.kitchen", "status": "committed"},
        {"device_id": "hvac.hall", "status": "coalesced"},
        {"device_id": "unmapped.rejected", "status": "rejected"},
        {"device_id": "unmapped.failed", "status": "failed"},
    ]
    value = evaluate_thermal_arm(cfg, data).realized_collateral.value
    assert value == {
        "committed_or_coalesced_commands": 2,
        "out_of_scope_commands": 1,
        "fraction": Decimal("0.5"),
    }


def test_committed_command_without_scope_mapping_fails_closed_but_rejected_does_not():
    cfg = manifest()
    cfg["optional_metrics"]["realized_collateral"] = {"device_scope_map": {}}
    rejected = trace()
    rejected["frames"][0]["applied_commands"] = [{"device_id": "x", "status": "rejected"}]
    assert evaluate_thermal_arm(cfg, rejected).realized_collateral.value["fraction"] == 0
    committed = deepcopy(rejected)
    committed["frames"][0]["applied_commands"][0]["status"] = "committed"
    with pytest.raises(ConformanceError, match="scope mapping"):
        evaluate_thermal_arm(cfg, committed)
