# Harness V2 Round-2 Remediation Map

Status: **ready for independent Round-3 review; not approved for implementation.**

## Changed contracts

- `harness_v2/interaction_state_machine_v2.json`
- `harness_v2/rule_dsl_v0.json`
- `harness_v2/public_private_schema_v2.json`
- `harness_v2/evaluator_and_group_protocol_v2.json`

All four are now executable JSON Schema Draft 2020-12 documents with `$schema`, `$id`, root `type`, `required`, `additionalProperties: false`, `$defs`, and normative `x-*` semantic tables.

## Finding-to-fix map

1. **Executable contracts** — all four documents pass `Draft202012Validator.check_schema` and `jq -e`.
2. **Transaction atomicity** — `interaction_state_machine_v2.json` defines the complete transaction payload, ordered validation, duplicate/reference checks, rollback scope, backend digest invariance, and the separate allowed accounting delta.
3. **Rule DSL** — `rule_dsl_v0.json` uses `oneOf` plus constant discriminators for triggers, recursive conditions, and lifecycle variants; every rule requires cooldown and priority. Normative tables cover cross-midnight periods, timestamp quantization, missing/stale inputs, ordering, conflicts, cancellation, release, and backend failure.
4. **Three clocks and one total order** — the interaction contract now orders integration, exogenous updates, device/workflow updates, event detection, rule evaluation, command application, event recording, callback coalescing, and termination.
5. **Workflow callbacks** — only subscribed workflow results are eligible; same-timestamp reasons coalesce into one callback.
6. **Information boundary and clarification** — audit-only metadata is excluded from the public DTO; nested canary names are rejected in extensible public maps. `ask_user` permits exactly one enumerated slot and specifies invalid/repeated-attempt accounting and grounded answers.
7. **Evaluator reproducibility** — the evaluator contract requires a versioned family manifest, component inputs/units/integration/missingness/normalization/aggregation/weights, sealed trace frames, a frozen oracle implementation/solver/tolerances, all exceptional gain branches, and RFC 8785 canonical metrics.
8. **Closed-loop counterfactuals** — groups freeze only pre-policy and exogenous quantities. Endogenous trajectories may diverge. Query shuffle/deletion must rerun closed-loop and fixed trace reuse is forbidden.
9. **Identifiable pilot contrasts** — each group is a same-track one-factor pair with one `contrast_axis`; faithful paraphrases share contract and evaluator digests.
10. **Budget/inventory behavior** — callback exhaustion suppresses later callbacks while installed rules continue to termination. Public inventory is complete across available/unavailable/offline controllable devices and is Query/Contract independent.

## Validation evidence

The following positive cases validated:

- explicit-profile interaction configuration;
- a named-period standing thermal rule;
- a public Agent view containing an offline controllable device.

The following negative cases were rejected:

- clarification enabled in the explicit-profile track;
- unknown trigger fields;
- missing rule priority;
- a private condition field;
- top-level `source_hash` in Agent view;
- nested evaluator canary in a public event payload.

Round-3 reviewer must inspect both schema executability and the normative `x-*` tables. Passing schema syntax alone is not approval.
