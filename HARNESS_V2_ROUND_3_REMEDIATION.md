# Harness V2 Round-3 Remediation Map

Status: **ready for Round-4 independent review; implementation remains blocked.**

## 1. Rule-to-transaction binding

`interaction_state_machine_v2.json#/$defs/ruleCreation` now references the root of `rule_dsl_v0.json`. An empty rule is rejected. The Rule DSL root is one rule; timezone, horizon, concrete named periods, observation/event catalogs, and capability schemas come from the public Episode context and are mandatory runtime validation inputs.

## 2. Transaction outcomes and exact accounting

Rejected and committed outcomes are distinct `oneOf` variants. A rejected outcome exposes only one `unchanged_backend_state_digest`, so contradictory pre/post digests cannot be encoded, and its ledger delta is fixed to one attempt plus one protocol error. A committed outcome fixes the error delta to zero. Per-mutation outcomes are strict, and golden expectations cover partial-invalid rollback and full commit.

## 3. Deterministic timing and lifecycle

All runtime instants are RFC3339 with explicit offsets. Agent transaction commit occurs after all simulator phases at a callback timestamp; new rules/subscriptions take effect strictly afterward; present/past rule or wake timestamps are rejected. Cooldown begins only after successful firing; failed/suppressed firings do not count. Expiry precedes same-timestamp triggers, and once/max-fire retirement counts successful firings only.

## 4. Complete public operating contract

The public DTO now includes:

- complete rooms and availability-aware device inventory;
- capability/operation/parameter schemas, including empty parameter arrays for parameterless operations;
- observation and event catalogs;
- concrete named periods;
- exact/bounded/undisclosed horizon variants;
- interaction budgets, public action costs, safety limits, and provenance;
- track-specific exact tool lists, so `ask_user` is impossible in the explicit-profile track.

Semantic identifiers reject private-canary tokens, and runtime catalog/reference checks plus dependency canaries remain mandatory.

## 5. Reproducible sealed trace and evaluator

Trace values carry value, unit, quality, and observation time. Commands and rule/protocol/safety events have strict schemas. Runtime checks require contiguous unique frames, monotonic timestamps, duration consistency, exact manifest-input/unit resolution, and event/reference integrity. Missing/stale behavior and constant-imputation prerequisites are executable.

Scores use mutually exclusive variants for eligible, protocol-invalid, unsafe, oracle-failed/nonfinite, and denominator-boundary cases. Excluded variants cannot carry headline gains. Canonical decimals reject exponents, trailing zeros, and negative zero. Component metrics and RFC 8785 serialization evidence are required.

## 6. Two-arm counterfactual groups

A group contains exactly two ordered Contract arms, A and B. Contract and evaluator identifiers live at arm level; each arm contains exactly one source-near Query followed by faithful paraphrases, preventing paraphrase-specific evaluator drift. A contrast witness plus runtime byte comparison proves that only `contrast_axis` differs. Every shuffle/deletion is rerun closed-loop under the frozen exogenous realization.

## Validation performed

All four files pass `jq -e` and `Draft202012Validator.check_schema`. Positive tests cover a parameterless rule command, a transaction with an externally referenced rule, an exact-ledger rejected outcome, a complete explicit public view, and a full evaluator/group/trace/score package.

Negative tests reject empty rules, invalid RFC3339 timestamps, private condition/preference/observation canaries, `ask_user` in the explicit track, incorrect rejection accounting, excluded scores carrying gain, negative zero, constant imputation without a value, empty trace event records, a third Contract arm, paraphrase-level evaluator override, and false reference-integrity assertions.
