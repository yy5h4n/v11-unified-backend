# Harness V2 Independent Review — Round 1

## Verdict

**MAJOR REVISION. Implementation and new Episode generation remain blocked.**

The reviewer agreed that V2 targets the right research object by separating simulator ticks, Agent decisions, and offline scoring. It also correctly prohibits target-device leakage, semantic defaults, fallback actions, and evaluator callbacks. However, the draft did not yet define machine-executable semantics.

## Blocking findings

1. One Agent turn and its atomic transaction boundary were ambiguous.
2. `create_rule` lacked deterministic trigger, time, conflict, lifecycle, failure, and release semantics.
3. Physics, rule evaluation, and Agent callback clocks were not fully separated.
4. `benchmark_scenario_parameter` could become a new route for publishing private gold information.
5. Interactive clarification was mixed with explicit-profile control.
6. Private loss, safety ordering, semantic oracle, and raw versus clipped gain were underspecified.
7. Counterfactual groups did not require query-independent public payloads or quantified policy separation.
8. No current real adapter satisfies V2 atomic, full-home, and information-boundary requirements.

## Accepted corrections

The response to this review is frozen in four machine-readable draft contracts:

- `harness_v2/interaction_state_machine_v2.json`
- `harness_v2/rule_dsl_v0.json`
- `harness_v2/public_private_schema_v2.json`
- `harness_v2/evaluator_and_group_protocol_v2.json`

These contracts define a read phase followed by exactly one atomic mutation transaction, a deliberately small deterministic rule DSL, three independent clocks, separate explicit and interactive tracks, a privileged semantic oracle restricted to Agent-equivalent online information, raw gain/regret reporting, and byte-identical counterfactual public contexts after query normalization.

## Required executable tests before approval

- Invalid second command rolls back the first command and preserves the snapshot digest.
- Mixed act/create/cancel transaction failure rolls back all mutations.
- Missing device/mode/target is rejected without default or fallback.
- Golden rule tests cover period edges, simultaneous conflict, missing sensor, lifecycle release, and backend failure.
- Internal rules execute across physics ticks without Agent callbacks.
- Unsubscribed events are trace-only; subscribed events wake exactly once; private risk never wakes.
- Nested private canaries cannot serialize into public payloads.
- Counterfactual normalized Agent views match after replacing Query with a placeholder.
- Query shuffle freezes evaluator and increases cross-application regret.
- Shortcut policies fail on main scenario groups.
- Raw gain may be negative and small opportunity gaps are stratified as boundary.
- A sealed trace reproduces all metrics without runtime access.

## Current decision

Do not modify real backends and do not generate new Episodes yet. Round 2 must review the four machine-readable contracts. After design approval, a fake backend conformance suite is implemented before any SimuHome, EnergyPlus, CityLearn, or EV adapter is promoted to `FULL`.
