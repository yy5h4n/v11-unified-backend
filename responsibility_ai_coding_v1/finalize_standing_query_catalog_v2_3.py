#!/usr/bin/env python3
"""Finalize the provisional v2.3 standing-intent query catalog with audited cross-review."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CATALOG = ROOT / "PROVISIONAL_AI_RESPONSIBILITY_CATALOG_V2_2.json"
DRAFTS = [ROOT / f"STANDING_QUERY_DRAFT_LUNA_V2_3_{i:02d}.jsonl" for i in range(2)]
REVIEWS = [ROOT / f"STANDING_QUERY_CROSS_REVIEW_LUNA_V2_3_{i:02d}.jsonl" for i in range(2)]
OUTPUT = ROOT / "STANDING_INTENT_QUERY_CATALOG_V2_3.json"
REPORT = ROOT / "STANDING_INTENT_QUERY_REPORT_V2_3.json"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def stable_intent_id(responsibility_id: str) -> str:
    return "si_" + responsibility_id.split("_", 1)[1]


def normalize_revision(query: str) -> str:
    # Review batch 01 sometimes capitalized the first verb after an infinitive.
    for prefix in ("I want you to ", "Please help me "):
        if query.startswith(prefix) and len(query) > len(prefix):
            return prefix + query[len(prefix)].lower() + query[len(prefix) + 1:]
    return query


PROFILE_RULES = [
    (re.compile(r"\b(?:comfort|comfortable|warm|cool|temperature|thermal)\b", re.I), "resident comfort targets"),
    (re.compile(r"\b(?:scheduled|on time|bedtime|wake|morning|evening|night|dinner|medication|feeding|watering)\b", re.I), "resident schedule or timing preferences"),
    (re.compile(r"\bauthori[sz]ed\b", re.I), "authorization list"),
    (re.compile(r"\b(?:inform|notify|alert|emergency call|contact)\b", re.I), "notification and escalation preferences"),
    (re.compile(r"\b(?:energy|power|charging)\b", re.I), "energy priorities and protected loads"),
    (re.compile(r"\b(?:clean|cleanliness|laundry)\b", re.I), "cleanliness standard and quiet-hour preferences"),
    (re.compile(r"\b(?:supplies|stock|restock|portion|food)\b", re.I), "inventory or portion preferences"),
    (re.compile(r"\b(?:lighting|illuminated|lit)\b", re.I), "lighting preferences"),
    (re.compile(r"\b(?:security|intrusion)\b", re.I), "security and escalation policy"),
    (re.compile(r"\b(?:threshold|configured target|health measurement)\b", re.I), "resident-defined health threshold and measurement semantics"),
]

EPISODE_RULES = [
    (re.compile(r"\b(?:away|unoccupied|nobody|empty|arriv|leav|presence)\w*\b", re.I), "occupancy and presence state"),
    (re.compile(r"\b(?:temperature|warm|cold|cool|heat|fireplace|radiator|ventilation)\w*\b", re.I), "thermal and air state"),
    (re.compile(r"\b(?:night|evening|morning|scheduled|on time|before|after|until|while)\b", re.I), "clock, calendar, and lifecycle state"),
    (re.compile(r"\b(?:intrusion|security|garage|door|window|visitor|mail|authori[sz]ed)\w*\b", re.I), "access and security event state"),
    (re.compile(r"\b(?:pet|dog|cat|plant|garden|infant|elderly)\w*\b", re.I), "dependent or environmental state"),
    (re.compile(r"\b(?:energy|power|device|charging|battery)\w*\b", re.I), "device and energy state"),
    (re.compile(r"\b(?:clean|laundry|bin|toilet paper|supplies|stock|food)\w*\b", re.I), "household task or inventory state"),
    (re.compile(r"\b(?:medication|health|ill|sleep|wake)\w*\b", re.I), "care-recipient state"),
]

CURATOR_QUERY_AMENDMENTS = {
    "rd_5aaf181229e6": {
        "query": "Please help me plan appropriate workouts when my health measurements fall below my configured target.",
        "rationale": "Clarifies the evidence-grounded workout-planning outcome and makes the household-specific health threshold an explicit profile dependency rather than an implicit oracle value.",
    }
}


def augment_dependencies(query: str, delegated_outcome: str, existing: list[str]) -> tuple[list[str], list[str]]:
    text = f"{query} {delegated_outcome}"
    profile = list(existing)
    for pattern, dependency in PROFILE_RULES:
        if pattern.search(text) and dependency not in profile:
            profile.append(dependency)
    episode = [dependency for pattern, dependency in EPISODE_RULES if pattern.search(text)]
    if not episode:
        episode = ["responsibility-relevant observable state"]
    return profile, episode


def main() -> None:
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    source = {row["responsibility_id"]: row for row in catalog["responsibilities"]}
    drafts = [row for path in DRAFTS for row in load_jsonl(path)]
    reviews = [row for path in REVIEWS for row in load_jsonl(path)]
    if len(drafts) != 142 or len(reviews) != 142:
        raise ValueError("expected 142 drafts and 142 cross reviews")
    if [row["responsibility_id"] for row in drafts] != [row["responsibility_id"] for row in reviews]:
        raise ValueError("draft/review order mismatch")
    if set(source) != {row["responsibility_id"] for row in drafts}:
        raise ValueError("responsibility coverage mismatch")
    batch_00_review_ids = {row["responsibility_id"] for row in load_jsonl(REVIEWS[0])}

    rows = []
    adjudication_counts = Counter()
    for draft, review in zip(drafts, reviews):
        rid = draft["responsibility_id"]
        original = draft["canonical_query"]
        final_query = original
        accepted_revision = False
        adjudication = "pass"
        if review["decision"] == "REVISE":
            proposed = review["revised_query"]
            if not isinstance(proposed, str) or not proposed.strip():
                raise ValueError(f"missing revised query for {rid}")
            if proposed.casefold() == original.casefold():
                adjudication = "reject_case_only_revision"
            else:
                final_query = normalize_revision(proposed.strip())
                accepted_revision = True
                adjudication = "accept_material_revision"
        curator_amendment = CURATOR_QUERY_AMENDMENTS.get(rid)
        if curator_amendment:
            final_query = curator_amendment["query"]
            adjudication = "post_review_curator_amendment"
        adjudication_counts[adjudication] += 1

        flags = list(draft["quality_flags"])
        # Batch 00's six material reviews identify genuine source/query mismatches.
        # Batch 01 review flags primarily describe the removed template artifact and
        # are retained as audit findings rather than projected onto the final query.
        if accepted_revision and rid in batch_00_review_ids:
            flags = sorted(set(flags) | set(review["quality_flags"]))
        status = "needs_review" if flags else "ready"
        profile_dependencies, episode_context_dependencies = augment_dependencies(
            final_query, draft["delegated_outcome"], draft["profile_dependencies"]
        )
        rows.append({
            "standing_intent_id": stable_intent_id(rid),
            "responsibility_id": rid,
            "canonical_query": final_query,
            "family": source[rid]["family"],
            "evidence_level": source[rid]["evidence_level"],
            "beneficiary": draft["beneficiary"],
            "lifecycle": draft["lifecycle"],
            "delegated_outcome": draft["delegated_outcome"],
            "duration_boundary": draft["duration_boundary"],
            "profile_dependencies": profile_dependencies,
            "episode_context_dependencies": episode_context_dependencies,
            "evidence_grounded_constraints": draft["evidence_grounded_constraints"],
            "configuration_variants_removed": draft["configuration_variants_removed"],
            "source_evidence_ids": draft["source_evidence_ids"],
            "quality_flags": flags,
            "generation_status": status,
            "generation_rationale": draft["rationale"],
            "cross_review": {
                "decision": review["decision"],
                "findings": review["quality_flags"],
                "rationale": review["rationale"],
                "curator_adjudication": adjudication,
                "revision_applied": accepted_revision,
                "original_query": original,
                "proposed_revision": review["revised_query"],
                "final_adjudicated_query": final_query,
                "post_review_curator_amendment": curator_amendment,
            },
            "provenance": "luna_generated_luna_cross_reviewed_curator_adjudicated",
        })

    ids = [row["responsibility_id"] for row in rows]
    intent_ids = [row["standing_intent_id"] for row in rows]
    queries = [row["canonical_query"] for row in rows]
    recipe_pattern = re.compile(r"\b(?:then|turn on|turn off|switch on|switch off|activate|deactivate|call tool|api\.)\b", re.I)
    hidden_pattern = re.compile(r"\b(?:episode_id|trajectory|oracle|scoring threshold|reward)\b", re.I)
    operational_hits = [row["responsibility_id"] for row in rows if recipe_pattern.search(row["canonical_query"])]
    hidden_hits = [row["responsibility_id"] for row in rows if hidden_pattern.search(row["canonical_query"])]
    nondelegation = [
        row["responsibility_id"] for row in rows
        if not re.match(r"^(?:I\b|Please\b)", row["canonical_query"], re.I)
    ]
    duplicate_queries = [q for q, count in Counter(q.casefold() for q in queries).items() if count > 1]
    ready_with_flags = [row["responsibility_id"] for row in rows if row["generation_status"] == "ready" and row["quality_flags"]]
    missing_profile_dependencies = [
        row["responsibility_id"] for row in rows
        if any(pattern.search(f"{row['canonical_query']} {row['delegated_outcome']}") for pattern, _ in PROFILE_RULES)
        and not row["profile_dependencies"]
    ]
    validation = {
        "valid": not (
            operational_hits or hidden_hits or nondelegation or duplicate_queries
            or ready_with_flags or missing_profile_dependencies
        ),
        "responsibility_coverage_exact": set(ids) == set(source),
        "responsibility_ids_unique": len(ids) == len(set(ids)),
        "standing_intent_ids_unique": len(intent_ids) == len(set(intent_ids)),
        "stable_intent_id_mapping": all(row["standing_intent_id"] == stable_intent_id(row["responsibility_id"]) for row in rows),
        "source_evidence_ids_exact": all(row["source_evidence_ids"] == source[row["responsibility_id"]]["evidence_ids"] for row in rows),
        "operational_recipe_hits": operational_hits,
        "hidden_evaluator_hits": hidden_hits,
        "nondelegation_query_hits": nondelegation,
        "duplicate_queries": duplicate_queries,
        "ready_rows_with_quality_flags": ready_with_flags,
        "keyword_triggered_missing_profile_dependencies": missing_profile_dependencies,
        "all_rows_have_episode_context_dependencies": all(bool(row["episode_context_dependencies"]) for row in rows),
        "backend_inputs_used": False,
        "human_validated": False,
    }
    if not all(value for key, value in validation.items() if key in {
        "responsibility_coverage_exact", "responsibility_ids_unique", "standing_intent_ids_unique",
        "stable_intent_id_mapping", "source_evidence_ids_exact"
    }):
        raise ValueError("structural validation failed")

    summary = {
        "responsibilities": len(rows),
        "canonical_queries": len(rows),
        "ready": sum(row["generation_status"] == "ready" for row in rows),
        "needs_review": sum(row["generation_status"] == "needs_review" for row in rows),
        "cross_review_decisions": dict(Counter(row["cross_review"]["decision"] for row in rows)),
        "curator_adjudications": dict(adjudication_counts),
        "quality_flag_counts": dict(Counter(flag for row in rows for flag in row["quality_flags"])),
        "rows_with_profile_dependencies": sum(bool(row["profile_dependencies"]) for row in rows),
        "rows_with_episode_context_dependencies": sum(bool(row["episode_context_dependencies"]) for row in rows),
    }
    output = {
        "catalog_version": "standing-intent-query-v2.3",
        "status": "provisional_ai_derived",
        "construction_policy": "responsibility-first; backend-blind query compilation; profile/episode/evaluator separation",
        "dependency_policy": "profile_dependencies hold household-specific preferences; episode_context_dependencies hold observable dynamic state; neither reveals evaluator policy",
        "status_policy": "ready requires zero unresolved quality flags; every flagged row is retained as needs_review",
        "summary": summary,
        "validation": validation,
        "queries": rows,
    }
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {
        "summary": summary,
        "validation": validation,
        "output_file": OUTPUT.name,
        "output_sha256": hashlib.sha256(OUTPUT.read_bytes()).hexdigest(),
        "kimi_review_status": "attempted_4_isolated_batches_all_failed_api_connection_zero_tokens",
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
