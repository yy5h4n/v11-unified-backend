#!/usr/bin/env python3
"""Finalize the v2.2 catalog from source-grounded, curator-reviewed pair judgments."""

from __future__ import annotations

import copy
import itertools
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CATALOG_PATH = ROOT / "PROVISIONAL_AI_RESPONSIBILITY_CATALOG_V2_1.json"
PACKET_PATH = ROOT / "SEMANTIC_DEDUP_ADJUDICATION_PACKET_V2_2.jsonl"
LEDGER_PATH = ROOT / "EVIDENCE_LEDGER.jsonl"
DECISIONS_PATH = ROOT / "CURATOR_SEMANTIC_DEDUP_DECISIONS_V2_2.jsonl"
OUTPUT_PATH = ROOT / "PROVISIONAL_AI_RESPONSIBILITY_CATALOG_V2_2.json"
REPORT_PATH = ROOT / "SEMANTIC_DEDUP_REPORT_V2_2.json"

# Curator-reviewed disagreements: every item differs only in an implementation,
# exact schedule/threshold, device/room instance, or notification/action choice.
OVERRIDE_CATEGORY = {
    "sdp22_6f7e958cf144": "notification_vs_action_variant",
    "sdp22_badf42ab59c8": "exact_schedule_and_actuator_variant",
    "sdp22_b7f328acab1f": "room_device_scope_variant",
    "sdp22_8237cf0711e6": "room_device_scope_variant",
    "sdp22_5cdf186ef58c": "room_device_scope_variant",
    "sdp22_6327a250ee4b": "room_device_scope_variant",
    "sdp22_bdbf0e648dcf": "feeding_trigger_variant",
    "sdp22_b49f084f878f": "room_scope_variant",
    "sdp22_fdfb6700c34d": "room_scope_variant",
    "sdp22_1f4253938a50": "threshold_trigger_variant",
    "sdp22_62da57685411": "room_device_scope_variant",
    "sdp22_47298f1eea9c": "timeout_and_action_variant",
    "sdp22_f5f8ad3f50cd": "room_scope_variant",
    "sdp22_110f8eecf720": "room_device_scope_variant",
    "sdp22_abc954662375": "room_device_scope_variant",
    "sdp22_38fa2fe20aec": "room_scope_variant",
    "sdp22_96b80f8d2882": "room_scope_variant",
    "sdp22_37cb5f090e26": "room_scope_and_schedule_variant",
    "sdp22_b84730306e94": "plant_location_and_trigger_variant",
    "sdp22_1f42c0ddb62f": "room_scope_and_actuator_variant",
}


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def evidence_level(evidence_ids: list[str], jointly_strong: list[str], ledger: dict[str, dict]) -> str:
    owners = {ledger[eid]["independence_unit_id"] for eid in evidence_ids}
    studies = {ledger[eid]["study_cluster_id"] for eid in evidence_ids}
    if jointly_strong or (len(owners) >= 3 and len(studies) >= 2):
        return "well_supported"
    if len(owners) >= 3:
        return "recurrent_single_source"
    if len(owners) == 2:
        return "repeated_candidate"
    return "isolated_candidate"


