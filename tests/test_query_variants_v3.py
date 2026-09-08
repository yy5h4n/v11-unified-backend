from pathlib import Path
import json

from validate_query_variants_v3 import QUERIES, validate


ROOT = Path(__file__).resolve().parents[1]


def test_reviewed_query_variants_pass_all_gates():
    result = validate()
    assert result == {
        "valid": True,
        "responsibility_count": 2,
        "query_count": 8,
        "unique_opening_bigrams": 7,
        "errors": [],
    }


def test_stale_scope_broadening_kimi_candidates_fail_closed():
    candidate = ROOT / "responsibility_ai_coding_v1" / "FULL_QUERY_VARIANTS_V3_KIMI.jsonl"
    result = validate(candidate)
    assert result["valid"] is False
    assert "coverage_or_order_mismatch_with_current_full_mapping" in result["errors"]
    assert any(error.endswith(":forbidden_or_scope_broadening") for error in result["errors"])


def test_query_file_is_the_canonical_reviewed_artifact():
    assert QUERIES.name == "FULL_QUERY_VARIANTS_V3_REVIEWED.jsonl"


def test_semantic_attacks_fail_closed(tmp_path):
    rows = [json.loads(line) for line in QUERIES.read_text().splitlines() if line.strip()]
    rows[0]["query_variants"][2]["text"] = "A warm kitchen in the evening is important to me."
    rows[0]["query_variants"][3]["text"] = "Could you keep the kitchen cozy once evening rolls around?"
    rows[1]["public_context_requirements"] = ["named_period_refs"]
    candidate = tmp_path / "semantic_attacks.jsonl"
    candidate.write_text("".join(json.dumps(row) + "\n" for row in rows))
    result = validate(candidate)
    assert result["valid"] is False
    assert any(error.endswith(":missing_maintain_semantics") for error in result["errors"])
    assert any(error.endswith(":forbidden_or_scope_broadening") for error in result["errors"])
    assert any(error.endswith(":unbound_demonstrative_scope") for error in result["errors"])


def test_missing_evening_binding_fails_closed(tmp_path):
    rows = [json.loads(line) for line in QUERIES.read_text().splitlines() if line.strip()]
    rows[0]["public_context_requirements"] = []
    candidate = tmp_path / "missing_evening_binding.jsonl"
    candidate.write_text("".join(json.dumps(row) + "\n" for row in rows))
    result = validate(candidate)
    assert result["valid"] is False
    assert any(error.endswith(":unbound_evening_window") for error in result["errors"])
