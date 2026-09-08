"""Pre-adjudication agreement for batch 2 under coding schema v2."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FIELDS = (
    "construct_type",
    "abstraction_level",
    "relation_to_extracted_construct",
    "standing_responsibility_support",
    "confidence",
)
SLOTS = (
    "actor",
    "beneficiary",
    "desired_state",
    "persistence_or_recurrence",
    "failure_meaning",
    "override_or_release",
)


def load(name: str) -> dict[str, dict]:
    rows = [
        json.loads(line)
        for line in (ROOT / name).read_text().splitlines()
        if line.strip()
    ]
    out = {row["evidence_id"]: row for row in rows}
    if len(out) != len(rows):
        raise ValueError(f"duplicate ID in {name}")
    return out


def kappa(a: list[str], b: list[str]) -> float | None:
    observed = sum(x == y for x, y in zip(a, b)) / len(a)
    ca, cb = Counter(a), Counter(b)
    expected = sum(
        (ca[label] / len(a)) * (cb[label] / len(b)) for label in set(ca) | set(cb)
    )
    if expected == 1.0:
        return 1.0 if observed == 1.0 else None
    return (observed - expected) / (1.0 - expected)


def summarize(ids: list[str], a: dict, b: dict, getter) -> dict:
    la = [getter(a[i]) for i in ids]
    lb = [getter(b[i]) for i in ids]
    disagree = [i for i in ids if getter(a[i]) != getter(b[i])]
    return {
        "exact_agreement_count": len(ids) - len(disagree),
        "total": len(ids),
        "exact_agreement_rate": (len(ids) - len(disagree)) / len(ids),
        "cohen_kappa": kappa(la, lb),
        "disagreeing_ids": disagree,
        "label_counts_a": dict(Counter(la)),
        "label_counts_b": dict(Counter(lb)),
    }


def main() -> None:
    a = load("annotation_batch2_a.jsonl")
    b = load("annotation_batch2_b.jsonl")
    if set(a) != set(b):
        raise ValueError("annotation IDs differ")
    ids = sorted(a)

    fields = {
        field: summarize(ids, a, b, lambda row, f=field: row[f]) for field in FIELDS
    }
    slots = {
        slot: summarize(
            ids,
            a,
            b,
            lambda row, s=slot: row["responsibility_slot_completeness"][s],
        )
        for slot in SLOTS
    }

    hypothesis_presence = summarize(
        ids,
        a,
        b,
        lambda row: "present" if row["standing_responsibility_hypothesis"] else "absent",
    )
    type_issues = []
    for coder, annotations in (("a", a), ("b", b)):
        for evidence_id, row in annotations.items():
            for field in ("constraints", "tradeoffs", "means_or_device"):
                if not isinstance(row[field], list):
                    type_issues.append(
                        {"coder": coder, "evidence_id": evidence_id, "field": field}
                    )

    report = {
        "report_version": "pilot-batch2-pre-adjudication-v2",
        "annotation_kind": "AI schema dry run; not formal human intercoder reliability",
        "n_evidence_units": len(ids),
        "categorical_fields": fields,
        "responsibility_slot_completeness": slots,
        "standing_responsibility_hypothesis_presence": hypothesis_presence,
        "schema_type_issues": type_issues,
    }
    (ROOT / "agreement_batch2_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

