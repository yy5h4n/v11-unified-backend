# Harness V2 Independent Review — Round 7

Verdict: **MAJOR — do not begin real-adapter Episode generation.**

The frozen snapshot passed 65 focused tests. The reviewer independently replayed the prior attacks and confirmed that the transaction post-state, profile drift, unbound build digests, diagnostic policy-identity swap, and diagnostic arm swap were rejected. The following correctly resealed attacks still passed.

## Remaining blocking attacks

1. **RESET-to-trace gap.** Changing every run's public reset temperature from 18°C to 29°C, then consistently resealing sessions, controls, group digests, and replay receipts while leaving the physical traces unchanged, still passed. The verifier must reconstruct public reset state from a trusted native backend snapshot and replay from that snapshot.
2. **Evaluator/loss rewrite.** Changing the top-level and both arm manifests from target 22°C to 21°C, replacing `implementation_hash`, and recomputing losses and score serialization still passed. Manifests need canonical digests and a trusted evaluator-code registry.
3. **Policy identity without execution identity.** Main Agent and A/source-near diagnostics claim the same policy/query/controls but issue different targets. A digest label does not prove execution by the same policy build and configuration.
4. **Counterfactual scope is not operational.** Arm B claims `whole_home`, but the scene exposes only a kitchen and both arm evaluators read the same single temperature. The contrast axis therefore does not change physical scope or evaluation scope.
5. **Persistent-rule path is unproven.** The golden fixture covers only one immediate command. It does not bind rule installation, scheduler firing, release, cancel, subscription, wake, rollback, or command-origin lineage end to end.

## Required remediation before the next review

- Add a trusted reset receipt containing a canonical native snapshot and a verifier-derived public projection.
- Replay from reset snapshot + seed/exogenous realization + sealed session stream, through a package/container-level verifier registry.
- Add canonical evaluator-manifest serialization/digest and pin evaluator implementation through the same trust registry.
- Add a trusted policy-execution receipt and pair all policy-visible nuisance inputs across one-factor runs.
- Replace the single-frame fixture with a multi-room, multi-frame golden scenario covering rule install → fire → release, a rejected transaction rollback, and A/B Contracts whose spatial scope changes both commands and evaluator inputs.

Only after these points pass correctly resealed mutation tests should Harness V2 be reviewed for permission to implement real adapters.
