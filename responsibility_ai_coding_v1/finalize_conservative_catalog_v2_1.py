#!/usr/bin/env python3
"""Finalize the conservative responsibility catalog under curator decisions.

This script is intentionally bounded to the catalog, evidence ledger, Kimi
annotations, and the preceding LUNA audit in this directory.  It never reads
backend/scenario/Episode inputs and never performs fuzzy merging.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path


HERE = Path(__file__).resolve().parent
CATALOG_PATH = HERE / "CONSERVATIVE_DEDUP_RESPONSIBILITY_CATALOG_V2.json"
LEDGER_PATH = HERE / "EVIDENCE_LEDGER.jsonl"
ANNOTATIONS_PATH = HERE / "ANNOTATIONS_KIMI.jsonl"
LUNA_PATH = HERE / "LUNA_CONSERVATIVE_AUDIT_V2.json"
CURATOR_PATH = HERE / "CURATOR_CONSERVATIVE_AUDIT_V2_1.json"
OUTPUT_PATH = HERE / "PROVISIONAL_AI_RESPONSIBILITY_CATALOG_V2_1.json"

# Main-review decision supplied by the curator.  The other 18 compound
# entries are split according to LUNA's table.
KEEP_ATOMIC_IDS = {
    "rd_bf52ea0cf49d",
    "rd_491429d8fab5",
    "rd_678537aa5e56",
    "rd_efea5fb4f93f",
    "rd_48cb6e53506a",
    "rd_6c95ef8c36b1",
    "rd_bc53f8b79068",
    "rd_98be58ef05f9",
    "rd_8b0f533b3eaa",
    "rd_89fd37b1d819",
    "rd_8b7dca2e69c7",
    "rd_68c81fbf8bad",
    "rd_952c27c5b039",
    "rd_ebd968ad40ce",
}
SPECIAL_SPLIT = {
    "rd_4ae8c9a53040": [
        "support cognitive training",
        "support eye-drop adherence",
        "support resident hydration",
        "maintain safe illumination",
    ]
}


def load_json(path: Path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def load_jsonl(path: Path):
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def normalize(text: str) -> str:
    """Catalog normalization: NFKC, casefold, punctuation removal, spaces."""
    text = unicodedata.normalize("NFKC", text).casefold()
    text = "".join(ch for ch in text if not unicodedata.category(ch).startswith("P"))
    return " ".join(text.split())


def infer_child_family(name: str, fallback: str) -> str:
    """Recompute a split child's family from its faithful objective text."""
    n = normalize(name)
    if any(k in n for k in ("security", "secure", "intrusion", "garage is closed", "water system", "emergency call", "gate access")):
        return "safety_security"
    if any(k in n for k in ("energy", "save energy", "energy waste")):
        return "energy_resource"
    if any(k in n for k in ("clean", "cleaning", "laundry", "garbage", "floor")):
        return "cleanliness_housework"
    if any(k in n for k in ("dog", "pet", "garden", "pool", "irrigation", "plant")):
        return "pet_plant_care"
    if any(k in n for k in ("light", "lighting", "illumination", "shutter", "shade", "curtain")):
        return "lighting"
    if any(k in n for k in ("temperature", "warm", "heating", "humidity", "air quality", "shower comfort")):
        return "thermal_air_comfort"
    if any(k in n for k in ("delivery", "notify", "news", "occupancy", "signal")):
        return "visitor_communication"
    if any(k in n for k in ("medication", "cognitive", "hydration", "treatment", "soothe", "baby", "stair", "health", "comfort")):
        return "care_health"
    return fallback


def child_id(parent_id: str, text: str) -> str:
    digest = hashlib.sha1((parent_id + "|" + normalize(text)).encode("utf-8")).hexdigest()[:12]
    return "rd_split_" + digest


def evidence_level(evidence_ids, strong_ids, ledger_by_id):
    strong = set(evidence_ids) & set(strong_ids)
    if strong:
        return "well_supported"
    if len(evidence_ids) >= 3 and len({ledger_by_id[e]["study_cluster_id"] for e in evidence_ids}) == 1:
        return "recurrent_single_source"
    if len(evidence_ids) > 1:
        return "repeated_candidate"
    return "isolated_candidate"


