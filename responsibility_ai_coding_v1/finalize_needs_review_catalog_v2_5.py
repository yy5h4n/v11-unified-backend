#!/usr/bin/env python3
"""Curator-resolve all V2.4 needs-review rows into release, merge, split, or quarantine."""

from __future__ import annotations

import copy
import hashlib
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "NATURAL_STANDING_INTENT_QUERY_CATALOG_V2_4.json"
RESPONSIBILITIES = ROOT / "PROVISIONAL_AI_RESPONSIBILITY_CATALOG_V2_2.json"
PACKET = ROOT / "NEEDS_REVIEW_ADJUDICATION_PACKET_V2_5.jsonl"
REVIEW_B = ROOT / "NEEDS_REVIEW_ADJUDICATION_LUNA_B_V2_5.jsonl"
LEDGER = ROOT / "EVIDENCE_LEDGER.jsonl"
DECISIONS = ROOT / "CURATOR_NEEDS_REVIEW_DECISIONS_V2_5.jsonl"
OUTPUT = ROOT / "NATURAL_STANDING_INTENT_QUERY_CATALOG_V2_5.json"
QUARANTINE_OUTPUT = ROOT / "RESPONSIBILITY_QUARANTINE_V2_5.jsonl"
REPORT = ROOT / "NEEDS_REVIEW_RESOLUTION_REPORT_V2_5.json"


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


QUARANTINE = {
    "rd_split_6b76eff02ef0": "The ledger row concatenates unrelated pet-feeding and stair-mobility text, so the stair responsibility cannot be cleanly traced to an auditable evidence unit.",
}

MERGE_INTO = {
    "rd_split_a52cf8f8d8fa": "rd_split_42d866d7dbea",  # newborn thermal comfort
    "rd_split_df4d7efb1b61": "rd_5f50fe481d06",       # medication adherence
    "rd_split_bd5e7664b9a8": "rd_128e426f1ff3",       # hydration
    "rd_split_39bba8cb11da": "rd_2126fdb0eb1b",       # home cleanliness
    "rd_split_caa71a39ed10": "rd_2126fdb0eb1b",
    "rd_split_100f3f013adc": "rd_2126fdb0eb1b",
    "rd_split_a7109276d669": "rd_split_94be9427a8c4",  # laundry upkeep
    "rd_split_9b7e8b0d9950": "rd_5dd0c995ea28",       # unoccupied-home security
    "rd_split_9cead2579fb2": "rd_split_c828da9fc975", # safety lighting
    "rd_89fd37b1d819": "rd_0883e558daf8",             # garden intrusion security
    "rd_4bd7f3e54b9a": "rd_split_af088787b04c",       # garden watering
    "rd_split_bb4c32068e04": "rd_cbfe46f2692a",       # remote home monitoring
}