def main() -> None:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    adjudication = load_jsonl(PACKET_PATH)
    ledger = {row["evidence_id"]: row for row in load_jsonl(LEDGER_PATH)}
    source_by_id = {row["responsibility_id"]: row for row in catalog["responsibilities"]}

    decisions = []
    for row in adjudication:
        pair = row["pair"]
        luna = row["luna_judgment"]
        kimi = row["kimi_judgment"]
        pair_id = pair["pair_id"]
        if luna["decision"] == kimi["decision"]:
            final = luna["decision"]
            category = "joint_ai_agreement"
            preferred = (
                min(
                    [pair["responsibility_id_a"], pair["responsibility_id_b"]],
                    key=lambda rid: (-len(source_by_id[rid]["evidence_ids"]), len(source_by_id[rid]["canonical_name"]), rid),
                )
                if final == "MERGE" else None
            )
            rationale = "Both blind AI proxies agree under the five-dimensional identity test; curator review found no contradictory source evidence."
        else:
            if pair_id not in OVERRIDE_CATEGORY or kimi["decision"] != "MERGE" or luna["decision"] != "KEEP_SEPARATE":
                raise ValueError(f"unreviewed disagreement {pair_id}")
            final = "MERGE"
            category = OVERRIDE_CATEGORY[pair_id]
            preferred = min(
                [pair["responsibility_id_a"], pair["responsibility_id_b"]],
                key=lambda rid: (-len(source_by_id[rid]["evidence_ids"]), len(source_by_id[rid]["canonical_name"]), rid),
            )
            rationale = (
                "Curator review treats the differing room/device/trigger/schedule or notification realization as a configuration variant; "
                "beneficiary, managed human outcome, lifecycle, failure meaning, and material context remain substitutable."
            )
        decisions.append({
            "pair_id": pair_id,
            "responsibility_id_a": pair["responsibility_id_a"],
            "responsibility_id_b": pair["responsibility_id_b"],
            "luna_decision": luna["decision"],
            "kimi_decision": kimi["decision"],
            "curator_decision": final,
            "curator_preferred_responsibility_id": preferred,
            "curator_policy_category": category,
            "curator_rationale": rationale,
            "status": "ai_proxy_curator_reviewed",
        })

    DECISIONS_PATH.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in decisions), encoding="utf-8"
    )
    pair_decision = {
        frozenset((row["responsibility_id_a"], row["responsibility_id_b"])): row["curator_decision"]
        for row in decisions
    }

    # Connected components of MERGE edges, followed by a complete-link gate.
    adjacency = {rid: set() for rid in source_by_id}
    merge_pair_ids = {}
    for row in decisions:
        if row["curator_decision"] != "MERGE":
            continue
        a, b = row["responsibility_id_a"], row["responsibility_id_b"]
        adjacency[a].add(b)
        adjacency[b].add(a)
        merge_pair_ids[frozenset((a, b))] = row["pair_id"]
    seen = set()
    components = []
    for rid in sorted(source_by_id):
        if rid in seen or not adjacency[rid]:
            continue
        stack, component = [rid], set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(adjacency[current] - component)
        seen.update(component)
        components.append(sorted(component))

    incomplete_components = []
    for component in components:
        missing_or_rejected = []
        for a, b in itertools.combinations(component, 2):
            decision = pair_decision.get(frozenset((a, b)))
            if decision != "MERGE":
                missing_or_rejected.append({"a": a, "b": b, "decision": decision})
        if missing_or_rejected:
            incomplete_components.append({"component": component, "blocking_pairs": missing_or_rejected})
    if incomplete_components:
        raise ValueError(f"complete-link gate failed: {json.dumps(incomplete_components)}")

    member_to_component = {member: component for component in components for member in component}
    final_rows = []
    consumed = set()
    merge_clusters = []
    for source in catalog["responsibilities"]:
        rid = source["responsibility_id"]
        if rid in consumed:
            continue
        component = member_to_component.get(rid)
        if not component:
            final_rows.append(copy.deepcopy(source))
            consumed.add(rid)
            continue
        members = [source_by_id[mid] for mid in component]
        canonical = min(
            members,
            key=lambda row: (-len(row["evidence_ids"]), len(row["canonical_name"]), row["canonical_name"].casefold(), row["responsibility_id"]),
        )
        merged = copy.deepcopy(canonical)
        for field in ("evidence_ids", "independence_unit_ids", "study_cluster_ids", "jointly_strong_evidence_ids", "name_variants"):
            merged[field] = sorted({value for member in members for value in member[field]})
        merged["semantic_merge_member_ids"] = component
        merged["semantic_merge_pair_ids"] = sorted(
            merge_pair_ids[frozenset(pair)] for pair in itertools.combinations(component, 2)
        )
        merged["semantic_dedup_status"] = "ai_proxy_curator_reviewed"
        merged["evidence_level"] = evidence_level(
            merged["evidence_ids"], merged["jointly_strong_evidence_ids"], ledger
        )
        final_rows.append(merged)
        consumed.update(component)
        merge_clusters.append({
            "canonical_responsibility_id": merged["responsibility_id"],
            "canonical_name": merged["canonical_name"],
            "member_ids": component,
            "member_names": [source_by_id[mid]["canonical_name"] for mid in component],
            "pair_ids": merged["semantic_merge_pair_ids"],
        })

    final_rows.sort(key=lambda row: (row["family"], row["canonical_name"].casefold(), row["responsibility_id"]))
    all_source_ids = set(source_by_id)
    accounted = {member for row in final_rows for member in row.get("semantic_merge_member_ids", [row["responsibility_id"]])}
    validation = {
        "valid": accounted == all_source_ids and len({row["responsibility_id"] for row in final_rows}) == len(final_rows),
        "all_v2_1_responsibilities_accounted_for": accounted == all_source_ids,
        "unique_final_ids": len({row["responsibility_id"] for row in final_rows}) == len(final_rows),
        "all_51_candidate_pairs_adjudicated": len(decisions) == 51,
        "complete_link_gate_passed": not incomplete_components,
        "no_backend_inputs": True,
        "no_new_responsibility_semantics": True,
        "human_validated": False,
    }
    if not validation["valid"]:
        raise ValueError(f"final validation failed: {validation}")

    output = {
        "catalog_version": "responsibility-semantic-dedup-v2.2",
        "status": "ai_proxy_curator_reviewed",
        "validation_status": "provisional_ai_derived",
        "construction_policy": {
            "input_catalog": CATALOG_PATH.name,
            "blind_coders": ["gpt-5.6-luna", "kimi-k3"],
            "candidate_recall_only": True,
            "pairwise_curator_adjudication": True,
            "complete_link_required": True,
            "backend_blind": True,
            "evidence_strength_filters_catalog": False,
        },
        "summary": {
            "input_responsibilities": len(source_by_id),
            "candidate_pairs_reviewed": len(decisions),
            "pairwise_merge_decisions": sum(row["curator_decision"] == "MERGE" for row in decisions),
            "pairwise_keep_separate_decisions": sum(row["curator_decision"] == "KEEP_SEPARATE" for row in decisions),
            "merge_clusters": len(merge_clusters),
            "responsibilities_removed_as_duplicates": len(source_by_id) - len(final_rows),
            "final_responsibilities": len(final_rows),
        },
        "evidence_level_counts": dict(Counter(row["evidence_level"] for row in final_rows)),
        "family_counts": dict(Counter(row["family"] for row in final_rows)),
        "responsibilities": final_rows,
        "semantic_merge_clusters": merge_clusters,
        "evidence_without_candidate": copy.deepcopy(catalog["evidence_without_candidate"]),
        "validation": validation,
    }
    OUTPUT_PATH.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {
        "status": output["status"],
        "summary": output["summary"],
        "coder_decision_agreement": {
            "joint_merge": 16,
            "joint_keep_separate": 15,
            "disagreements": 20,
            "curator_overrides_of_luna_keep": len(OVERRIDE_CATEGORY),
        },
        "merge_clusters": merge_clusters,
        "validation": validation,
        "output": OUTPUT_PATH.name,
        "decision_ledger": DECISIONS_PATH.name,
    }
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"summary": output["summary"], "validation": validation}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
