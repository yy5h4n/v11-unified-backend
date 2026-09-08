# V11 backend public protocol

The public factory is `unified_compiler.agent_interface.make_agent_backend(route_id, **kwargs)`. The canonical route inventory is `unified_compiler.route_registry.PUBLIC_ROUTE_IDS` and contains exactly 15 routes. Historical aliases resolve to canonical ids and are never separate runtimes.

Every backend exposes `reset(seed=0)`, `observe()`, `legal_actions()`, `step(action, dt_seconds=None)`, and idempotent `close()`. `reset` returns a receipt with `time_seconds=0`, `observation`, `action=None`, `done`, `terminated`, `truncated`, and `info`. `step` returns those fields plus `delta_t_seconds`. `time_seconds` is elapsed episode time; when a native receipt has a clock, the reset native clock is recorded as an origin and subtracted. Delta is computed from consecutive public times.

Calls before reset and after close raise `AgentLifecycleError`. Calls after terminal transition raise the same lifecycle error. Invalid finite-positive `dt_seconds`, non-finite action numbers, unknown factory arguments, and route-specific illegal actions are rejected before native transition. A failed native reset or step poisons the episode and requires a new reset. Observations and legal action schemas are deep copies.

`info` is a public whitelist. Private feedback, hidden schedules, witness actions, and evaluator conclusions are excluded. Native-specific diagnostic fields are namespaced under `info` only when explicitly public. Heavy runtimes use process-isolated execution in the acceptance runner; concurrency limits and timeouts are enforced by the runner, not by pretending thread safety.

The protocol does not turn batch replay into online physics. Under the user-approved [FDS replay target supplement](FDS_REPLAY_TARGET_SUPPLEMENT.md), FDS exposes a truthful `prefix_replay` capability: each transition runs a fresh real FDS process over the accumulated DEVC/CTRL/RAMP schedule, and `online_step_supported=false`; callers requesting synchronous online stepping are rejected. Replay Episode readiness is separate from interactive conformance and native lockstep/checkpoint claims.

EnergyPlus accepts its registered cadence values of 600, 1200, and 1800 seconds. The facade delegates these as repeated native callback barriers and reports the elapsed native time and delta for the complete request.

## CityLearn capability boundary

CityLearn multi-system Episode generation remains available from its native route, but the configured route is not a verified D3 cross-channel coupling capability: `d3_coupling_supported=false`. Independent battery and HVAC responses remain usable Episode evidence. Coupling-required selection must exclude this route until raw fixed-peer native evidence establishes that capability.

## LLM conversation boundary

The backend returns complete public observations. The LLM-facing conversation
layer in `unified_compiler.llm_conversation` sends the complete initial
observation once and then appends one canonical assistant action followed by
one `environment_observation` user message containing `action_result` and a
lossless `observation_delta`. The latest full observation is retained only in
the caller's process memory to compute the next delta. `validate_canonical_conversation`
replays the initial observation plus the delta chain and rejects tampered,
ambiguous, duplicate-key, or non-finite JSON payloads.

This layer is independent of the LLM provider. It does not make API calls or
choose a model. The system document must publish the route's device/action
schema (`device_interfaces`, `action_grammar`, or
`public_action_schema`); a runner can obtain it directly from
`backend.legal_actions()` and pass it to `extend_system_content` or the
conversation constructor. A provider runner should then pass
`CompactObservationConversation.messages()` to the model, append the returned
action with `append_action`, and append the native receipt's
`public_action_result_from_receipt(receipt)` and observation with
`append_environment`. The receipt projection intentionally excludes the full
observation and the echoed action from `action_result`.
