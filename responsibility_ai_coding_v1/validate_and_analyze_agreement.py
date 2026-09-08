#!/usr/bin/env python3
"""Validate Luna/Kimi coding files and analyze agreement.

The five Kimi batch files are treated as an untrusted, partitioned JSONL
input.  Nothing is merged until every batch is present, individually valid,
non-overlapping, and their union is exactly the Luna evidence-id set.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable


BATCH_NAMES = tuple(f"ANNOTATIONS_KIMI_BATCH_{n:02d}.jsonl" for n in range(1, 6))
MERGED_NAME = "ANNOTATIONS_KIMI.jsonl"
REPORT_NAME = "AGREEMENT_REPORT.json"


def read_json(path: Path) -> tuple[Any | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, "file_not_found"
    except (OSError, UnicodeDecodeError) as exc:
        return None, f"read_error: {exc}"
    except json.JSONDecodeError as exc:
        return None, f"invalid_json: line {exc.lineno}, column {exc.colno}"


def read_jsonl(path: Path) -> dict[str, Any]:
    """Read JSONL without stopping at the first bad line."""
    result: dict[str, Any] = {
        "path": path.name,
        "exists": path.is_file(),
        "line_count": 0,
        "records": [],
        "parse_errors": [],
    }
    if not result["exists"]:
        result["status"] = "missing"
        return result
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line_number, raw in enumerate(handle, 1):
                result["line_count"] += 1
                if not raw.strip():
                    result["parse_errors"].append(
                        {"line": line_number, "error": "blank_line"}
                    )
                    continue
                try:
                    result["records"].append((line_number, json.loads(raw)))
                except json.JSONDecodeError as exc:
                    result["parse_errors"].append(
                        {
                            "line": line_number,
                            "error": f"invalid_json: column {exc.colno}",
                        }
                    )
    except (OSError, UnicodeDecodeError) as exc:
        result["parse_errors"].append({"line": None, "error": f"read_error: {exc}"})
    result["status"] = "read"
    return result


def schema_rules(schema: dict[str, Any]) -> tuple[list[str], dict[str, set[str]]]:
    required = schema.get("required_fields")
    if not isinstance(required, dict) or not required:
        raise ValueError("CODING_SCHEMA.json has no usable required_fields object")
    allowed: dict[str, set[str]] = {}
    for field, spec in required.items():
        if isinstance(spec, list) and all(isinstance(item, str) for item in spec):
            allowed[field] = set(spec)
        else:
            allowed[field] = set()
    return list(required), allowed


def validate_record(
    record: Any,
    required: list[str],
    allowed: dict[str, set[str]],
) -> list[str]:
    """Return all validation errors for one record.

    The schema represents enum domains as lists.  ``semantic_content`` is
    accepted as either one enum string or a list of enum strings, because the
    two supplied coder tracks use those equivalent representations.  All
    other enum fields remain scalar.
    """
    errors: list[str] = []
    if not isinstance(record, dict):
        return ["record_not_object"]
    expected = set(required)
    actual = set(record)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    if missing:
        errors.append("missing_fields:" + ",".join(missing))
    if extra:
        errors.append("extra_fields:" + ",".join(extra))

    evidence_id = record.get("evidence_id")
    if not isinstance(evidence_id, str) or not evidence_id:
        errors.append("evidence_id_not_nonempty_string")

    for field in required:
        if field not in record:
            continue
        if field == "evidence_id":
            # The schema documents this field as the string type, not an enum.
            continue
        value = record[field]
        if field == "candidate_responsibility":
            if value is not None and not isinstance(value, str):
                errors.append("candidate_responsibility_not_string_or_null")
            continue
        if field == "rationale":
            if not isinstance(value, str):
                errors.append("rationale_not_string")
            elif len(value.split()) > 18:
                errors.append("rationale_over_18_words")
            continue
        domain = allowed.get(field, set())
        if field == "semantic_content" and isinstance(value, list):
            if not value:
                errors.append("semantic_content_empty_list")
            elif not all(isinstance(item, str) and item in domain for item in value):
                errors.append("semantic_content_enum_violation")
        elif not isinstance(value, str) or value not in domain:
            errors.append(f"{field}_enum_violation")
    return errors


def id_of(record: Any) -> str | None:
    return record.get("evidence_id") if isinstance(record, dict) else None


def index_ids(records: Iterable[tuple[int, Any]]) -> tuple[list[str], list[str], list[dict[str, Any]]]:
    ids: list[str] = []
    duplicates: list[str] = []
    malformed: list[dict[str, Any]] = []
    seen: set[str] = set()
    for line_number, record in records:
        evidence_id = id_of(record)
        if evidence_id is None:
            malformed.append({"line": line_number, "error": "missing_evidence_id"})
            continue
        ids.append(evidence_id)
        if evidence_id in seen and evidence_id not in duplicates:
            duplicates.append(evidence_id)
        seen.add(evidence_id)
    return ids, duplicates, malformed


def validate_file(
    loaded: dict[str, Any],
    required: list[str],
    allowed: dict[str, set[str]],
    expected_ids: set[str] | None = None,
    canonical_index: dict[str, int] | None = None,
) -> dict[str, Any]:
    records = loaded.get("records", [])
    ids, duplicates, malformed_ids = index_ids(records)
    record_errors: list[dict[str, Any]] = list(malformed_ids)
    for line_number, record in records:
        errors = validate_record(record, required, allowed)
        if errors:
            record_errors.append(
                {"line": line_number, "evidence_id": id_of(record), "errors": errors}
            )
    unexpected = sorted(set(ids) - expected_ids) if expected_ids is not None else []
    order_valid = True
    order_error: dict[str, Any] | None = None
    if canonical_index is not None and ids:
        positions = [canonical_index.get(evidence_id, -1) for evidence_id in ids]
        if any(position < 0 for position in positions):
            order_valid = False
            order_error = {"error": "id_not_in_canonical_order"}
        elif positions != sorted(positions):
            order_valid = False
            order_error = {"error": "records_not_in_canonical_order"}
    loaded["ids"] = ids
    loaded["duplicate_ids"] = duplicates
    loaded["unexpected_ids"] = unexpected
    loaded["record_errors"] = record_errors
    loaded["order_valid"] = order_valid
    if order_error:
        loaded["order_error"] = order_error
    loaded["schema_valid"] = not loaded.get("parse_errors") and not record_errors
    loaded["valid"] = bool(
        loaded.get("exists")
        and loaded.get("schema_valid")
        and not duplicates
        and not unexpected
        and order_valid
        and ids
    )
    return loaded


def normalized_value(field: str, value: Any) -> Any:
    """Normalize only semantic_content's scalar/list representation."""
    if field == "semantic_content":
        if isinstance(value, list):
            return tuple(sorted(value))
        return (value,)
    return value


