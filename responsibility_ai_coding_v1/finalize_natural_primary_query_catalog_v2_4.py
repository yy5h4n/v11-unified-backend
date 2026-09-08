#!/usr/bin/env python3
"""Assemble and validate one natural primary query per V2.3 standing intent."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "STANDING_INTENT_QUERY_CATALOG_V2_3.json"
PACKETS = [ROOT / f"NATURAL_PRIMARY_QUERY_PACKET_V2_4_{i:02d}.jsonl" for i in range(4)]
DRAFTS = [ROOT / f"NATURAL_PRIMARY_QUERY_DRAFT_V2_4_{i:02d}.jsonl" for i in range(4)]
OUTPUT = ROOT / "NATURAL_STANDING_INTENT_QUERY_CATALOG_V2_4.json"
REPORT = ROOT / "NATURAL_STANDING_INTENT_QUERY_REPORT_V2_4.json"

FORBIDDEN = [
    "please help me", "i want you to", "given this context", "for the situation described",
    "my request is that", "my household preference is that", "would you be willing to",
    "what i would like you to do is", "i ask you to", "help me ensure",
    "make sure you ensure", "help me support",
]
RECIPE = re.compile(r"\b(?:then|turn on|turn off|switch on|switch off|activate|deactivate|call tool|api\.)\b", re.I)
HIDDEN = re.compile(r"\b(?:episode_id|trajectory|oracle|reward|scoring threshold)\b", re.I)

CURATOR_QUERY_OVERRIDES = {
    "rd_6756752dd66e": "Make sure the baby can settle back to sleep after crying at night.",
    "rd_824cd77ac7e9": "Don't leave music playing when the house is empty.",
    "rd_f61b22e412c4": "Make sure the toilet is left flushed after use.",
    "rd_5d75d19a4827": "Keep my shower-time music available whenever I want it.",
    "rd_a948a16db4ab": "Let me know when the bin needs emptying.",
    "rd_b29cce0b1017": "Keep the room softly lit for comfortable music listening.",
    "rd_d85f7d5c68b5": "Keep the lighting working when a bulb fails.",
    "rd_cf2389fcc9f1": "Keep the entrance well lit when I arrive home in the evening.",
    "rd_1783c33e2747": "Don't leave a lit stove unattended.",
    "rd_bd6dfd6a2c77": "Don't let the pellet supply run out.",
    "rd_split_016151c7c038": "Keep the kitchen warm in the evening.",
}

NATURAL_ACTION_RECIPE = re.compile(
    r"(?:\bif\b.*\bsettle\b|^stop the music\b|^play music\b|^empty the bin\b|"
    r"\bhave the toilet flushed\b|^dim the room lighting\b|^replace failed bulbs\b|"
    r"^light the entrance\b|^shut off a lit stove\b|^warm up the kitchen\b)",
    re.I,
)


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def tokens(text: str) -> list[str]:
    text = unicodedata.normalize("NFKC", text).casefold()
    text = "".join(char if char.isalnum() or char.isspace() else " " for char in text)
    return text.split()


def main() -> None:
    source_catalog = json.loads(SOURCE.read_text(encoding="utf-8"))
    source_rows = source_catalog["queries"]
    packets = [row for path in PACKETS for row in load_jsonl(path)]
    drafts = [row for path in DRAFTS for row in load_jsonl(path)]
    if len(source_rows) != len(packets) or len(packets) != len(drafts) or len(drafts) != 142:
        raise ValueError("expected 142 source, packet, and draft rows")
    source = {row["standing_intent_id"]: row for row in source_rows}
    packet_ids = [row["standing_intent_id"] for row in packets]
    draft_ids = [row["standing_intent_id"] for row in drafts]
    if packet_ids != draft_ids or set(packet_ids) != set(source):
        raise ValueError("coverage/order mismatch")

    rows = []
    inherited_flag_corrections = 0
    inherited_status_corrections = 0
    for packet, draft in zip(packets, drafts):
        sid = packet["standing_intent_id"]
        base = source[sid]
        if draft["responsibility_id"] != packet["responsibility_id"]:
            raise ValueError(f"responsibility mismatch {sid}")
        if draft.get("quality_flags") != base["quality_flags"]:
            inherited_flag_corrections += 1
        if draft.get("generation_status") != base["generation_status"]:
            inherited_status_corrections += 1
        generated_query = draft["natural_query"].strip()
        query = CURATOR_QUERY_OVERRIDES.get(packet["responsibility_id"], generated_query)
        rows.append({
            **{key: value for key, value in base.items() if key not in {"canonical_query", "cross_review", "generation_rationale"}},
            "v2_3_canonical_query": base["canonical_query"],
            "natural_query": query,
            "quality_flags": base["quality_flags"],
            "generation_status": base["generation_status"],
            "surface_generation": {
                "generator": "luna_microbatch",
                "semantic_equivalence_rationale": draft["semantic_equivalence_rationale"],
                "inherited_flags_forced_from_v2_3": draft.get("quality_flags") != base["quality_flags"],
                "inherited_status_forced_from_v2_3": draft.get("generation_status") != base["generation_status"],
                "generated_query": generated_query,
                "curator_override_applied": query != generated_query,
            },
        })

    queries = [row["natural_query"] for row in rows]
    forbidden_hits = {
        phrase: [row["responsibility_id"] for row in rows if phrase in row["natural_query"].casefold()]
        for phrase in FORBIDDEN
    }
    forbidden_hits = {key: value for key, value in forbidden_hits.items() if value}
    recipe_hits = [
        row["responsibility_id"] for row in rows
        if RECIPE.search(row["natural_query"]) or NATURAL_ACTION_RECIPE.search(row["natural_query"])
    ]
    hidden_hits = [row["responsibility_id"] for row in rows if HIDDEN.search(row["natural_query"])]
    length_hits = [row["responsibility_id"] for row in rows if not 4 <= len(tokens(row["natural_query"])) <= 24]
    punctuation_hits = [row["responsibility_id"] for row in rows if not re.search(r"[.!?]$", row["natural_query"])]
    opening_counts = Counter(tuple(tokens(query)[:3]) for query in queries)
    max_opening, max_opening_count = max(opening_counts.items(), key=lambda item: item[1])
    duplicate_texts = [text for text, count in Counter(query.casefold() for query in queries).items() if count > 1]
    validation = {
        "valid": not (forbidden_hits or recipe_hits or hidden_hits or length_hits or punctuation_hits or duplicate_texts or max_opening_count > 14),
        "coverage_exact": set(draft_ids) == set(source),
        "standing_intent_ids_unique": len(draft_ids) == len(set(draft_ids)),
        "query_texts_unique": not duplicate_texts,
        "forbidden_wrapper_hits": forbidden_hits,
        "action_recipe_hits": recipe_hits,
        "hidden_evaluator_hits": hidden_hits,
        "length_gate_hits": length_hits,
        "punctuation_gate_hits": punctuation_hits,
        "max_opening_trigram": list(max_opening),
        "max_opening_trigram_count": max_opening_count,
        "max_opening_trigram_fraction": round(max_opening_count / 142, 4),
        "ready_status_never_promoted": all(
            row["generation_status"] == source[row["standing_intent_id"]]["generation_status"] for row in rows
        ),
        "backend_inputs_used": False,
        "human_validated": False,
    }
    summary = {
        "standing_intents": 142,
        "natural_primary_queries": 142,
        "ready": sum(row["generation_status"] == "ready" for row in rows),
        "needs_review": sum(row["generation_status"] == "needs_review" for row in rows),
        "inherited_flag_corrections": inherited_flag_corrections,
        "inherited_status_corrections": inherited_status_corrections,
        "rejected_artifact": "four_surface_variants_draft_rejected_as_formulaic_and_ungrammatical",
        "curator_query_overrides": sum(row["surface_generation"]["curator_override_applied"] for row in rows),
    }
    output = {
        "catalog_version": "natural-standing-intent-query-v2.4",
        "status": "provisional_ai_derived",
        "policy": "one natural primary query per standing intent; paraphrases deferred to a separate robustness split",
        "summary": summary,
        "validation": validation,
        "queries": rows,
    }
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {"summary": summary, "validation": validation, "output_file": OUTPUT.name, "output_sha256": hashlib.sha256(OUTPUT.read_bytes()).hexdigest()}
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
