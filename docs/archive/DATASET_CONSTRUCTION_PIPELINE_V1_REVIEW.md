# Dataset Construction Pipeline v1 — Review Record

## Scope

This record covers the holistic review and freeze decision for
`DATASET_CONSTRUCTION_PIPELINE_V1.md`. The construction specification, not any
legacy V11 data release, was under review.

## Round 1

The first independent review returned `major revision / do not freeze`. It found
that Revision 1 did not operationally identify responsibility, mixed semantic
unknown with physical no-op, contradicted itself about solver membership, lacked
mathematical opportunity strata and public-information tests, mixed semantic and
physical split genealogy, and did not define finite-window persistence or an
executable conformance package.

Revision 2 narrowed responsibility to participant-supported household maintenance
objectives; added a source-type inference matrix, discovery/development/
confirmatory partitions, numeric admission rules, independent semantic and
physical statuses, RFC 8785/SHA-256 lineage, `V0/Vpub/Delta` strata, a declared
construction oracle, information-set testing, separate provenance graphs,
finite-window continuation semantics and a machine-readable package requirement.

## Round 2 blind review

A fresh reviewer read only Revision 2 and again returned `major`. The remaining
issues were specification determinism rather than story direction: confirmatory
statistics, critical-case status, Contract compilation, freeze/QA ordering,
undefined arithmetic over lexicographic scores, unprovable no-op claims, policy
selection over private processes, seed reuse, horizon gaming, split algorithms
and cross-object conformance.

Revision 3 resolved these by:

- defining independence units, confirmatory denominators and dual-annotator gates;
- separating semantic, authorization, physical and release status;
- adding a temporal Contract DSL and clause-coverage matrix;
- making frozen Contract changes versioned restarts rather than post-scan edits;
- separating lexicographic evaluation from bounded construction utility;
- using `Pi_cert` only for positive witnesses and requiring a `Pi_auth_pub`
  upper-bound certificate for certified no-op;
- classifying public-history equivalence groups with one policy and disjoint
  search/certification seeds;
- defining terminal guard-band/viability semantics;
- defining track-specific semantic/physical conflict graphs and a deterministic
  splitter;
- requiring an end-to-end reference validator/evaluator/package.

The reviewer then found only four local ambiguities. Revision 3 was amended to
define each annotator as a separate confirmatory predictor, separate `Pi_cert`
from `Pi_auth_pub`, require a common authorized acceptable-policy intersection,
and estimate `Delta_witness` for the selected policy rather than an unattainable
population maximum. The reviewer concluded the text could freeze once the actual
hash-bound package existed and passed.

## Conformance-package review

The generated `conformance_v1` package was not accepted on self-report. Independent
re-execution first passed 105 tests, but manual review found two blockers: the
manifest did not hash every artifact, and Python float notation did not fully
implement ECMAScript/JCS boundary formatting. Both were repaired. The final
package binds the normative document and every non-generated artifact into release
tuple:

`9281b2930d40ab8d2998527e315e885ceddb6f27888ca3faff744bbc568cfb7f`

The clean final run passed 108 tests with zero failures, errors or skips.

## Freeze conclusion

Verdict: `frozen specification release 1.0`.

Allowed claim: the project defines a reproducible pipeline for deriving
human-evidenced household responsibilities, freezing Query/Contract semantics,
mining publicly actionable physical opportunities, compiling finite executable
Responsibility Episodes and releasing leakage-controlled splits.

Not allowed: existing V11 artifacts are not automatically evidence-grounded;
the AI pilot is not human validation; the benchmark does not yet establish
population prevalence, real-world deployment performance or implicit-intent
inference.

## Next implementation gate

Implementation now begins at Stage 1–3: acquire/audit the real-user evidence
frame and run formal human coding/admission. Only `human_validated` or separately
qualified `critical_case_validated` responsibilities with positive Agent-control
authorization may enter backend scanning under this release.