def value_key(value: Any) -> str:
    if isinstance(value, tuple):
        value = list(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def cohen_kappa(left: list[Any], right: list[Any]) -> float | None:
    if not left or len(left) != len(right):
        return None
    n = len(left)
    observed = sum(a == b for a, b in zip(left, right)) / n
    left_counts = Counter(value_key(value) for value in left)
    right_counts = Counter(value_key(value) for value in right)
    labels = set(left_counts) | set(right_counts)
    expected = sum(left_counts[label] * right_counts[label] for label in labels) / (n * n)
    if math.isclose(expected, 1.0):
        return 1.0 if math.isclose(observed, 1.0) else 0.0
    return (observed - expected) / (1.0 - expected)


def disagreement_matrix(left: list[Any], right: list[Any]) -> dict[str, dict[str, int]]:
    matrix: dict[str, dict[str, int]] = {}
    for left_value, right_value in zip(left, right):
        row = value_key(left_value)
        column = value_key(right_value)
        matrix.setdefault(row, {})[column] = matrix.setdefault(row, {}).get(column, 0) + 1
    return matrix


def analyze_agreement(
    luna_records: list[dict[str, Any]], kimi_records: list[dict[str, Any]], required: list[str]
) -> dict[str, Any]:
    by_luna = {record["evidence_id"]: record for record in luna_records}
    by_kimi = {record["evidence_id"]: record for record in kimi_records}
    ids = [record["evidence_id"] for record in luna_records]
    fields = [field for field in required if field != "evidence_id"]
    field_report: dict[str, Any] = {}
    for field in fields:
        left = [normalized_value(field, by_luna[evidence_id][field]) for evidence_id in ids]
        right = [normalized_value(field, by_kimi[evidence_id][field]) for evidence_id in ids]
        matches = sum(a == b for a, b in zip(left, right))
        field_report[field] = {
            "n": len(ids),
            "exact_matches": matches,
            "exact_agreement": matches / len(ids) if ids else None,
            "cohen_kappa": cohen_kappa(left, right),
            "disagreement_matrix": disagreement_matrix(left, right),
        }
    standing = "standing_responsibility_support"
    standing_disagreements = []
    for evidence_id in ids:
        luna_value = by_luna[evidence_id][standing]
        kimi_value = by_kimi[evidence_id][standing]
        if luna_value != kimi_value:
            standing_disagreements.append(
                {
                    "evidence_id": evidence_id,
                    "luna": luna_value,
                    "kimi": kimi_value,
                }
            )
    return {
        "n_records": len(ids),
        "fields": field_report,
        "standing_responsibility_support_disagreements": standing_disagreements,
    }


def build_report(root: Path) -> tuple[dict[str, Any], list[str] | None]:
    schema, schema_error = read_json(root / "CODING_SCHEMA.json")
    if schema_error or not isinstance(schema, dict):
        report = {"status": "blocked", "errors": [schema_error or "schema_not_object"]}
        return report, None
    try:
        required, allowed = schema_rules(schema)
    except ValueError as exc:
        return {"status": "blocked", "errors": [str(exc)]}, None

    luna_loaded = read_jsonl(root / "ANNOTATIONS_LUNA.jsonl")
    luna_loaded = validate_file(luna_loaded, required, allowed)
    luna_ids = luna_loaded.get("ids", [])
    luna_id_set = set(luna_ids)
    canonical_index = {evidence_id: index for index, evidence_id in enumerate(luna_ids)}
    luna_loaded["duplicate_ids"] = sorted(
        evidence_id for evidence_id, count in Counter(luna_ids).items() if count > 1
    )
    luna_loaded["canonical_order_valid"] = (
        bool(luna_ids) and len(luna_ids) == len(luna_id_set) and not luna_loaded["duplicate_ids"]
    )
    luna_loaded["valid"] = bool(luna_loaded.get("valid") and luna_loaded["canonical_order_valid"])

    batches: dict[str, dict[str, Any]] = {}
    all_batch_ids: list[str] = []
    for name in BATCH_NAMES:
        loaded = read_jsonl(root / name)
        loaded = validate_file(
            loaded,
            required,
            allowed,
            expected_ids=luna_id_set,
            canonical_index=canonical_index,
        )
        batches[name] = loaded
        all_batch_ids.extend(loaded.get("ids", []))

    counts = Counter(all_batch_ids)
    cross_batch_duplicates = sorted(evidence_id for evidence_id, count in counts.items() if count > 1)
    covered_ids = set(all_batch_ids)
    coverage_missing = sorted(luna_id_set - covered_ids)
    coverage_unexpected = sorted(covered_ids - luna_id_set)
    batches_valid = all(batch.get("valid", False) for batch in batches.values())
    complete_valid_batches = bool(
        luna_loaded.get("valid")
        and len(batches) == 5
        and batches_valid
        and not cross_batch_duplicates
        and not coverage_missing
        and not coverage_unexpected
        and len(all_batch_ids) == len(luna_ids)
    )

    report: dict[str, Any] = {
        "schema_version": schema.get("schema_version"),
        "status": "ready" if complete_valid_batches else "incomplete_or_invalid",
        "canonical_source": "ANNOTATIONS_LUNA.jsonl",
        "canonical": {
            "exists": luna_loaded.get("exists", False),
            "line_count": luna_loaded.get("line_count", 0),
            "schema_valid": luna_loaded.get("schema_valid", False),
            "valid": luna_loaded.get("valid", False),
            "record_count": len(luna_ids),
            "duplicate_ids": luna_loaded.get("duplicate_ids", []),
        },
        "batches": {
            name: {
                "exists": data.get("exists", False),
                "line_count": data.get("line_count", 0),
                "record_count": len(data.get("ids", [])),
                "status": data.get("status"),
                "schema_valid": data.get("schema_valid", False),
                "valid": data.get("valid", False),
                "parse_errors": data.get("parse_errors", []),
                "record_errors": data.get("record_errors", []),
                "duplicate_ids": data.get("duplicate_ids", []),
                "unexpected_ids": data.get("unexpected_ids", []),
                "order_valid": data.get("order_valid", False),
                **({"order_error": data["order_error"]} if "order_error" in data else {}),
            }
            for name, data in batches.items()
        },
        "coverage": {
            "all_five_batches_present_and_valid": complete_valid_batches,
            "cross_batch_duplicate_ids": cross_batch_duplicates,
            "missing_from_kimi_union": coverage_missing,
            "unexpected_in_kimi_union": coverage_unexpected,
            "kimi_union_record_count": len(all_batch_ids),
        },
        "merged_output": None,
        "agreement": None,
    }
    if complete_valid_batches:
        merged_records = [
            record
            for _, data in batches.items()
            for _, record in data["records"]
        ]
        merged_records.sort(key=lambda record: canonical_index[record["evidence_id"]])
        report["agreement"] = analyze_agreement(
            [record for _, record in luna_loaded["records"]], merged_records, required
        )
        report["merged_output"] = MERGED_NAME
        return report, [json.dumps(record, ensure_ascii=False, separators=(",", ":")) for record in merged_records]
    return report, None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parent,
        help="directory containing schema, Luna output, and Kimi batches",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and print the report without writing report or merged output",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()
    report, merged_lines = build_report(root)
    if not args.dry_run:
        (root / REPORT_NAME).write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        if merged_lines is not None:
            (root / MERGED_NAME).write_text("\n".join(merged_lines) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
