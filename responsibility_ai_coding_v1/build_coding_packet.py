from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path


HERE = Path(__file__).resolve().parent
PARENT = HERE.parent
PILOT = PARENT / "responsibility_induction_pilot"
CSV_PATH = HERE / "source_snapshots" / "rules_nl_en.csv"


def dump_json(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def dump_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main() -> None:
    source_cards = json.loads((PILOT / "corpus_cards.json").read_text(encoding="utf-8"))["corpora"]
    csv_hash = hashlib.sha256(CSV_PATH.read_bytes()).hexdigest()
    with CSV_PATH.open(encoding="cp1252", newline="") as handle:
        raw_rules = list(csv.DictReader(handle, delimiter=";"))
    if len(raw_rules) != 203:
        raise RuntimeError(f"expected 203 User Needs rules, found {len(raw_rules)}")

    ledger: list[dict] = []
    for row_index, row in enumerate(raw_rules, start=2):
        user_id = row["user"].strip()
        ledger.append({
            "evidence_id": f"userneeds_u{int(user_id):02d}_r{row_index:03d}",
            "source_id": "mattioli_2023_user_needs_rules",
            "source_type": "participant_authored_hypothetical_rule",
            "independence_unit_id": f"mattioli-user-{int(user_id):02d}",
            "study_cluster_id": "mattioli_2023_recruitment_pool",
            "participant_or_household_id": f"User-{int(user_id)}",
            "location": f"rules_nl_en.csv row {row_index} at fixed commit",
            "evidence_form": "verbatim_participant_authored_rule",
            "evidence_text": row["nl"].strip(),
            "rule_title": row["rule"].strip(),
            "authorship_or_status": "participant-authored hypothetical desired automation; not deployed",
            "evidence_limits": [
                "does not establish deployment or successful use",
                "does not establish persistence, accountability, failure meaning, or release unless explicit in text",
                "supports desired behavior but not population prevalence",
            ],
            "partition": "semantic_discovery",
            "source_snapshot_sha256": csv_hash,
        })

    # Preserve the already-audited non-User-Needs units verbatim. The four old
    # curated User Needs rows are excluded because the full fixed-commit CSV is
    # now authoritative and would otherwise duplicate them.
    for path in (PILOT / "evidence_units.jsonl", PILOT / "evidence_units_batch2.jsonl"):
        for row in read_jsonl(path):
            if row["source_id"] == "mattioli_2023_user_needs_rules":
                continue
            row = dict(row)
            row["independence_unit_id"] = row.get("participant_or_household_id") or f"unresolved-{row['evidence_id']}"
            row["study_cluster_id"] = row["source_id"]
            row["partition"] = "semantic_development"
            row["evidence_limits"] = [row.pop("does_not_support", "unknown limitations")]
            ledger.append(row)

    ids = [row["evidence_id"] for row in ledger]
    if len(ids) != len(set(ids)):
        raise RuntimeError("duplicate evidence_id")
    ledger.sort(key=lambda row: row["evidence_id"])

    audit = {
        "schema_version": "responsibility-ai-coding-source-audit-v1",
        "coding_track": "dual_ai_surrogate_coding",
        "not_human_validation": True,
        "sources": source_cards,
        "snapshots": [{
            "source_id": "mattioli_2023_user_needs_rules",
            "path": "source_snapshots/rules_nl_en.csv",
            "encoding": "cp1252",
            "delimiter": ";",
            "rows": len(raw_rules),
            "participants": len({row["user"] for row in raw_rules}),
            "sha256": csv_hash,
            "source_commit": "15024be10d76c69e4189ae795f1b0bdb4583bc99",
        }],
        "ledger_units": len(ledger),
        "independence_units": len({row["independence_unit_id"] for row in ledger}),
    }
    partitions = {
        "schema_version": "semantic-partition-manifest-v1",
        "semantic_discovery": ["mattioli_2023_user_needs_rules"],
        "semantic_development": sorted({row["source_id"] for row in ledger if row["partition"] == "semantic_development"}),
        "semantic_confirmatory": [],
        "confirmatory_status": "missing_untouched_source_components",
        "rule": "all evidence from one study/recruitment pool remains in one semantic partition",
    }
    packet = [{
        "evidence_id": row["evidence_id"],
        "source_type": row["source_type"],
        "partition": row["partition"],
        "evidence_text": row["evidence_text"],
        "authorship_or_status": row["authorship_or_status"],
        "evidence_limits": row["evidence_limits"],
    } for row in ledger]

    dump_json(HERE / "SOURCE_AUDIT.json", audit)
    dump_json(HERE / "PARTITION_MANIFEST.json", partitions)
    dump_jsonl(HERE / "EVIDENCE_LEDGER.jsonl", ledger)
    dump_jsonl(HERE / "CODING_PACKET.jsonl", packet)
    print(json.dumps({"ledger_units": len(ledger), "coding_units": len(packet), "confirmatory_units": 0}))


if __name__ == "__main__":
    main()
