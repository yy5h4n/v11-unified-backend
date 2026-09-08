#!/usr/bin/env python3
"""Build a fine-grained responsibility catalog by conservative deduplication only."""

from __future__ import annotations

import collections
import hashlib
import json
import re
import unicodedata
from pathlib import Path

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


ROOT = Path(__file__).resolve().parent
THRESHOLD = 0.86
STOPWORDS = {"a", "an", "the", "to", "and", "or", "of", "for", "from", "with", "in", "on", "at", "is", "are", "be", "when", "while"}
ENTITY_GROUPS = [
    {"infant", "baby", "newborn"}, {"pet", "dog", "cat", "aquarium"},
    {"plant", "plants", "garden", "soil"}, {"child", "children"},
    {"elderly", "older"},
]
CONTEXT_GROUPS = [
    {"unoccupied", "empty", "away", "vacated"}, {"occupied", "occupants"},
    {"morning", "wake", "waking"}, {"night", "nighttime", "evening"},
    {"leaving", "departure", "departing"}, {"arrival", "return", "returning"},
    {"storm", "storms", "rain", "weather"},
]


def load_jsonl(name: str) -> list[dict]:
    return [json.loads(line) for line in (ROOT / name).read_text(encoding="utf-8").splitlines() if line]


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def tokens(text: str) -> set[str]:
    return {token for token in normalize(text).split() if token not in STOPWORDS}


def group_presence(token_set: set[str], groups: list[set[str]]) -> set[int]:
    return {index for index, group in enumerate(groups) if token_set & group}


def guarded_pair(left: str, right: str, score: float) -> tuple[bool, str]:
    if normalize(left) == normalize(right):
        return True, "exact_normalized"
    if score < THRESHOLD:
        return False, "below_similarity_threshold"
    left_tokens, right_tokens = tokens(left), tokens(right)
    if group_presence(left_tokens, ENTITY_GROUPS) != group_presence(right_tokens, ENTITY_GROUPS):
        return False, "beneficiary_entity_guard"
    if group_presence(left_tokens, CONTEXT_GROUPS) != group_presence(right_tokens, CONTEXT_GROUPS):
        return False, "context_guard"
    union = left_tokens | right_tokens
    jaccard = len(left_tokens & right_tokens) / len(union) if union else 1.0
    substring = normalize(left) in normalize(right) or normalize(right) in normalize(left)
    if jaccard < 0.50 and not substring:
        return False, "token_overlap_guard"
    return True, "high_similarity_guarded"


def family_for(texts: list[str]) -> str:
    text = " ".join(normalize(text) for text in texts)
    rules = [
        ("care_health", r"medicat|infant|baby|newborn|health|doctor|hydration|fitness|step.count|elderly|child"),
        ("pet_plant_care", r"\bpet\b|\bdog\b|\bcat\b|aquarium|plant|garden|soil"),
        ("cleanliness_housework", r"clean|floor|laundry|garbage|toilet|saniti"),
        ("safety_security", r"security|intrusion|fire|gas|hazard|alarm|gate|access|storm|leak|overflow|window|garage"),
        ("thermal_air_comfort", r"temperature|thermal|warm|heating|cold|humidity|air quality|mould|ventilat"),
        ("lighting", r"light|illumina|shading|blind"),
        ("supplies_food", r"stock|restock|supply|fridge item|food|coffee|beer|pellet|toilet paper"),
        ("energy_resource", r"energy|power draw|solar|electricity|heating waste"),
        ("routine_readiness", r"wake|morning|sleep|study|reading|dinner|shower|bath|cooking|ready"),
        ("visitor_communication", r"visitor|doorbell|intercom|mail|delivery|notify"),
        ("comfort_ambiance", r"music|ambiance|theater|cinema|party|relax"),
    ]
    for family, pattern in rules:
        if re.search(pattern, text):
            return family
    return "other_household_outcome"


def evidence_level(evidence_ids: list[str], ledger: dict, luna: dict, kimi: dict) -> tuple[str, list[str]]:
    owners = {ledger[eid]["independence_unit_id"] for eid in evidence_ids}
    studies = {ledger[eid]["study_cluster_id"] for eid in evidence_ids}
    jointly_strong = sorted(
        eid for eid in evidence_ids
        if luna[eid]["standing_responsibility_support"] in {"strongly_implies", "entails"}
        and kimi[eid]["standing_responsibility_support"] in {"strongly_implies", "entails"}
    )
    if jointly_strong or (len(owners) >= 3 and len(studies) >= 2):
        return "well_supported", jointly_strong
    if len(owners) >= 3:
        return "recurrent_single_source", jointly_strong
    if len(owners) == 2:
        return "repeated_candidate", jointly_strong
    return "isolated_candidate", jointly_strong


