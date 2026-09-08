# Standing Query Generation Instructions v2.3

Read and obey `STANDING_QUERY_PROTOCOL_V2_3.md`. Compile every input responsibility into exactly one JSONL row, preserving input order. Output JSONL only: no prose, heading, Markdown fence, omission, addition, merge, or ID change.

## Abstraction rule

Write one natural first-person delegation of the evidence-grounded responsibility. Preserve the beneficiary, durable human outcome, lifecycle, and evidence-supported material context or duration. Abstract away devices, sensors, actuators, tool names, IF/WHEN/THEN logic, action order, observation policy, waiting policy, escalation implementation, and notification channel unless one is itself the user-level outcome. Never inspect or mention backend capability, trajectories, scoring rules, oracle actions, or evaluator thresholds.

Keep an exact time or threshold only when it is the user-level outcome. Otherwise list the needed value in `profile_dependencies`. Do not invent preferences, hazards, beneficiaries, notification duties, termination conditions, or permissions. If the canonical name conflicts with evidence, evidence is insufficient, or the responsibility remains compound, set `generation_status` to `needs_review` and add the applicable flags; never silently repair or drop the item.

`standing_intent_id` must equal `si_` plus the suffix of `responsibility_id` after its first underscore. It is stable across every episode bound to this query.

## Exact output fields

Each line must contain exactly:

```json
{
  "standing_intent_id": "string",
  "responsibility_id": "string",
  "canonical_query": "string",
  "beneficiary": "string",
  "lifecycle": "MAINTAIN|GUARD|ACHIEVE_BY|PREPARE_FOR|RECOVER_AFTER_EVENT|OPTIMIZE_UNDER",
  "delegated_outcome": "string",
  "duration_boundary": "string or null",
  "profile_dependencies": ["string"],
  "evidence_grounded_constraints": ["string"],
  "configuration_variants_removed": ["string"],
  "source_evidence_ids": ["string"],
  "quality_flags": ["operational_leakage|oracle_leakage|unsupported_invention|ambiguous_outcome|missing_boundary|compound_responsibility|canonical_name_mismatch|needs_source_review"],
  "generation_status": "ready|needs_review",
  "rationale": "string"
}
```

Copy `source_evidence_ids` exactly from the input responsibility. Use the supplied blind signatures as aids, not as ground truth; resolve disagreements against the evidence and conservatively mark `needs_review` when they materially affect the query.

Before emitting each row, verify that the query is natural first-person delegation, expresses only one responsibility, is reusable across episodes, and contains no action recipe or hidden answer.
