import json
from copy import deepcopy

import pytest

import build_workflow_formal_release_v1 as builder
import validate_formal_dataset_release as validator
from harness_v2.semantic_validator import ConformanceError
from harness_v2.trust_evidence import decode_serialization
from harness_v2.workflow_trust import (
    verify_trusted_evaluation_receipt,
    verify_trusted_replay_receipt,
    verify_trusted_reset_receipt,
)


SCENARIOS = {"laundry_completion_notification", "mail_arrival_notification"}


def _fixture_inputs(tmp_path):
    source = json.loads(builder.CONTRACTS.read_text(encoding="utf-8"))
    routes = [
        deepcopy(route)
        for route in source["routes"]
        if route.get("contract", {}).get("scenario_type") in SCENARIOS
    ]
    assert len(routes) == 2
    for route in routes:
        route["support_status"] = "FULL"
        route["capability_match"] = {"missing_requirements": []}
        route["contract"]["minimum_horizon_seconds"] = 3600
    contracts = tmp_path / "contracts.json"
    contracts.write_text(json.dumps({"routes": routes}), encoding="utf-8")
    trusted_items = json.loads(builder.QUERY_VARIANTS.read_text(encoding="utf-8"))["items"]
    items = {route["responsibility_id"]: trusted_items[route["responsibility_id"]] for route in routes}
    variants = tmp_path / "queries.json"
    variants.write_text(json.dumps({"items": items}), encoding="utf-8")
    return routes, contracts, variants


def test_release_builder_uses_code_side_trusted_receipts_for_all_arms(tmp_path, monkeypatch):
    routes, contracts, variants = _fixture_inputs(tmp_path)
    output = tmp_path / "release"
    monkeypatch.setattr(builder, "CONTRACTS", contracts)
    monkeypatch.setattr(builder, "QUERY_VARIANTS", variants)
    monkeypatch.setattr(builder, "OUTPUT", output)
    monkeypatch.setattr(builder, "VARIANTS_PER_RESPONSIBILITY", 1)
    monkeypatch.setattr(validator, "QUERY_VARIANTS", variants)

    manifest = builder.build()
    assert manifest["status"] == "PASS"
    rows = [json.loads(line) for line in (output / "episodes_private.jsonl").read_text().splitlines()]
    assert len(rows) == 2
    expected_arms = {"reference", "repeat", "noop", "query_deleted", "query_shuffled"}

    public_rows = {
        row["episode_id"]: row
        for row in map(json.loads, (output / "episodes_public.jsonl").read_text().splitlines())
    }
    by_id = {route["responsibility_id"]: route for route in routes}
    for row in rows:
        assert set(row["trusted_runtime"]) == {"runtime_id", "version", "registry_entry_digest"}
        assert set(row["trusted_replays"]) == expected_arms
        assert set(row["trusted_evaluations"]) == expected_arms
        assert row["admission_receipt"]["gates"] == {
            "reference_success": True,
            "noop_fails": True,
            "query_deletion_fails": True,
            "query_shuffle_fails": True,
            "deterministic_replay": True,
            "action_sensitive": True,
        }

        route = by_id[row["responsibility_id"]]
        config = row["process_receipt"]["payload"]["backend_effective_config"]
        horizon = row["process_receipt"]["payload"]["horizon_seconds"]
        public = public_rows[row["episode_id"]]
        runtime = validator._build_code_side_workflow_registry(
            scenario=route["contract"]["scenario_type"],
            config=config,
            horizon_seconds=horizon,
        ).require(validator.RUNTIME_ID)
        reset_spec = decode_serialization(row["trusted_reset_receipt"]["episode_spec"], "test EpisodeSpec")
        spec = builder._episode(
            row["episode_id"], reset_spec["seed"], public["query"], horizon, public["public_profile"]
        )
        verify_trusted_reset_receipt(row["trusted_reset_receipt"], runtime, spec)
        reference_run = verify_trusted_replay_receipt(
            row["trusted_replays"]["reference"],
            runtime,
            spec,
            expected_policy_id=builder.REFERENCE_POLICY_ID,
            expected_reset_receipt_digest=row["trusted_reset_receipt"]["receipt_digest"],
        )
        result = verify_trusted_evaluation_receipt(
            row["trusted_evaluations"]["reference"],
            runtime,
            builder.EVALUATOR_ID,
            route["contract"]["scenario_type"],
            reference_run,
        )
        assert result["success"]
        assert result["gates"]["trigger_observed"]
        assert result["gates"]["causal_outcomes"]
        assert "notification_sent" in result["event_types"]
        expected_trigger = {
            "laundry_completion_notification": "laundry_cycle_finished",
            "mail_arrival_notification": "mail_delivered",
        }[route["contract"]["scenario_type"]]
        assert expected_trigger in result["event_types"]

        forged = deepcopy(row["trusted_replays"]["reference"])
        forged["receipt_digest"] = "0" * 64
        with pytest.raises(ConformanceError, match="receipt digest mismatch"):
            verify_trusted_replay_receipt(
                forged,
                runtime,
                spec,
                expected_policy_id=builder.REFERENCE_POLICY_ID,
                expected_reset_receipt_digest=row["trusted_reset_receipt"]["receipt_digest"],
            )

    validated = validator.validate_release(output, minimum_responsibilities=2, minimum_episodes=2)
    assert validated["status"] == "PASS"
    assert validated["trusted_replay_count"] == 10


def test_partial_capability_match_remains_fail_closed(tmp_path, monkeypatch):
    routes, contracts, variants = _fixture_inputs(tmp_path)
    payload = json.loads(contracts.read_text())
    payload["routes"][0]["support_status"] = "PARTIAL"
    payload["routes"][0]["capability_match"] = {"missing_requirements": ["events.away_started"]}
    contracts.write_text(json.dumps(payload))
    monkeypatch.setattr(builder, "CONTRACTS", contracts)
    monkeypatch.setattr(builder, "QUERY_VARIANTS", variants)
    monkeypatch.setattr(builder, "OUTPUT", tmp_path / "must-not-exist")
    monkeypatch.setattr(builder, "VARIANTS_PER_RESPONSIBILITY", 1)

    with pytest.raises(RuntimeError, match="formal_release_blocked_by_partial_capability_matches"):
        builder.build()
    assert not (tmp_path / "must-not-exist").exists()
