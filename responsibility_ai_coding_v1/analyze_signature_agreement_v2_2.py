import hashlib
import json
import sys
from difflib import SequenceMatcher
from pathlib import Path

BASE = Path(__file__).resolve().parent
PACKET_PATH = BASE / "SIGNATURE_CODING_PACKET_V2_2.jsonl"
LUNA_PATH = BASE / "SIGNATURE_ANNOTATIONS_LUNA_V2_2.jsonl"
KIMI_PATH = BASE / "SIGNATURE_ANNOTATIONS_KIMI_ISOLATED_V2_2.jsonl"
REPORT_PATH = BASE / "SIGNATURE_AGREEMENT_REPORT_V2_2.json"
PAIRS_PATH = BASE / "SEMANTIC_DEDUP_CANDIDATE_PAIRS_V2_2.jsonl"

IDENTITY_FIELDS = ["beneficiary_class", "lifecycle", "outcome_key", "failure_mode", "context_key"]
FOUR_FIELDS = ["beneficiary_class", "lifecycle", "failure_mode", "context_key"]
EXPECTED_N = 167
NAME_SEQ_THRESHOLD = 0.78
OUTCOME_SEQ_THRESHOLD = 0.70
OUTCOME_JACCARD_THRESHOLD = 0.5


def load_jsonl(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def norm(v):
    if v is None:
        return None
    return str(v).strip()


def signature_of(coding):
    coding = coding or {}
    return {f: norm(coding.get(f)) for f in IDENTITY_FIELDS}


def seq_ratio(a, b):
    return SequenceMatcher(None, a or "", b or "").ratio()


def token_jaccard(a, b):
    ta = {t for t in (a or "").split("_") if t}
    tb = {t for t in (b or "").split("_") if t}
    if not ta and not tb:
        return 1.0
    if not ta or not tb:
        return 0.0
    inter = ta & tb
    return len(inter) / len(ta | tb)


def norm_name(s):
    return " ".join((s or "").lower().split())


def main():
    packet_rows = load_jsonl(PACKET_PATH)
    luna_rows = load_jsonl(LUNA_PATH)
    kimi_rows = load_jsonl(KIMI_PATH)

    packet = {r["responsibility_id"]: r for r in packet_rows}
    luna = {r["responsibility_id"]: r for r in luna_rows}
    kimi = {r["responsibility_id"]: r for r in kimi_rows}

    id_sets_equal = set(packet) == set(luna) == set(kimi)
    counts = {"packet": len(packet_rows), "luna": len(luna_rows), "kimi": len(kimi_rows)}
    dup_free = (len(packet) == len(packet_rows) and len(luna) == len(luna_rows)
                and len(kimi) == len(kimi_rows))
    aligned = (counts["packet"] == EXPECTED_N and counts["luna"] == EXPECTED_N
               and counts["kimi"] == EXPECTED_N and id_sets_equal and dup_free)
    if not aligned:
        sys.stderr.write(json.dumps({
            "error": "id_alignment_failed", "counts": counts,
            "expected_n": EXPECTED_N, "id_sets_equal": id_sets_equal,
            "duplicate_free": dup_free}, ensure_ascii=False) + "\n")
        sys.exit(1)

    ids = sorted(packet)

    field_agree = {f: 0 for f in IDENTITY_FIELDS}
    full_agree = 0
    for rid in ids:
        ls = signature_of(luna[rid].get("coding"))
        ks = signature_of(kimi[rid].get("coding"))
        for f in IDENTITY_FIELDS:
            if ls[f] == ks[f]:
                field_agree[f] += 1
        if all(ls[f] == ks[f] for f in IDENTITY_FIELDS):
            full_agree += 1

    n = len(ids)
    field_rates = {f: {"agree": field_agree[f], "disagree": n - field_agree[f],
                       "rate": round(field_agree[f] / n, 6)} for f in IDENTITY_FIELDS}

    records = {}
    for rid in ids:
        p = packet[rid]
        records[rid] = {
            "name": p.get("responsibility_name"),
            "evidence": p.get("evidence", []),
            "luna_sig": signature_of(luna[rid].get("coding")),
            "kimi_sig": signature_of(kimi[rid].get("coding")),
        }

    pair_map = {}
    for i in range(n):
        for j in range(i + 1, n):
            a, b = ids[i], ids[j]
            ra, rb = records[a], records[b]
            triggers = []
            r1_luna = ra["luna_sig"] == rb["luna_sig"]
            r1_kimi = ra["kimi_sig"] == rb["kimi_sig"]
            if r1_luna:
                triggers.append({"rule": "R1_full_signature_exact", "coder": "luna", "score": 1.0})
            if r1_kimi:
                triggers.append({"rule": "R1_full_signature_exact", "coder": "kimi", "score": 1.0})
            for coder, key in (("luna", "luna_sig"), ("kimi", "kimi_sig")):
                sa, sb = ra[key], rb[key]
                if all(sa[f] == sb[f] for f in FOUR_FIELDS):
                    o_seq = seq_ratio(sa["outcome_key"], sb["outcome_key"])
                    o_jac = token_jaccard(sa["outcome_key"], sb["outcome_key"])
                    if o_seq >= OUTCOME_SEQ_THRESHOLD or o_jac >= OUTCOME_JACCARD_THRESHOLD:
                        triggers.append({
                            "rule": "R2_four_fields_exact_outcome_similar",
                            "coder": coder,
                            "outcome_seq_ratio": round(o_seq, 6),
                            "outcome_token_jaccard": round(o_jac, 6),
                            "score": round(max(o_seq, o_jac), 6)})
            name_ratio = seq_ratio(norm_name(ra["name"]), norm_name(rb["name"]))
            if name_ratio >= NAME_SEQ_THRESHOLD:
                bene_luna = ra["luna_sig"]["beneficiary_class"] == rb["luna_sig"]["beneficiary_class"]
                bene_kimi = ra["kimi_sig"]["beneficiary_class"] == rb["kimi_sig"]["beneficiary_class"]
                if bene_luna or bene_kimi:
                    coders = [c for c, ok in (("luna", bene_luna), ("kimi", bene_kimi)) if ok]
                    triggers.append({
                        "rule": "R3_name_similar_beneficiary_match",
                        "beneficiary_match_coders": coders,
                        "name_seq_ratio": round(name_ratio, 6),
                        "score": round(name_ratio, 6)})
            if triggers:
                pair_map[(a, b)] = triggers

    def tier_of(triggers):
        if any(t["rule"] == "R1_full_signature_exact" for t in triggers):
            return 1
        if any(t["rule"] == "R2_four_fields_exact_outcome_similar" for t in triggers):
            return 2
        return 3

    ordered = sorted(pair_map.items(),
                     key=lambda kv: (tier_of(kv[1]), -max(t["score"] for t in kv[1]),
                                     kv[0][0], kv[0][1]))

    seen_pair_ids = set()
    out_pairs = []
    for (a, b), triggers in ordered:
        pair_id = "sdp22_" + hashlib.sha256(f"{a}|{b}".encode("utf-8")).hexdigest()[:12]
        assert pair_id not in seen_pair_ids, f"duplicate pair_id {pair_id}"
        seen_pair_ids.add(pair_id)
        assert a != b, f"self pair {a}"
        out_pairs.append({
            "pair_id": pair_id,
            "responsibility_id_a": a,
            "responsibility_name_a": records[a]["name"],
            "evidence_a": records[a]["evidence"],
            "responsibility_id_b": b,
            "responsibility_name_b": records[b]["name"],
            "evidence_b": records[b]["evidence"],
            "signature_luna": {"a": records[a]["luna_sig"], "b": records[b]["luna_sig"]},
            "signature_kimi": {"a": records[a]["kimi_sig"], "b": records[b]["kimi_sig"]},
            "triggers": triggers,
            "merge_decision": None,
            "recall_only": True})

    rule_counts = {}
    for _, triggers in ordered:
        for t in triggers:
            rule_counts[t["rule"]] = rule_counts.get(t["rule"], 0) + 1

    pair_ids_unique = len({p["pair_id"] for p in out_pairs}) == len(out_pairs)
    no_self_pairs = all(p["responsibility_id_a"] != p["responsibility_id_b"] for p in out_pairs)

    report = {
        "artifact": "SIGNATURE_AGREEMENT_REPORT_V2_2",
        "generated_by": Path(__file__).name,
        "deterministic": True,
        "inputs": {
            "packet": PACKET_PATH.name,
            "luna_annotations": LUNA_PATH.name,
            "kimi_annotations": KIMI_PATH.name,
            "kimi_isolation_audit": "KIMI_ISOLATION_AUDIT_V2_2.json"},
        "outputs": {"report": REPORT_PATH.name, "candidate_pairs": PAIRS_PATH.name},
        "identity_fields": IDENTITY_FIELDS,
        "validation": {
            "expected_n": EXPECTED_N,
            "counts": counts,
            "id_sets_equal": id_sets_equal,
            "duplicate_free_inputs": dup_free,
            "id_alignment_ok": aligned,
            "pair_ids_unique": pair_ids_unique,
            "no_self_pairs": no_self_pairs},
        "inter_rater_agreement": {
            "n_compared": n,
            "per_field_exact": field_rates,
            "outcome_key_exact": field_rates["outcome_key"],
            "full_signature_exact": {"agree": full_agree, "disagree": n - full_agree,
                                     "rate": round(full_agree / n, 6)}},
        "semantic_dedup_recall": {
            "policy": "recall_only_no_merge",
            "thresholds": {"name_seq_ratio": NAME_SEQ_THRESHOLD,
                           "outcome_seq_ratio": OUTCOME_SEQ_THRESHOLD,
                           "outcome_token_jaccard": OUTCOME_JACCARD_THRESHOLD},
            "candidate_pair_count": len(out_pairs),
            "trigger_rule_counts": rule_counts}}

    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")
    with open(PAIRS_PATH, "w", encoding="utf-8") as f:
        for p in out_pairs:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    summary = {
        "status": "ok",
        "n_ids_aligned": n,
        "field_exact_rates": {f: field_rates[f]["rate"] for f in IDENTITY_FIELDS},
        "full_signature_exact_rate": round(full_agree / n, 6),
        "outcome_key_exact_rate": field_rates["outcome_key"]["rate"],
        "candidate_pair_count": len(out_pairs),
        "trigger_rule_counts": rule_counts,
        "validations": {"id_alignment_ok": aligned, "pair_ids_unique": pair_ids_unique,
                        "no_self_pairs": no_self_pairs},
        "outputs": [REPORT_PATH.name, PAIRS_PATH.name]}
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