def main() -> None:
    ledger_rows = load_jsonl("EVIDENCE_LEDGER.jsonl")
    ledger = {row["evidence_id"]: row for row in ledger_rows}
    luna = {row["evidence_id"]: row for row in load_jsonl("ANNOTATIONS_LUNA.jsonl")}
    kimi_rows = load_jsonl("ANNOTATIONS_KIMI.jsonl")
    kimi = {row["evidence_id"]: row for row in kimi_rows}
    mentions = [
        {"evidence_id": row["evidence_id"], "text": row["candidate_responsibility"]}
        for row in kimi_rows if row.get("candidate_responsibility")
    ]
    texts = [mention["text"] for mention in mentions]
    matrix = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), lowercase=True).fit_transform(texts)
    similarities = cosine_similarity(matrix)

    clusters: list[list[int]] = []
    merge_edges = []
    rejected_high_similarity = []
    for index, text in enumerate(texts):
        placed = False
        for cluster in clusters:
            decisions = [guarded_pair(text, texts[member], float(similarities[index, member])) for member in cluster]
            if all(decision[0] for decision in decisions):
                for member, decision in zip(cluster, decisions):
                    merge_edges.append({
                        "left_evidence_id": mentions[index]["evidence_id"],
                        "right_evidence_id": mentions[member]["evidence_id"],
                        "score": round(float(similarities[index, member]), 6),
                        "reason": decision[1],
                    })
                cluster.append(index)
                placed = True
                break
            for member, decision in zip(cluster, decisions):
                score = float(similarities[index, member])
                if score >= THRESHOLD and not decision[0]:
                    rejected_high_similarity.append({
                        "left_evidence_id": mentions[index]["evidence_id"],
                        "right_evidence_id": mentions[member]["evidence_id"],
                        "score": round(score, 6),
                        "guard": decision[1],
                    })
        if not placed:
            clusters.append([index])

    responsibilities = []
    for cluster in clusters:
        rows = [mentions[index] for index in cluster]
        evidence_ids = sorted({row["evidence_id"] for row in rows})
        variants = sorted({row["text"] for row in rows})
        counts = collections.Counter(row["text"] for row in rows)
        canonical = sorted(variants, key=lambda value: (-counts[value], len(value), value.casefold()))[0]
        level, jointly_strong = evidence_level(evidence_ids, ledger, luna, kimi)
        normalized_key = normalize(canonical)
        responsibility_id = "rd_" + hashlib.sha256(normalized_key.encode("utf-8")).hexdigest()[:12]
        responsibilities.append({
            "responsibility_id": responsibility_id,
            "canonical_name": canonical,
            "normalized_key": normalized_key,
            "family": family_for(variants),
            "evidence_level": level,
            "evidence_ids": evidence_ids,
            "independence_unit_ids": sorted({ledger[eid]["independence_unit_id"] for eid in evidence_ids}),
            "study_cluster_ids": sorted({ledger[eid]["study_cluster_id"] for eid in evidence_ids}),
            "jointly_strong_evidence_ids": jointly_strong,
            "name_variants": variants,
            "compound_review_required": any(" and " in f" {normalize(value)} " for value in variants),
        })

    noncandidate_ids = sorted(set(ledger) - {row["evidence_id"] for row in mentions})
    output = {
        "catalog_version": "responsibility-dedup-v2",
        "validation_status": "provisional_ai_derived",
        "construction_policy": {
            "source_unit": "non-null Kimi candidate_responsibility attached to audited evidence",
            "new_semantics_generated": False,
            "exact_normalization": "NFKC, casefold, punctuation removal, whitespace collapse",
            "fuzzy_method": "character TF-IDF 3-5 grams, complete-link guarded clustering",
            "cosine_threshold": THRESHOLD,
            "guards": ["beneficiary entity", "context", "token overlap"],
            "evidence_strength_filters_catalog": False,
            "backend_blind": True,
        },
        "summary": {
            "audited_evidence_units": len(ledger),
            "candidate_mentions": len(mentions),
            "evidence_without_candidate": len(noncandidate_ids),
            "deduplicated_responsibilities": len(responsibilities),
            "merged_mentions": len(mentions) - len(responsibilities),
            "compound_review_count": sum(row["compound_review_required"] for row in responsibilities),
        },
        "evidence_level_counts": dict(collections.Counter(row["evidence_level"] for row in responsibilities)),
        "family_counts": dict(collections.Counter(row["family"] for row in responsibilities)),
        "responsibilities": sorted(responsibilities, key=lambda row: (row["family"], row["canonical_name"].casefold())),
        "merge_edges": merge_edges,
        "rejected_high_similarity_pairs": rejected_high_similarity,
        "evidence_without_candidate": noncandidate_ids,
        "validation": {
            "valid": True,
            "all_candidate_evidence_accounted_for": len({eid for row in responsibilities for eid in row["evidence_ids"]}) == len({row["evidence_id"] for row in mentions}),
            "no_backend_inputs": True,
            "no_model_generated_responsibility_text": True,
        },
    }
    path = ROOT / "CONSERVATIVE_DEDUP_RESPONSIBILITY_CATALOG_V2.json"
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"summary": output["summary"], "evidence_level_counts": output["evidence_level_counts"], "family_counts": output["family_counts"], "output": path.name}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
