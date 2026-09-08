# Harness V2 Independent Review — Round 2

Verdict: **MAJOR — implementation and Episode generation remain blocked.**

Reviewer scope: `HARNESS_V2_SPEC.md` and the four files under `harness_v2/`. The reviewer was an independent collaboration agent, not Friday GLM; the Friday review MCP was unavailable in this session.

## Blocking findings

1. The four JSON files were structured design notes, not executable JSON Schemas.
2. Transaction failure conflated backend state with the accounting ledger. The backend digest must remain unchanged while exactly one attempt/error may be recorded outside that digest.
3. Rule DSL variants lacked discriminated schemas and complete semantics for missing/stale values, period boundaries, simultaneous order, conflicts, release, cancellation, and backend failure.
4. Physics integration, exogenous updates, device/workflow updates, event detection, rule evaluation, command application, callback delivery, and termination lacked one total order.
5. Workflow results were inconsistently described as unconditional callbacks versus subscription-gated callbacks.
6. `source_hash` and `visibility_justification` were incorrectly Agent-visible; `ask_user` lacked a bounded slot protocol.
7. Evaluator manifests, trace frames, units, integration boundaries, normalizers, oracle implementation/tolerances, exceptional gain branches, and canonical serialization were underspecified.
8. Counterfactual groups incorrectly froze endogenous observations/events and query shuffle reused a backend trace. Only initial public state, inventory, seed, exogenous realization, costs, budgets, horizon policy, split, and track may be frozen; every policy must rerun closed-loop.
9. Pilot counterfactuals must be one-factor pairs with a declared `contrast_axis`; faithful paraphrases share an evaluator; groups never cross track.
10. Callback-budget exhaustion must suppress callbacks without stopping installed rules. Complete inventory includes unavailable/offline devices and public availability.

## Required pre-implementation evidence

- Draft 2020-12 schema validation for all four contracts plus positive/negative instance tests.
- Cross-contract vocabulary and lifecycle consistency.
- Independent re-review with no blocking findings.
- Only after approval: fake-backend conformance tests. Real adapters and Episode generation remain prohibited until the fake backend passes.
