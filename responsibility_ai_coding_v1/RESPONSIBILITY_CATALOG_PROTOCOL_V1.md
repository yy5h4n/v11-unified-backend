# Responsibility Catalog Induction Protocol v1

## Scope and blinding

Induce device-independent standing responsibilities from `EVIDENCE_LEDGER.jsonl`
and the two independent unit-coding files. Do not inspect backend inventories,
scenario catalogs, mined windows, existing Episode labels, or model performance.
The result is an AI-proxy-coded provisional catalog, never human validation.

## Unit-to-cluster rule

Cluster evidence by the maintained human outcome, beneficiary, temporal
lifecycle, and meaningful failure—not by device, trigger wording, or available
backend. Merge synonyms. Split unrelated conjunctions. Preserve a compound
objective only when evidence itself makes the objectives jointly managed or
traded off.

Every proposed cluster must record its evidence IDs, independence units, study
clusters, source forms, contradictory evidence, and the narrowest claim its
evidence supports. A single evidence unit may inform multiple facets but counts
only once toward any cluster's admission threshold.

## Admission paths

### `admitted_provisional`

A cluster is admitted through either:

1. **Direct path:** at least one unit is rated `strongly_implies` or `entails`
   by both coders, with an identifiable beneficiary, recurring/persistent
   temporal scope, accepted delegation, authorized action, and a meaningful
   failure or care objective after adjudication; or
2. **Convergent path:** at least three independent demand owners across at least
   two study clusters express the same maintained outcome, and the aggregate
   evidence supports beneficiary, recurrence, delegation, action authorization,
   and meaningful failure. Mechanism-only/default/vendor-authored rules do not
   count as demand owners.

### `candidate_needs_confirmation`

Use when at least three independent demand owners within one study cluster
converge on the same maintained outcome, but cross-study confirmation or a
direct standing-responsibility anchor is absent.

### `excluded_from_responsibility_catalog`

Use for one-shot convenience, mechanism-only records, unsupported purpose,
refused delegation, unresolved authorization conflict, or clusters below the
evidence thresholds. Preserve these records in the evidence ledger.

## Catalog object

Each retained cluster contains:

- `canonical_name` and one-sentence `objective`;
- `beneficiary` and `lifecycle`;
- `meaningful_failure` and `authorized_action_scope`;
- `evidence_ids`, `independence_unit_ids`, `study_cluster_ids`, and source-form
  counts;
- `admission_status`, `admission_path`, `admission_rationale`, `confidence`;
- `boundary_notes`, including nearby responsibilities not merged;
- `coding_disagreements` and contradictory/refusal evidence.

No backend capability, simulator variable, threshold, action schema, Episode
count, or baseline result may appear in admission reasoning.

## Version status

The first catalog is labeled `provisional_ai_validated`. Confirmatory held-out
evidence and genuine human coding remain future validation requirements.
