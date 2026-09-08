# V8 JSON History Migration Audit

## Decision

V8 combines the gateway-compatible V6 text action protocol with complete
closed-loop history.  Each model action is extracted from exactly one
`<answer>JSON_OBJECT</answer>` envelope, normalized, and replayed alongside the
environment feedback and newest public observation.  Native tool calling and
its capability probe are not part of V8.

## Request shape

The first request is:

1. `system`: the V6 household-agent rules, four action branches, device
   interfaces, and query-scoped observable event interface;
2. `user`: one canonical JSON object containing `original_query`,
   `public_preferences`, and `initial_observation`.

Each accepted subsequent step appends:

1. `assistant`: only the canonical `<answer>JSON_OBJECT</answer>` action;
2. `user`: a canonical JSON `environment_observation` containing
   `action_result` and `current_observation`.

The environment message uses the `user` role because OpenAI-compatible
`tool` messages require a preceding native `tool_call_id`.  This preserves the
tool/environment-feedback semantics without reintroducing the unsupported
native-tool transport dependency.

## Migration invariants

- The system message is byte-identical throughout one Episode.
- The original query, public profile, and initial observation appear once.
- All successfully parsed actions and all following public environment
  observations remain in chronological order.
- `previous_action` and `previous_action_result` are absent; history is not
  duplicated into the latest user payload.
- The exact V5 frozen 15-Episode sample and release hashes remain authoritative.
- V1--V7 runners, probe code, and historical outputs are untouched.
- Checkpoints must be an exact frozen-sample prefix, must rebuild byte-for-byte,
  are atomically replaced, and completed reports cannot be overwritten.
- Exhausted API transport retries are fatal to the current invocation: they
  are re-raised, never converted into `invalid_model_output`, never scored as
  `protocol_invalid`, and never append a failed Episode.  The exact completed
  prefix is atomically retained with `execution_finished=false` for resumption.
- Envelope or strict-JSON failures after a successful API response remain
  model protocol errors and retain the existing `protocol_invalid` semantics.

## Privacy and reproducibility

Raw model content and reasoning outside the answer envelope are never stored
or replayed.  Per-call records contain only the normalized action, protocol
error, response SHA-256, UTF-8 byte length, latency, and provider usage fields.
The canonical conversation therefore contains public experiment inputs,
normalized actions, and public environment results, but no hidden evaluator
contract, private backend state, future event schedule, or chain-of-thought.

## Token accounting

`api_output_tokens` is the primary model-output metric and exactly equals the
explicit provider-name aliases `completion_tokens` and `output_tokens`.
`prompt_tokens` and the provider's raw aggregate (reported as
`provider_total_tokens`) remain separate diagnostics under
`metrics_all.token_diagnostics`; no generic `total_tokens` report key is
emitted. Missing
fields stay zero, and a provider total is never inferred.  Presence and
mismatch counters preserve the audit trail.

## Verification scope

`tests/test_evaluate_workflow_v4_flash_v8.py` checks request chronology, full
history retention, removal of duplicate previous-action fields, exclusion of
thinking/raw content, absence of native-tool arguments, token accounting,
final action/observation pairing, canonical checkpoint validation, frozen
sample inheritance, system inclusion of device/event interfaces, fail-closed
transport handling, exact-prefix recovery, and unchanged 15-Episode normal
completion.