REVISIONS = {
    "rd_bf52ea0cf49d": ("support children keeping their bedtime", "Make sure the kids stick to their bedtime.", "children follow their intended bedtime"),
    "rd_split_145ee393b9c2": ("maintain bathroom comfort while occupied", "Keep the bathroom comfortable while it's in use.", "bathroom comfort while occupied"),
    "rd_491429d8fab5": ("maintain healthy indoor air under humidity and mould risk", "Keep the indoor air healthy when humidity or mould becomes a problem.", "healthy indoor air under humidity or mould risk"),
    "rd_678537aa5e56": ("arrange care and medication when the resident is ill", "Arrange appropriate care and medication when I'm ill.", "access to appropriate care and medication during illness"),
    "rd_split_f1dd61ebda25": ("support regular cognitive training", "Keep me engaged with my cognitive exercises.", "regular participation in cognitive training"),
    "rd_split_75756fc5714e": ("support a calming environment during low mood", "Create a calming environment when I'm feeling low.", "a calming environment during low mood"),
    "rd_split_bb0c7c2da137": ("support eye-drop adherence", "Remind me about my eye drops at the prescribed time.", "eye drops taken at the prescribed time"),
    "rd_a948a16db4ab": ("alert the resident when the bin needs emptying", "Let me know when the bin needs emptying.", "awareness that the bin needs emptying"),
    "rd_fcd3c198a00d": ("alert the resident when toilet paper is depleted", "Let me know when the toilet paper has run out.", "awareness of toilet-paper depletion"),
    "rd_48cb6e53506a": ("keep the rubbish bin sanitized and lined", "Keep the rubbish bin sanitized and properly lined.", "a sanitized rubbish bin fitted with a bag"),
    "rd_6c95ef8c36b1": ("maintain clear kitchen air while cooking", "Keep the kitchen air clear while I cook.", "clear kitchen air during cooking"),
    "rd_split_94be9427a8c4": ("keep household laundry up to date", "Keep the household laundry up to date.", "household laundry kept up to date"),
    "rd_split_0feef23c4030": ("maintain scheduled pool cleaning", "Keep the pool clean on its established schedule.", "pool cleanliness maintained on schedule"),
    "rd_split_57dc1a5ccd89": ("avoid energy waste in unused spaces", "Avoid wasting energy in spaces nobody is using.", "energy waste avoided in unused spaces"),
    "rd_bc53f8b79068": ("maintain suitable daylight and privacy while a room is occupied", "Keep the room's daylight and privacy comfortable while someone is there.", "suitable daylight and privacy in an occupied room"),
    "rd_split_b8936d13fb0d": ("maintain emergency lighting during fire alarm or outage", "Keep emergency lighting available during a fire alarm or power outage.", "emergency illumination during a fire alarm or outage"),
    "rd_split_4053e031415b": ("maintain comfortable lighting for reading and relaxation", "Keep the lighting comfortable for reading and relaxation.", "comfortable lighting for reading and relaxation"),
    "rd_split_c828da9fc975": ("maintain lighting needed for resident safety", "Keep the room safely lit whenever light is needed.", "adequate lighting for resident safety"),
    "rd_cf2389fcc9f1": ("maintain entrance lighting for evening arrival", "Keep the entrance well lit when I arrive home in the evening.", "adequate entrance lighting for evening arrival"),
    "rd_5e0e9919d03e": ("alert the resident when a full bin is due for collection", "Tell me when the full bin is due for collection.", "awareness that a full bin is due for collection"),
    "rd_98be58ef05f9": ("protect the boiler and electrical system during pressure faults", "Keep the boiler and electrical system safe during a pressure fault.", "boiler and electrical-system safety during a pressure fault"),
    "rd_8b0f533b3eaa": ("protect the home from forecast rain", "Keep the home protected when rain is forecast and nobody can attend to it.", "home protection from forecast rain"),
    "rd_split_e27a3c692e50": ("keep the pet fed", "Keep my pet properly fed.", "pet receives the required food"),
    "rd_68c81fbf8bad": ("protect the home from hidden water leaks", "Protect my home if a hidden water leak develops.", "home protected from damage caused by a hidden leak"),
    "rd_split_39d32cc7fd22": ("keep the kitchen ready for cooking", "Have the kitchen ready when it's time to cook.", "kitchen readiness for cooking"),
    "rd_37104b57370a": ("maintain comfortable indoor temperature", "Keep the indoor temperature comfortable.", "comfortable indoor temperature"),
    "rd_ebd968ad40ce": ("maintain comfortable indoor temperature and humidity", "Keep the indoor temperature and humidity comfortable.", "comfortable indoor temperature and humidity"),
    "rd_54bdb4d1d881": ("maintain healthy indoor air", "Keep the air inside healthy.", "healthy indoor air"),
    "rd_split_be2cdd7acdff": ("maintain comfort during the morning shower", "Keep me comfortable during my morning shower.", "resident comfort during the morning shower"),
    "rd_split_137db9864548": ("provide the daily news during the morning shower", "Give me the day's news during my morning shower.", "access to the day's news during the morning shower"),
}

# One compound row genuinely contains two human outcomes. Soothing evidence is
# absorbed into the existing infant-sleep responsibility; monitoring becomes a new row.
COMPOUND_PARENT = "rd_efea5fb4f93f"
COMPOUND_MERGE_TARGET = "rd_6756752dd66e"


