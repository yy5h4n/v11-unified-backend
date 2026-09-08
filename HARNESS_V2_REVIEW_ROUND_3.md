# Harness V2 Independent Review — Round 3

Verdict: **MAJOR — fake backend, real backend, and Episode generation remain blocked.**

## What passed

- All four files are valid Draft 2020-12 JSON Schemas under both `jq -e` and `Draft202012Validator.check_schema`.
- The accounting/backend digest distinction, closed-loop counterfactual reruns, same-track grouping, callback-exhaustion behavior, and complete availability-aware inventory are directionally correct.

## Blocking bypasses found

1. A transaction accepted an empty `{}` rule instead of binding the Rule DSL.
2. Callback reasons could be configured to an empty list.
3. Rule references to missing named periods, nested event-filter canaries, and several lifecycle edge cases remained open.
4. Public DTOs lacked capability parameter schemas, event catalog, named periods, budgets, horizon policy, and other information promised by the prose; integer and RFC3339 time coordinates were mixed.
5. Private canaries could enter public semantic-name strings such as preference, observation, event, and capability names.
6. Trace command/event records were arbitrary objects; units, quality, chronology, loss input mapping, and transaction results were insufficient for reproducible scoring.
7. Excluded score branches could still carry arbitrary gains and `main_gain_pass=true`; canonical decimals accepted negative zero.
8. `one_factor_pair:true` allowed three members and faithful paraphrases could use different Contracts/evaluators.
9. Cross-object references between manifest, trace, group member, score serialization, and digests were not closed.

## Required remediation

- Bind create-rule transactions to the actual Rule DSL and add strict transaction outcomes/golden vectors.
- Complete deterministic lifecycle/cooldown/same-timestamp semantics and parameterless commands.
- Complete the public operating contract and one RFC3339 time system.
- Make trace and score branches strict and auditable.
- Represent each semantic contrast as exactly two Contract arms, with paraphrases nested inside each arm.

The review was performed by an independent collaboration agent in read-only mode. It was not a Friday GLM review because that MCP was unavailable.
