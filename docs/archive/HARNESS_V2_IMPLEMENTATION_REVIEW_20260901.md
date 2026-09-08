# Harness V2 implementation review — 2026-09-01

## Verdict

PASS, limited to the current `SimuHome thermal + explicit_profile_control + act/wait` integration and the provisional batch in `generated/harness_v2_full_episode_batch_v2/`.

The `research-review` skill's Friday GLM MCP was unavailable in this recovered session. A fresh independent read-only agent performed the fail-closed review instead; no reviewer edits were accepted into the implementation.

## Reviewed implementation

- `harness_v2/core.py`
- `harness_v2/simuhome_adapter.py`
- `harness_v2/capability_protocol.py`
- `harness_v2/evaluator_v3.py`
- `evaluate_harness_v2_v4_flash.py`
- `build_harness_v2_full_episode_batch.py`
- corresponding Harness V2 tests
- `generated/harness_v2_full_episode_batch_v2/`

## Evidence

- The complete Harness V2 regression suite passed: 170 tests.
- The focused integration suite passed: 88 tests.
- The independent scan review reproduced 600 source files, 161 raw windows, 62 strict threshold passes with 62/62 deterministic replays, and 11 unique initially-in-band candidates.
- The new builder admitted 11/11 Episodes: 5 kitchen and 6 multi-room Episodes across two FULL responsibilities.
- A second build was byte-identical for the public rows, private rows, validation gate, and build report before the final non-semantic documentation edit; the final build report hashes the current implementation files.
- All 11 Episodes completed under a full-batch FakeClient run with protocol-valid actions.

## Findings

1. The public action schema and server-side model-action validator are generated from the same capability manifest. The current track exposes only `act` and `wait`; `ask`, `install_rule`, and `cancel_rule` are unavailable.
2. Immediate commands, rejected transactions, rule firing, and rule release carry transaction/origin receipts. Rejected or failed atomic operations retain equal pre/post backend state digests.
3. The model wire view is derived only from `agent_view`; responsibility IDs, Query IDs, evaluator bindings, no-op/reference traces, validation diagnostics, and private target scope are not sent.
4. Evaluator V3 implements the half-open `[start, release)` interval, time-weighted and equal-room BandSat, Band-Maintenance Pass, public-target-only soft MAE, DeltaSat, strict reference-gap eligibility, explicit N/A dimensions, and committed/coalesced-only realized collateral.
5. The V4 runner calls Evaluator V3. Legacy MAE remains construction diagnostics only and is not the primary reported benchmark score.

## Allowed claims

- This is a provisional batch of 11 synthetic SimuHome thermal Episodes for two FULL responsibility types.
- It supports reporting BandSat, Band-Maintenance Pass, DeltaSat, and NRG only where the preregistered reference-gap and unit gates are satisfied.
- The batch is deterministically rebuildable under the recorded implementation and source hashes.

## Claims not supported

- Rule concurrency, conflict resolution, or command coalescing is not certified on the current model track.
- Hash-linked receipts are tamper evidence under a trusted builder, not cryptographic signatures against a malicious builder.
- Lifecycle, Energy, and Formal Safety remain unscorable/N/A for this batch.
- SimuHome dynamics are synthetic; no calibrated physical realism claim is allowed.
- This review does not establish that every future backend is Harness-compatible.

## Real-model smoke result

After network connectivity was restored, one `deepseek-v4-flash-meituan` Episode completed successfully. The persistent report is:

`runs/harness_v2_v4_flash_smoke_v2_20260901/report_success.json`

- 23/23 API calls produced protocol-valid actions; the run produced 24 observations and completed.
- Evaluator V3 reported BandSat 1.0, Band-Maintenance Pass true, DeltaSat approximately 0.95554, and eligible NRG 1.0.
- All 23 actual applied commands were committed and in-scope; realized collateral was 0/23.
- An independent read-only replay reproduced the exact trace digest and all reported V3 metrics.
- The model issued the same semantic command at all 23 decision points. Energy, lifecycle, formal safety, and repeated-action cost remain unscored, so this smoke does not establish adaptation, efficiency, safety superiority, or batch-level generalization.
- The smoke report seals the public-batch hash and trace digest but not the runner/private-file hashes. The current file combination is exactly replayable, but the report provenance envelope should be strengthened before a formal benchmark release.
