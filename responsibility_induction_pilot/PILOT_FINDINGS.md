# Responsibility Induction Pilot — First Findings

## Scope and status

This is an AI-assisted schema dry run over 12 source-grounded evidence units,
not formal human annotation and not a released responsibility ontology. The
units cover one longitudinal autoethnographic household, four TAREME field-trial
households, and four participants' hypothetical desired automations.

## Pre-adjudication agreement

| Field | Exact agreement | Cohen's kappa | Interpretation |
|---|---:|---:|---|
| Construct type | 11/12 (91.7%) | 0.887 | The coarse task/goal/preference/implementation/unknown boundary is promising |
| Abstraction level | 10/12 (83.3%) | 0.736 | L1 versus L2 and L2 versus L4 need sharper anchors |
| Evidence relation | 9/12 (75.0%) | 0.600 | The v1 field conflated entailment of a preference/goal with support for a standing responsibility |
| Confidence | 10/12 (83.3%) | 0.657 | Confidence anchors need operational definitions |
| Candidate-statement presence | 11/12 (91.7%) | not computed | Coders mostly agreed on when any construct could be stated |

These statistics describe two AI coders debugging a schema. They must not be
reported as human intercoder reliability.

## Most important empirical result

Neither coder labeled any of the 12 individual evidence units as a standing
responsibility. Both instead found:

- goals without established standing ownership;
- preferences and value conflicts;
- one bounded task;
- implementations whose underlying purpose is ambiguous.

This is desirable conservatism. It demonstrates why the benchmark cannot map
one rule to one responsibility. A responsibility must be adjudicated from a
cross-evidence bundle after grouping compatible units from independent
households and checking whether ownership, beneficiary, persistence, failure,
and release are jointly supported.

## Candidate bundles, not admitted responsibilities

The current evidence suggests several bundles worth expanding, but none yet
passes the admission gate:

1. **Context-adaptive thermal comfort.** The longitudinal diary explicitly
   reports discomfort when an obsolete away schedule conflicts with actual
   occupancy. A hypothetical temperature/humidity rule supplies only compatible
   mechanism evidence. More independent purpose evidence is needed.
2. **Support for recurring care and training activities.** Two TAREME households
   describe periodic reminders and personalized support. The evidence is
   promising but largely comes through one study modality and does not fully
   establish accountable ownership, failure meaning, or release conditions.
3. **Respecting autonomy and privacy boundaries in household monitoring/care.**
   One household reports guilt about monitoring a cleaner; another resident
   explicitly refuses a replacement wearable. These establish real boundary
   evidence but are not interchangeable enough to merge into one evaluator.
4. **Preparation for anticipated events.** A participant-authored rain/calendar
   rule clearly expresses a bounded preparation task, not yet a recurring
   standing responsibility.

## Codebook revisions caused by the dry run

Version 2 makes four changes:

- list-valued constraints and trade-offs now have an explicit array type;
- entailment of the extracted task/goal/preference is separated from support
  for a standing responsibility;
- six responsibility slots receive explicit present/absent/ambiguous labels;
- extracted constructs and standing-responsibility hypotheses are stored in
  different fields.

## Decision

No responsibility is admitted from this batch. The next batch should
purposefully add independent households and evidence modalities to the four
candidate bundles, plus negative examples where similar actions have different
purposes. Only cross-evidence bundles that satisfy the frozen semantic gate may
become canonical responsibilities and later drive backend window mining.

