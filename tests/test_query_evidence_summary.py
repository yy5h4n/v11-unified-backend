from pathlib import Path

from tools.summarize_evidence_query_release import summarize


ROOT = Path(__file__).resolve().parents[1]
BATCHES = [
    ROOT / "generated/query_construction_v2/development_batch_v1.json",
    ROOT / "generated/query_construction_v2/validation_batch_v1.json",
    ROOT / "generated/query_construction_v2/core_transfer_batch_v1.json",
]


def test_release_summary_preserves_denominators_and_costs():
    result = summarize(BATCHES)
    totals = result["combined_descriptive_counts"]
    assert totals["attempted_items"] == 11
    assert totals["accepted_items"] == 4
    assert totals["accepted_core_items"] == 3
    assert totals["accepted_calibration_items"] == 1
    assert totals["rejected_items"] == 5
    assert totals["pending_items"] == 2
    assert result["recorded_cost_totals"] == {
        "provider_calls": 0,
        "provider_tokens": 0,
        "native_runs": 18,
        "unknown_cost_events": 0,
    }


def test_no_model_pass_rate_is_invented_when_no_exact_query_was_run():
    result = summarize(BATCHES)
    assert result["model_execution"] == {
        "evaluated_items": 0,
        "passed_items": 0,
        "pass_rate": None,
    }
    assert "not estimates" in result["claim_boundary"]
