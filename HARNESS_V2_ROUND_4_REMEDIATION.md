# Harness V2 Round-4 Remediation Plan

Status: **in progress; implementation and Episode generation remain blocked.**

## Normative split

Harness V2 uses three enforcement layers. Passing only one layer is never conformance:

1. **JSON Schema** validates local shape, conditional variants, unknown fields, and shared primitive formats.
2. **Semantic validator** validates cross-record time order, uniqueness, references, provenance evidence, state transitions, units, score recomputation, and recursive leakage.
3. **Golden-vector suite** executes the scheduler, transaction engine, sealed transcript, baseline bundles, and language diagnostics against hand-computed positive and adversarial cases.

## Frozen scheduler

At every next scheduler instant, the runtime forms the union of physics boundaries, exogenous updates, device/workflow completions, concrete named-period edges, rule trigger/expiry instants, requested wakes, user replies, horizon, and subscribed public events. It advances to the earliest instant and executes exactly once in this order:

1. integrate physics from the prior instant using prior committed inputs;
2. apply exogenous updates, then device/workflow completions;
3. materialize public/private observations and events with stable IDs;
4. expire rules and collect release batches;
5. collect trigger occurrences and evaluate conditions on the post-update public snapshot;
6. resolve firing conflicts and atomically attempt batches;
7. update cooldown/fire count **only for successfully committed firings**, retire newly completed rules, and collect their release batches;
8. resolve and atomically attempt release batches; a failed release retires without retry;
9. record command/workflow/rule/protocol/safety results and the post-phase state digest;
10. apply horizon precedence; otherwise coalesce eligible public callback reasons;
11. deliver at most one callback; after its read phase, validate and commit exactly one terminal choice;
12. make newly installed rules/subscriptions/wakes effective strictly after that commit instant.

There is no generic periodic “rule tick.” Timestamp triggers and expiry edges are scheduler instants; public events are evaluated at the instant they are recorded. A backend failure does not consume fire count or start cooldown. No rule is evaluated twice at one instant.

## Repair packages

- **R4-A Interaction:** add complete `turnExchange`, `inspectExchange`, `clarificationExchange`, state transition, accounting, track, and budget bindings.
- **R4-B Public boundary:** enforce observation quality/value variants and callback payload variants locally; validate inventory/reference uniqueness and evidence-bound preferences semantically; case-fold all recursively inspected keys and strings before leakage matching.
- **R4-C Shared types:** all schemas reference one identifier, timestamp, digest, and decimal vocabulary; conformance always enables `FormatChecker`.
- **R4-D Transcript:** seal canonical bootstrap bytes plus every delivered callback, inspect request/response, terminal choice/outcome, clarification, error, state digest, Agent/policy identity, and accounting snapshot.
- **R4-E Evaluation:** define pointwise operators, parameters, masks/event selectors, units and aggregation; require unique IDs, compatible types, and at least one positive weight.
- **R4-F Baselines:** bind agent/no-op/oracle run receipts to independently sealed traces, policies, identical frozen exogenous inputs, public-information parity, and recomputed score branches.
- **R4-G Language diagnostics:** store real source/paraphrase/deletion/shuffle closed-loop reruns; compute one-factor Contract differences rather than trusting a witness assertion.

## Mandatory adversarial vectors

The suite must reject every Round-4 counterexample: termination before start, illegal explicit-track clarification, fresh-null observation, reason/payload mismatch, duplicate conflicting devices, self-asserted private preference, case-variant canary, inconsistent question outcomes, whitespace IDs, uppercase SHA, incomplete session trace, zero-weight/non-recomputable loss, unbound baseline scalar, identical Contract arms, and missing diagnostic reruns.
