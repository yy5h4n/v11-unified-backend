# Harness V2 Independent Review — Round 5

Verdict: **MAJOR — fake-backend conformance remains blocked.**

Seven schemas passed Draft 2020-12 meta-validation and the local suite reported 18 passing tests, but the positive fixtures were not complete instances of the formal session, manifest, or scenario-group schemas. The green result was therefore local rather than end-to-end.

## Blocking findings

1. Explicit-profile turns can still encode a clarification exchange; budgets and cross-turn state/ledger continuity are not bound.
2. Recursive leakage validation covers only bootstrap views, not callbacks, inspect responses, execution feedback, or canonical bytes.
3. Scheduler semantics remain descriptive and retain stale `first_rule_tick` language; no executable rule-state transition ledger exists.
4. Shared IDs, digests, timestamps, decimals, and mandatory `FormatChecker` are not enforced through one top-level entrypoint.
5. Canonical payload/transcript digests are not recomputed, transcript grammar is not reconstructed, and session/physical-trace swaps are possible.
6. Loss operators are not typed or evaluated from trace inputs, masks, events, units, missingness, and unequal durations.
7. Run receipt status/loss/parity are self-asserted; protocol-invalid runs can be presented as eligible scores.
8. Language diagnostics do not require paraphrases or bind queries to independent closed-loop session/trace receipts and measured separation.

## Required next architecture

Build one complete top-level Schema-valid golden scenario bundle first. A single `validate_conformance()` entrypoint must then run, in order: full-schema validation with shared registry and `FormatChecker`; canonical-byte and transcript verification; scheduler/state-ledger verification; trace evaluator recomputation; baseline receipt and score recomputation; and counterfactual diagnostic verification. Every negative test must mutate exactly one field of the same valid golden bundle.

The review was performed by an independent read-only collaboration agent; the input SHA-256 snapshot remained stable.