def main() -> None:
    catalog = load_json(CATALOG_PATH)
    ledger = load_jsonl(LEDGER_PATH)
    annotations = load_jsonl(ANNOTATIONS_PATH)
    luna = load_json(LUNA_PATH)
    ledger_by_id = {row["evidence_id"]: row for row in ledger}
    annotation_by_id = {row["evidence_id"]: row for row in annotations}
    luna_compounds = {row["responsibility_id"]: row for row in luna["compound_audits"]}

    # Curator audit explicitly records both the LUNA recommendation and the
    # final decision, including every unchanged compound entry.
    compound_audits = []
    final_split_by_parent = {}
    for source in catalog["responsibilities"]:
        if not source.get("compound_review_required"):
            continue
        rid = source["responsibility_id"]
        luna_row = luna_compounds[rid]
        if rid in KEEP_ATOMIC_IDS:
            final_decision = "KEEP_ATOMIC"
            final_split = []
        else:
            final_decision = "SPLIT_INTO"
            final_split = SPECIAL_SPLIT.get(rid, list(luna_row["split_into"]))
            final_split_by_parent[rid] = final_split
        luna_split = list(luna_row["split_into"])
        compound_audits.append({
            "responsibility_id": rid,
            "evidence_ids": list(source["evidence_ids"]),
            "luna_suggestion": luna_row["decision"],
            "luna_split_into": luna_split,
            "final_decision": final_decision,
            "final_split_into": final_split,
            "decision_differs": luna_row["decision"] != final_decision or luna_split != final_split,
        })

    merge_audits = []
    for row in luna["merge_edge_audits"]:
        merge_audits.append({
            "left_evidence_id": row["left_evidence_id"],
            "right_evidence_id": row["right_evidence_id"],
            "luna_suggestion": row["decision"],
            "final_decision": "KEEP_MERGED",
            "decision_differs": row["decision"] != "KEEP_MERGED",
        })

    split_parents = set(final_split_by_parent)
    raw_entries = []
    for source in catalog["responsibilities"]:
        rid = source["responsibility_id"]
        if rid not in split_parents:
            entry = copy.deepcopy(source)
            entry["compound_review_required"] = False
            entry["compound_review_status"] = (
                "keep_atomic" if source.get("compound_review_required") else "not_flagged"
            )
            raw_entries.append(entry)
            continue
        for text in final_split_by_parent[rid]:
            entry = {
                "responsibility_id": child_id(rid, text),
                "canonical_name": text,
                "normalized_key": normalize(text),
                "family": infer_child_family(text, source["family"]),
                "evidence_level": source["evidence_level"],
                "evidence_ids": list(source["evidence_ids"]),
                "independence_unit_ids": list(source["independence_unit_ids"]),
                "study_cluster_ids": list(source["study_cluster_ids"]),
                "jointly_strong_evidence_ids": list(source["jointly_strong_evidence_ids"]),
                "name_variants": [text],
                "compound_review_required": False,
                "compound_review_status": "split_child",
                "parent_responsibility_id": rid,
            }
            raw_entries.append(entry)

    # Exact-only dedup after splitting.  The first entry owns the stable ID;
    # provenance from any collapsed split child is retained.
    by_key = {}
    final_entries = []
    for entry in raw_entries:
        key = normalize(entry["canonical_name"])
        if key not in by_key:
            entry["normalized_key"] = key
            by_key[key] = entry
            final_entries.append(entry)
            continue
        existing = by_key[key]
        for field in ("evidence_ids", "independence_unit_ids", "study_cluster_ids", "jointly_strong_evidence_ids", "name_variants"):
            existing[field] = list(dict.fromkeys(existing[field] + entry[field]))
        parents = []
        for candidate in (existing.get("parent_responsibility_id"), entry.get("parent_responsibility_id")):
            if candidate and candidate not in parents:
                parents.append(candidate)
        if parents:
            existing["parent_responsibility_id"] = parents[0] if len(parents) == 1 else parents

    # Recompute evidence level for every final key from its covered evidence.
    for entry in final_entries:
        entry["evidence_level"] = evidence_level(
            entry["evidence_ids"],
            entry["jointly_strong_evidence_ids"],
            ledger_by_id,
        )
        entry["family"] = entry["family"] if "parent_responsibility_id" not in entry else infer_child_family(entry["canonical_name"], entry["family"])

    original_candidate_ids = {
        row["evidence_id"] for row in annotations if row.get("candidate_responsibility") is not None
    }
    final_covered_ids = {eid for entry in final_entries for eid in entry["evidence_ids"]}
    split_children_count = sum(len(v) for v in final_split_by_parent.values())
    merged_cluster_count = sum(1 for entry in catalog["responsibilities"] if len(entry["evidence_ids"]) > 1)
    family_counts = dict(sorted(Counter(entry["family"] for entry in final_entries).items()))
    evidence_counts = dict(sorted(Counter(entry["evidence_level"] for entry in final_entries).items()))
    orphan_split_parents = sorted(split_parents - {
        parent
        for entry in final_entries
        for parent in (
            [entry.get("parent_responsibility_id")]
            if isinstance(entry.get("parent_responsibility_id"), str)
            else (entry.get("parent_responsibility_id") or [])
        )
    })
    all_candidate_evidence_covered = original_candidate_ids == final_covered_ids
    all_compounds_decided = len(compound_audits) == 32
    ids_are_unique = len({entry["responsibility_id"] for entry in final_entries}) == len(final_entries)
    validation_is_valid = (
        all_candidate_evidence_covered
        and not orphan_split_parents
        and all_compounds_decided
        and ids_are_unique
    )

    curator = {
        "audit_version": "conservative-audit-v2.1",
        "status": "ai_proxy_curator_reviewed",
        "validation_status": "provisional_ai_derived",
        "merge_edge_audits": merge_audits,
        "compound_audits": compound_audits,
        "summary": {
            "merge_edges": len(merge_audits),
            "compound_entries": len(compound_audits),
            "decision_differences": sum(row["decision_differs"] for row in compound_audits + merge_audits),
        },
    }
    CURATOR_PATH.write_text(json.dumps(curator, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    final_catalog = {
        "catalog_version": "responsibility-dedup-v2.1",
        "status": "ai_proxy_curator_reviewed",
        "validation_status": "provisional_ai_derived",
        "construction_policy": {
            "source_unit": "non-null Kimi candidate_responsibility attached to audited evidence",
            "new_semantics_generated": False,
            "split_source": "LUNA_CONSERVATIVE_AUDIT_V2.json plus explicit curator override",
            "normalization": "NFKC, casefold, punctuation removal, whitespace collapse",
            "post_split_dedup": "exact normalized key only",
            "fuzzy_merge": False,
            "backend_inputs": False,
        },
        "summary": {
            "evidence_records": len(ledger),
            "original_candidate_mentions": len(original_candidate_ids),
            "split_parents": len(split_parents),
            "split_children_generated": split_children_count,
            "existing_merged_clusters_retained": merged_cluster_count,
            "exact_post_split_collapses": len(raw_entries) - len(final_entries),
            "final_responsibility_count": len(final_entries),
        },
        "evidence_level_counts": evidence_counts,
        "family_counts": family_counts,
        "responsibilities": final_entries,
        "merge_edges": copy.deepcopy(catalog["merge_edges"]),
        "rejected_high_similarity_pairs": copy.deepcopy(catalog["rejected_high_similarity_pairs"]),
        "evidence_without_candidate": copy.deepcopy(catalog["evidence_without_candidate"]),
        "validation": {
            "valid": validation_is_valid,
            "all_original_candidate_evidence_covered": all_candidate_evidence_covered,
            "orphan_split_parents": orphan_split_parents,
            "all_32_compounds_have_decisions": all_compounds_decided,
            "unique_responsibility_ids": ids_are_unique,
            "no_backend_inputs": True,
            "no_fuzzy_merge": True,
        },
    }
    OUTPUT_PATH.write_text(json.dumps(final_catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
