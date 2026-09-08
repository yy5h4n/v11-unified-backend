# Harness V2 Independent Review — Round 4

Verdict: **MAJOR — protocol freeze, fake-backend conformance claims, real-backend integration, and Episode generation remain blocked.**

Reviewed frozen SHA-256 snapshot:

- `interaction_state_machine_v2.json`: `574b92f05db0e4747fa6b7ca89bbb24b9c64fe391b4a87ea3562e8805d80dc91`
- `rule_dsl_v0.json`: `6398da34850f5ab52474068df907f60aab59f61b5d866454acd25382d0b46005`
- `public_private_schema_v2.json`: `a101b863c0585d706308c63e979f1893a889827d5b6f8537d4a68894cacd78aa`
- `evaluator_and_group_protocol_v2.json`: `f8b6c1d20ee48da10a605812e418c7184fe857f683142cf6991532c7af32bebd`

## Blocking findings

1. **The interaction model is not a closed state machine.** It admits termination before start and track-illegal `ask_user`; inspect, clarification, yield, and accounting/state transitions lack complete exchanges.
2. **The public DTO admits contradictions and leakage.** It accepts fresh null observations, event callbacks without events, conflicting duplicate devices, self-asserted preference provenance, and case variants of private canary terms.
3. **Rule scheduling semantics conflict.** Fire-count/cooldown updates are described both before and after backend success, and tick-between expiry/trigger/release cases lack one total-order transition algorithm.
4. **Cross-schema types are inconsistent.** Identifiers, timestamps, and digests use different constraints, so objects accepted at one boundary cannot round-trip through another.
5. **The sealed trace cannot replay the actual Agent session.** It omits canonical bootstrap bytes, callbacks, inspect calls, ask/reply, yield, tool errors, Agent identity/output, and rule/subscription/wake snapshots.
6. **The evaluator loss is not reproducible.** Components lack pointwise loss operators, parameters, active-mask bindings, event selectors, and semantic checks for IDs, units, types, and positive weights.
7. **Normalized gain lacks baseline evidence.** No-op/oracle losses are unbound scalars rather than recomputable run receipts/traces with parity evidence.
8. **Counterfactual language diagnostics are declarations, not artifacts.** Deletion, shuffle, and paraphrase reruns are not sealed; identical Contract arms can pass using a self-asserted witness.

Additional confirmed protocol-event bypasses: `question_answered` may carry an error and `question_rejected` may omit one.

## Required repair order

1. Freeze one scheduler and turn/clarification exchange semantics.
2. Introduce shared primitive schemas and a mandatory semantic validator.
3. Close public DTO references, provenance, and recursive leakage checks.
4. Seal the complete session transcript.
5. Make loss and no-op/oracle bundles independently recomputable.
6. Store and verify real closed-loop language diagnostic runs.
7. Re-run every reported counterexample plus hand-computed golden vectors.

The review was performed by an independent read-only collaboration agent. Friday GLM MCP was unavailable in this session.
