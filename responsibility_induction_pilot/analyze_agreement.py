"""Compute transparent pre-adjudication agreement for the pilot annotations."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CATEGORICAL_FIELDS = (
    "construct_type",
    "abstraction_level",
    "evidence_relation",
    "confidence",
)


def read_jsonl(path: Path) -> dict[str, dict]:
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    by_id = {row["evidence_id"]: row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError(f"duplicate evidence_id in {path}")
    return by_id


def cohen_kappa(a: list[str], b: list[str]) -> float | None:
    if len(a) != len(b) or not a:
        raise ValueError("paired non-empty labels required")
    observed = sum(x == y for x, y in zip(a, b)) / len(a)
    ca, cb = Counter(a), Counter(b)
    labels = set(ca) | set(cb)
    expected = sum((ca[x] / len(a)) * (cb[x] / len(b)) for x in labels)
    if expected == 1.0:
        return 1.0 if observed == 1.0 else None
    return (observed - expected) / (1.0 - expected)


def main() -> None:
    ann_a = read_jsonl(ROOT / "annotation_a.jsonl")
    ann_b = read_jsonl(ROOT / "annotation_b.jsonl")
    if set(ann_a) != set(ann_b):
        raise ValueError("annotation evidence IDs do not match")

    ids = sorted(ann_a)
    field_reports = {}
    disagreements = []
    for field in CATEGORICAL_FIELDS:
        labels_a = [ann_a[i][field] for i in ids]
        labels_b = [ann_b[i][field] for i in ids]
        disagreeing_ids = [i for i in ids if ann_a[i][field] != ann_b[i][field]]
        field_reports[field] = {
            "exact_agreement_count": len(ids) - len(disagreeing_ids),
            "total": len(ids),
            "exact_agreement_rate": (len(ids) - len(disagreeing_ids)) / len(ids),
            "cohen_kappa": cohen_kappa(labels_a, labels_b),
            "disagreeing_ids": disagreeing_ids,
            "label_counts_a": dict(Counter(labels_a)),
            "label_counts_b": dict(Counter(labels_b)),
        }
        for evidence_id in disagreeing_ids:
            disagreements.append(
                {
                    "evidence_id": evidence_id,
                    "field": field,
                    "annotation_a": ann_a[evidence_id][field],
                    "annotation_b": ann_b[evidence_id][field],
                }
            )

    candidate_presence_a = [ann_a[i]["candidate_statement"] is not None for i in ids]
    candidate_presence_b = [ann_b[i]["candidate_statement"] is not None for i in ids]
    candidate_presence_agreement = sum(
        x == y for x, y in zip(candidate_presence_a, candidate_presence_b)
    )

    type_issues = []
    for coder, annotations in (("a", ann_a), ("b", ann_b)):
        for evidence_id, row in annotations.items():
            for field in ("constraints", "tradeoffs"):
                if not isinstance(row[field], str):
                    type_issues.append(
                        {
                            "coder": coder,
                            "evidence_id": evidence_id,
                            "field": field,
                            "actual_type": type(row[field]).__name__,
                        }
                    )

    report = {
        "report_version": "pilot-pre-adjudication-agreement-v1",
        "annotation_kind": "AI schema dry run; not formal human intercoder reliability",
        "n_evidence_units": len(ids),
        "categorical_fields": field_reports,
        "candidate_statement_presence": {
            "exact_agreement_count": candidate_presence_agreement,
            "total": len(ids),
            "exact_agreement_rate": candidate_presence_agreement / len(ids),
        },
        "all_categorical_disagreements": disagreements,
        "schema_type_issues": type_issues,
    }
    (ROOT / "agreement_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )

    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