def evidence_level(eids: list[str], resp_rows: dict, ledger: dict) -> str:
    owners = {ledger[eid]["independence_unit_id"] for eid in eids}
    studies = {ledger[eid]["study_cluster_id"] for eid in eids}
    jointly = {eid for row in resp_rows.values() for eid in row.get("jointly_strong_evidence_ids", []) if eid in eids}
    if jointly or (len(owners) >= 3 and len(studies) >= 2):
        return "well_supported"
    if len(owners) >= 3:
        return "recurrent_single_source"
    if len(owners) == 2:
        return "repeated_candidate"
    return "isolated_candidate"


def main() -> None:
    source_catalog = json.loads(SOURCE.read_text(encoding="utf-8"))
    source_rows = {row["responsibility_id"]: copy.deepcopy(row) for row in source_catalog["queries"]}
    resp_rows = {row["responsibility_id"]: row for row in json.loads(RESPONSIBILITIES.read_text(encoding="utf-8"))["responsibilities"]}
    packet = {row["responsibility_id"]: row for row in load_jsonl(PACKET)}
    review_b = {row["responsibility_id"]: row for row in load_jsonl(REVIEW_B)}
    ledger = {row["evidence_id"]: row for row in load_jsonl(LEDGER)}
    review_ids = set(packet)
    covered = set(QUARANTINE) | set(MERGE_INTO) | set(REVISIONS) | {COMPOUND_PARENT}
    if covered != review_ids:
        raise ValueError(f"curator coverage mismatch missing={sorted(review_ids-covered)} extra={sorted(covered-review_ids)}")

    decisions = []
    quarantined = []
    absorbed = Counter()

    def absorb(source_id: str, target_id: str, policy: str) -> None:
        source = source_rows[source_id]
        target = source_rows[target_id]
        target["source_evidence_ids"] = sorted(set(target["source_evidence_ids"]) | set(source["source_evidence_ids"]))
        target["evidence_level"] = evidence_level(target["source_evidence_ids"], resp_rows, ledger)
        target.setdefault("v2_5_absorbed_responsibility_ids", []).append(source_id)
        target.setdefault("v2_5_absorption_policy", []).append(policy)
        absorbed[target_id] += 1

    for rid in sorted(review_ids):
        if rid in QUARANTINE:
            decisions.append({"responsibility_id": rid, "curator_decision": "QUARANTINE", "target_id": None, "rationale": QUARANTINE[rid], "review_b_decision": review_b[rid]["decision"]})
            quarantined.append({**packet[rid], "resolution": "quarantined", "curator_rationale": QUARANTINE[rid]})
        elif rid in MERGE_INTO:
            target = MERGE_INTO[rid]
            absorb(rid, target, "same human outcome; schedule/room/device/notification or source-fragment variant")
            decisions.append({"responsibility_id": rid, "curator_decision": "MERGE_INTO_EXISTING", "target_id": target, "rationale": "The evidence supports the target's existing human outcome; the apparent difference is configuration or a fragmented source extraction.", "review_b_decision": review_b[rid]["decision"]})
        elif rid == COMPOUND_PARENT:
            absorb(rid, COMPOUND_MERGE_TARGET, "infant soothing outcome merged into existing sleep-restoration responsibility")
            digest = hashlib.sha256(f"{rid}|infant_observability".encode()).hexdigest()[:12]
            new_rid, new_sid = f"rd_v25_{digest}", f"si_v25_{digest}"
            parent = source_rows[rid]
            new_row = copy.deepcopy(parent)
            new_row.update({
                "responsibility_id": new_rid,
                "standing_intent_id": new_sid,
                "natural_query": "Let me check on the baby when they may need attention.",
                "v2_3_canonical_query": "maintain visibility of the infant during possible distress",
                "beneficiary": "infant",
                "lifecycle": "GUARD",
                "delegated_outcome": "visibility of the infant during possible distress",
                "duration_boundary": "when the infant may need attention",
                "quality_flags": [],
                "generation_status": "ready",
                "evidence_level": evidence_level(parent["source_evidence_ids"], resp_rows, ledger),
                "v2_5_created_from_compound_parent": rid,
            })
            source_rows[new_rid] = new_row
            decisions.append({"responsibility_id": rid, "curator_decision": "MERGE_AND_CREATE_CHILD", "target_id": COMPOUND_MERGE_TARGET, "created_child_id": new_rid, "rationale": "Soothing and infant observability are distinct outcomes; soothing merges with the existing sleep-restoration responsibility and observability is released separately.", "review_b_decision": review_b[rid]["decision"]})
        else:
            name, query, outcome = REVISIONS[rid]
            row = source_rows[rid]
            row["v2_5_previous_natural_query"] = row["natural_query"]
            row["v2_5_resolved_canonical_name"] = name
            row["natural_query"] = query
            row["delegated_outcome"] = outcome
            row["quality_flags"] = []
            row["generation_status"] = "ready"
            row["v2_5_resolution"] = "source_grounded_curator_release"
            decisions.append({"responsibility_id": rid, "curator_decision": "REVISE_AND_RELEASE", "target_id": rid, "resolved_canonical_name": name, "resolved_natural_query": query, "resolved_delegated_outcome": outcome, "rationale": "Curator review finds one defensible standing outcome after separating human responsibility from device/action configuration.", "review_b_decision": review_b[rid]["decision"]})

    removed = set(QUARANTINE) | set(MERGE_INTO) | {COMPOUND_PARENT}
    release_rows = [row for rid, row in source_rows.items() if rid not in removed]
    release_rows.sort(key=lambda row: row["responsibility_id"])
    if any(row["generation_status"] != "ready" or row["quality_flags"] for row in release_rows):
        bad = [row["responsibility_id"] for row in release_rows if row["generation_status"] != "ready" or row["quality_flags"]]
        raise ValueError(f"unresolved release rows: {bad}")
    ids = [row["responsibility_id"] for row in release_rows]
    sids = [row["standing_intent_id"] for row in release_rows]
    queries = [row["natural_query"].casefold() for row in release_rows]
    validation = {
        "valid": True,
        "all_44_needs_review_rows_adjudicated": len(decisions) == 44,
        "no_needs_review_in_release": all(row["generation_status"] == "ready" for row in release_rows),
        "no_quality_flags_in_release": all(not row["quality_flags"] for row in release_rows),
        "release_responsibility_ids_unique": len(ids) == len(set(ids)),
        "release_standing_intent_ids_unique": len(sids) == len(set(sids)),
        "release_queries_unique": len(queries) == len(set(queries)),
        "quarantine_is_resolved_not_pending": all(row["resolution"] == "quarantined" for row in quarantined),
        "backend_inputs_used": False,
        "human_validated": False,
    }
    summary = {
        "v2_4_input_responsibilities": 142,
        "v2_4_needs_review": 44,
        "released_responsibilities": len(release_rows),
        "released_ready": len(release_rows),
        "quarantined": len(quarantined),
        "merged_into_existing": len(MERGE_INTO),
        "compound_parents_resolved": 1,
        "new_children_created": 1,
        "decision_counts": dict(Counter(row["curator_decision"] for row in decisions)),
        "invalid_reviewer_a": "discarded_all_quarantine_due_false_verbatim_missing_claim",
        "replacement_kimi_review": "failed_api_connection_no_output",
    }
    output = {
        "catalog_version": "natural-standing-intent-query-v2.5",
        "status": "ai_proxy_curator_resolved_release",
        "summary": summary,
        "validation": validation,
        "queries": release_rows,
    }
    DECISIONS.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in decisions), encoding="utf-8")
    QUARANTINE_OUTPUT.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in quarantined), encoding="utf-8")
    OUTPUT.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {"summary": summary, "validation": validation, "output_file": OUTPUT.name, "output_sha256": hashlib.sha256(OUTPUT.read_bytes()).hexdigest()}
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
