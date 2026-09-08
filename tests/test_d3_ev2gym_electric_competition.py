from __future__ import annotations

import json
from pathlib import Path

from d3_ev2gym_electric_competition_adapter import (
    EV2GymElectricCompetition,
    EV2GymCompetitionActionError,
    EV2GymCompetitionError,
    SCHEMA_VERSION,
    runtime_provenance,
)
from probe_d3_ev2gym_electric_competition import probe


def test_public_schema_has_two_native_shared_transformer_channels():
    route = EV2GymElectricCompetition()
    schema = route.legal_actions()
    assert set(schema["channels"]) == {"charger_0_rate", "charger_1_rate"}
    assert schema["channels"]["charger_0_rate"]["index"] == 0
    assert schema["channels"]["charger_1_rate"]["index"] == 1
    assert "shared" in schema["shared_constraint"]


def test_invalid_action_is_rejected_before_native_runtime():
    route = EV2GymElectricCompetition()
    try:
        route._action({"charger_0_rate": 2.0, "charger_1_rate": 0.0})
    except EV2GymCompetitionActionError:
        pass
    else:
        raise AssertionError("out-of-range action was accepted")


def test_runtime_provenance_is_explicit():
    provenance = runtime_provenance()
    assert provenance["native_backend"] == "EV2Gym"
    assert provenance["status_policy"].startswith("EVIDENCE_PENDING")
    assert provenance["topology_sha256"]


def test_probe_fail_closed_when_local_native_dependencies_are_missing():
    report = probe(seed=23, horizon_steps=2)
    assert report["schema_version"] == SCHEMA_VERSION
    assert report["status"] in {"EVIDENCE_PENDING", "BACKEND_REPLAY_VERIFIED"}
    if report["status"] == "EVIDENCE_PENDING":
        assert report["verified"] is False
        assert "error" in report["details"]

