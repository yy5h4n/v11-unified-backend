import copy
import json
from pathlib import Path

import pytest

from query_construction.evidence_batch import validate_batch


FIXTURE = Path(__file__).resolve().parents[1] / "generated/query_construction_v2/development_batch_v1.json"
VALIDATION = Path(__file__).resolve().parents[1] / "generated/query_construction_v2/validation_batch_v1.json"
CORE_TRANSFER = Path(__file__).resolve().parents[1] / "generated/query_construction_v2/core_transfer_batch_v1.json"


def batch():
    return json.loads(FIXTURE.read_text())


def test_checked_in_batch_is_denominator_explicit_and_not_model_certified():
    result = validate_batch(batch(), base_dir=FIXTURE.parent)
    assert result["attempted_items"] == 6
    assert result["decision_counts"] == {"accepted_core": 2, "rejected": 3, "pending": 1}
    assert result["costs"]["provider_calls"] == 0
    assert result["accepted_items"] == 2


def test_constructed_change_cannot_be_called_human_explicit():
    value = batch()
    value["items"][0]["classification"] = "human_explicit"
    with pytest.raises(ValueError, match="constructed meaning"):
        validate_batch(value)


def test_validation_source_must_have_been_frozen_before_reading():
    value = batch()
    value["items"][0]["split"] = "validation"
    with pytest.raises(ValueError, match="not frozen"):
        validate_batch(value)


def test_passing_model_gate_cannot_override_failed_backend_gate():
    value = batch()
    item = value["items"][2]
    item["decision"] = "accepted_core"
    item["gates"]["model_execution"] = {
        "status": "passed", "owner": "model", "evidence": "hypothetical passing run"
    }
    with pytest.raises(ValueError, match="unpassed non-model"):
        validate_batch(value)


def test_execution_evidence_digest_is_verified():
    value = batch()
    value["items"][0]["execution_evidence"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="stale execution evidence"):
        validate_batch(value, base_dir=FIXTURE.parent)


def test_gate_ownership_cannot_mislabel_data_failure_as_model_failure():
    value = batch()
    value["items"][2]["gates"]["backend_support"]["owner"] = "model"
    with pytest.raises(ValueError, match="only model_execution"):
        validate_batch(value)


def test_calibration_acceptance_requires_all_objective_gates_except_mechanism():
    value = batch()
    item = value["items"][0]
    item["decision"] = "accepted_calibration"
    item["gates"]["mechanism_relevance"] = {
        "status": "not_applicable", "owner": "unresolved",
        "evidence": "explicitly a fixed-time calibration rather than a mechanism claim",
    }
    validate_batch(value)
    item["gates"]["native_execution"]["status"] = "pending"
    with pytest.raises(ValueError, match="unpassed non-model"):
        validate_batch(value)


def test_rule_frozen_validation_batch_reports_failures_without_cherry_picking():
    value = json.loads(VALIDATION.read_text())
    result = validate_batch(value, base_dir=VALIDATION.parent)
    assert result["attempted_items"] == 4
    assert result["decision_counts"] == {"accepted_calibration": 1, "rejected": 2, "pending": 1}
    assert result["costs"]["provider_calls"] == 0


def test_frozen_core_transfer_has_fresh_nontrivial_native_evidence():
    value = json.loads(CORE_TRANSFER.read_text())
    result = validate_batch(value, base_dir=CORE_TRANSFER.parent)
    assert result["decision_counts"] == {"accepted_core": 1}
    assert result["costs"] == {
        "provider_calls": 0,
        "provider_tokens": 0,
        "native_runs": 4,
        "unknown_cost_events": 0,
        "scope": "Four fresh local native Modelica trajectories; no provider calls.",
    }


def test_frozen_acceptance_cannot_use_digest_only_legacy_evidence():
    value = json.loads(CORE_TRANSFER.read_text())
    value["items"][0]["execution_evidence"]["verification"]["mode"] = "digest_only_legacy"
    with pytest.raises(ValueError, match="must be recomputed"):
        validate_batch(value, base_dir=CORE_TRANSFER.parent)


def test_decision_label_must_follow_objective_gate_state():
    value = batch()
    value["items"][3]["decision"] = "pending"
    with pytest.raises(ValueError, match="pending item"):
        validate_batch(value)
    value = batch()
    value["items"][3]["gates"]["evaluation_validity"]["status"] = "pending"
    value["items"][3]["decision"] = "rejected"
    with pytest.raises(ValueError, match="rejected item"):
        validate_batch(value)


def test_inference_and_synthesis_labels_require_matching_provenance():
    value = batch()
    item = value["items"][0]
    item["construction"]["changes"] = []
    with pytest.raises(ValueError, match="must declare"):
        validate_batch(value)
    value = batch()
    item = value["items"][3]
    item["classification"] = "domain_or_product_inferred"
    item["construction"]["mode"] = "evidence_derived"
    with pytest.raises(ValueError, match="product/domain source"):
        validate_batch(value)
