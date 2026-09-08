# Atomic Responsibility Extraction and Conservative Deduplication Protocol v2

## Purpose

Construct a fine-grained responsibility catalog from the 223 source-grounded
evidence units. The former 15 clusters are navigation families only. Evidence
strength annotates atomic responsibilities; it never deletes a semantically
valid candidate.

## Atomic responsibility

An atomic responsibility is the narrowest device-independent proposition that
specifies:

1. a beneficiary;
2. a maintained, guarded, prepared, achieved, recovered, or optimized human
   outcome;
3. a temporal lifecycle;
4. a meaningful failure interpretation or an explicitly unknown one; and
5. the context in which the responsibility applies.

Split unrelated conjunctions. Preserve multiple objectives together only when
the source expresses an inseparable trade-off or joint managed outcome.

Device names, trigger syntax, numeric thresholds, schedules, notification
channels, and actuator choices are implementation or contract variants, not
responsibility identity.

## Identity signature

Each proposition carries this semantic signature:

`beneficiary_class × lifecycle × outcome_key × failure_mode × context_key`

- `beneficiary_class`: household | resident | child | infant | older_adult |
  visitor | companion_animal | plant | property | grid_society | unknown
- `lifecycle`: MAINTAIN | GUARD | ACHIEVE_BY | PREPARE_FOR |
  RECOVER_AFTER_EVENT | OPTIMIZE_UNDER
- `outcome_key`: concise device-independent snake-case outcome
- `failure_mode`: harm | discomfort | security_failure | care_lapse | waste |
  shortage | task_incompletion | inconvenience | unknown
- `context_key`: always | occupied | unoccupied | night | departure | arrival |
  scheduled | threshold_event | hazard_event | weather_event | unknown

Two propositions are duplicates only when all five signature components match
and their objectives are mutually substitutable. Lexical similarity, shared
devices, or membership in one family is insufficient.

Authorization and delegation are attached as evidence-supported variants:
`notify_only`, `recommend`, `act_and_notify`, `autonomous_act`, or `unknown`.
They do not create new responsibility identities.

## Extraction record

For every evidence unit emit one or more records with:

- `proposition_id`, `evidence_id`, `is_responsibility_candidate`;
- `family`, `canonical_name`, `objective`;
- all five signature fields;
- `delegation_variants`, `authorized_action_summary`;
- `source_support` and `exclusion_reason` when not a candidate;
- `confidence` and concise `rationale`.

Every evidence ID must appear at least once. A compound evidence unit may emit
multiple proposition records, each retaining the same evidence ID.

## Evidence aggregation after deduplication

For each unique atomic responsibility aggregate evidence IDs, independence
units, study clusters, source forms, coder support, delegation variants, and
contradictions. Assign, without filtering:

- `well_supported`: jointly strong direct evidence, or at least three
  independent demand owners across at least two study clusters;
- `recurrent_single_source`: at least three independent demand owners but no
  cross-study convergence;
- `repeated_candidate`: two independent demand owners;
- `isolated_candidate`: one independent demand owner;
- `not_a_responsibility`: mechanism-only, unsupported purpose, explicit refusal
  without an accepted form, or no maintainable outcome.

The catalog must expose all levels. Only later benchmark-release policy decides
which evidence levels enter a release.

## Blinding and status

Extraction and deduplication must not inspect backend capabilities, scenario
catalogs, Episode counts, mined windows, or baseline performance. Outputs are
`provisional_ai_derived`, not human validated.
