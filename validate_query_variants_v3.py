#!/usr/bin/env python3
"""Fail-closed validation for reviewed multi-surface Query sets."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent
MAPPING = ROOT / "generated" / "responsibility_backend_mapping_v2.json"
QUERIES = ROOT / "responsibility_ai_coding_v1" / "FULL_QUERY_VARIANTS_V3_REVIEWED.jsonl"
FORMS = {"direct", "contextual", "preference", "colloquial"}
FORBIDDEN = re.compile(
    r"\b(?:episode|oracle|reward|evaluator|score|thermostat|setpoint|sensor|actuator|"
    r"all the rooms|every room|at all times|don['’]t ever|stuffy|cozy|comfortable)\b",
    re.IGNORECASE,
)
MAINTAIN = re.compile(r"\b(?:keep|stay|remain)\b", re.IGNORECASE)
WARMTH = re.compile(r"\bwarm\b", re.IGNORECASE)
EVENING = re.compile(r"\bevenings?\b", re.IGNORECASE)
DEMONSTRATIVE_SCOPE = re.compile(r"\b(?:these|those) rooms\b", re.IGNORECASE)
CONTEXT_KEYS = {"room_refs", "named_period_refs"}


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def validate(path: Path = QUERIES) -> dict:
    mapping = json.loads(MAPPING.read_text(encoding="utf-8"))
    full_ids = [row["responsibility_id"] for row in mapping["mappings"] if row["support_status"] == "FULL"]
    rows = load_jsonl(path)
    errors = []
    if [row.get("responsibility_id") for row in rows] != full_ids:
        errors.append("coverage_or_order_mismatch_with_current_full_mapping")
    query_ids, texts, openings = [], [], []
    for row in rows:
        variants = row.get("query_variants", [])
        context_requirements = row.get("public_context_requirements", [])
        if len(context_requirements) != len(set(context_requirements)) or not set(context_requirements) <= CONTEXT_KEYS:
            errors.append(f"{row.get('responsibility_id')}:invalid_public_context_requirements")
        if "named_period_refs" not in context_requirements:
            errors.append(f"{row.get('responsibility_id')}:unbound_evening_window")
        if row.get("review_status") != "REVIEWED":
            errors.append(f"{row.get('responsibility_id')}:not_reviewed")
        if len(variants) != 4 or {item.get("form") for item in variants} != FORMS:
            errors.append(f"{row.get('responsibility_id')}:invalid_forms")
        for item in variants:
            text = item.get("text", "").strip()
            query_ids.append(item.get("query_id"))
            texts.append(text.casefold())
            openings.append(tuple(re.findall(r"[a-z0-9']+", text.casefold())[:2]))
            if not 5 <= len(text.split()) <= 18:
                errors.append(f"{item.get('query_id')}:length")
            if FORBIDDEN.search(text):
                errors.append(f"{item.get('query_id')}:forbidden_or_scope_broadening")
            if not MAINTAIN.search(text):
                errors.append(f"{item.get('query_id')}:missing_maintain_semantics")
            if not WARMTH.search(text):
                errors.append(f"{item.get('query_id')}:missing_thermal_warmth_target")
            if not EVENING.search(text):
                errors.append(f"{item.get('query_id')}:missing_evening_window")
            if DEMONSTRATIVE_SCOPE.search(text) and "room_refs" not in context_requirements:
                errors.append(f"{item.get('query_id')}:unbound_demonstrative_scope")
            if not re.search(r"[.!?]$", text):
                errors.append(f"{item.get('query_id')}:punctuation")
    if len(query_ids) != len(set(query_ids)):
        errors.append("duplicate_query_ids")
    if len(texts) != len(set(texts)):
        errors.append("duplicate_query_texts")
    if Counter(openings).most_common(1)[0][1] > 2:
        errors.append("opening_bigram_concentration")
    return {
        "valid": not errors,
        "responsibility_count": len(rows),
        "query_count": len(texts),
        "unique_opening_bigrams": len(set(openings)),
        "errors": errors,
    }


def main() -> None:
    result = validate()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
