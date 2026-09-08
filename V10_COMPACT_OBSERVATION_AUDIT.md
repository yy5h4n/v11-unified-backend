# V10 Compact Observation Audit

## Status

Implemented and green.  V10 keeps every V8/V9 API-boundary semantic and changes
two things: the v10 system message now explains the delta protocol to the
evaluated agent, and what the environment says after the bootstrap turn — the
full observation snapshot is never repeated, and each environment turn carries
a deterministic recursive delta instead.

## Decision

An earlier draft dynamically rebuilt each request and kept a full snapshot in
the newest environment turn.  That design was corrected: V10 preserves V8's
simple append-only history exactly.  `CompactDeltaObservationPolicy` holds one
`self.history` list that is only ever appended to; `build_request()` returns a
deep copy of it and never reconstructs or reorders prior messages.

## Request shape

The first request is byte-identical to V8's except for the v10 system
extension (below):

1. `system`: the V6 household-agent rules, action grammar, device interfaces,
   and query-scoped observable event interface, extended with the v10
   `observation_delta_protocol` block;
2. `user`: one canonical JSON `initial_request` containing `original_query`,
   `public_preferences`, and the full `initial_observation` (obs0).

Each subsequent step appends exactly:

1. `assistant`: only the canonical `<answer>JSON_OBJECT</answer>` action;
2. `user`: one canonical JSON `environment_observation` containing the exact
   `action_result` plus `observation_delta` — a recursive diff between the
   immediately preceding observation and the new one.

So the sequence literally stays
`system, user(obs0), assistant(a1), user(result1+delta1), assistant(a2), user(result2+delta2), ...`
with no `current_observation`/full snapshot anywhere after obs0 and no
dynamically rebuilt or replaced prior messages.

## V10 system-prompt extension

Previously v10 reused the v6 system content byte-for-byte, so the evaluated
agent was never told how to read `observation_delta`.  `build_v10_system_content`
now parses the v6 system document, adds one fixed top-level
`observation_delta_protocol` key carrying `instructions` (a fixed tuple of
concise statements), and re-serializes with v6's exact canonical settings
(`ensure_ascii=False, sort_keys=True, indent=1`), so the result is
deterministic and the episode-static system prefix stays byte-identical
across calls.  The instructions state exactly: `initial_observation` is the
full baseline; every later `environment_observation` contains the exact
`action_result` plus an `observation_delta` relative to the immediately
preceding reconstructed observation; apply deltas sequentially; `op=none`
means no change; `op=replace` carries before/after and the `after` value is
used; `op=object` removes the keys in `removed`, adds the values in `added`,
and recursively applies `changed`; `op=array` recursively applies `changed`
entries keyed by decimal index; a missing key differs from an explicit `null`;
and omitted unchanged fields are never absent.  No internal evaluator or
private data (e.g. the in-memory latest full observation) is exposed — only
the public wire protocol.  Policy initialization and `_verify_static_prefix`
both build through this function, and `expected_episode_prompt` computes its
hash from it, so checkpoint validation pins the explained prompt, and
`prompt_template_sha256` binds the exact instruction text.

## Delta codec

`observation_delta` / `apply_observation_delta` provide a total, generic,
deterministic codec over dict/list/scalar structures:

- canonically identical snapshots produce the explicit `{"op": "none"}`;
- recursive `object` nodes carry `added` values, sorted `removed` keys, and
  `changed` sub-deltas; same-length `array` nodes recurse per index;
- scalars, type changes, list length changes, and bool/int distinctions become
  exact `replace` nodes carrying both `before` and `after`;
- missing keys (`removed`/`added`) and explicit `null` values are never
  conflated;
- apply is strict: replace `before` values, removal/addition preconditions,
  recursion keys, and array indices are all verified, so a tampered delta
  cannot silently apply.

Obs0 plus the persisted delta chain therefore reconstructs every later
observation exactly (exact roundtrip, including nested changes, missing vs
null, and lists).

## Latest full observation

The policy retains the last full observation privately in process memory
(`self._last_observation`) solely to compute the next delta.  It is never sent
again and never persisted: the report stores only the canonical conversation,
whose environment turns are action results and deltas.

## Preserved V8/V9 semantics

- `<answer>JSON_OBJECT</answer>` text envelope; native tools never sent.
- No thinking or raw model prose is replayed or persisted (SHA-256 + byte
  length only).
- `api_output_tokens` is the primary token metric; `prompt_tokens` and the
  provider aggregate stay diagnostic under `metrics_all.token_diagnostics`;
  missing provider fields stay zero; totals are never synthesized.
- Exhausted transport retries stay fatal (fail-closed): `APIError` aborts the
  Episode, is never converted into `invalid_model_output`, and the completed
  subset is checkpointed with `execution_finished=false`.
- Envelope/strict-JSON failures remain model protocol errors.
- V9 Episode-level concurrency: default `--workers 30`, one `ChatClient` and
  policy per Episode, strictly serial calls within an Episode, main-thread-only
  atomic checkpoint writes, and results always in frozen-sample order.
- Checkpoints accept any completed subset (validated strictly, canonically
  ordered), are atomically replaced, and completed reports are never
  overwritten.
- The exact V5 frozen 15-Episode sample, release hashes, and event catalog hash
  remain authoritative; V8/V9 files and results are untouched.

## Strict v10 checkpoint validation

`validate_canonical_conversation` re-checks the V8 message grammar and then, in
addition: every environment turn must be a delta-typed `environment_observation`
(fields exactly `{message_type, action_result, observation_delta}`); any
`current_observation` key is rejected as a full-snapshot violation; canonical
serialization is verified; and the delta chain is replayed from the initial
observation so every delta must apply exactly to the immediately preceding
observation.  `_validate_episode_row` additionally cross-checks prompt/event
selection, token rows, model output records, and public action records as in
V9.

## Verification scope

`tests/test_evaluate_workflow_v4_flash_v10.py` covers: delta/apply exact
roundtrips (nested dicts, lists, scalars, missing vs null, bool/int, list
length/type changes); strict apply rejection paths; a 4-action run asserting
the append-only message sequence (each request extends the previous verbatim),
>=3-action pairing, and no rebuilding; rejection of any post-initial full
snapshot; deltas being relative to the immediately preceding observation and
exactly reconstructing it; retention of all actions and action results in
order; no-thinking/no-raw-content retention; token semantics; V9 concurrency,
checkpoint, subset-order, failure, and metadata gates; V8/V9 regression
assertions; and the v10 system extension: the v6 document plus exactly one
`observation_delta_protocol` key whose instructions appear exactly once, no
private-data leakage, deterministic byte-identical system content across all
four calls, the `expected_episode_prompt` hash matching the v10 system content
(and differing from v6-only), the prompt-template hash binding the instruction
text, and history staying append-only and delta-only with the v10 system.
All 82 tests across the v8/v9/v10 suites pass.

## Known limitations

- Because full snapshots are deliberately not persisted, checkpoint validation
  proves that each stored delta *applies* to its reconstructed predecessor, not
  that it equals the canonical diff of the (unpersisted) true observations.
  Replace nodes' `before` values make most tampering detectable via chain
  breakage; a fabricated delta consistent with the whole chain is
  indistinguishable from genuine history by construction.
- List element insertion/removal mid-list is encoded as an exact `replace` of
  the whole list (lossless but not minimal); same-length element-wise edits
  recurse per index.
- A pre-existing v10 run directory (from the unexplained-prompt draft) is now
  refused: the changed system hash and migration manifest fail checkpoint
  recomputation; it must be moved aside before rerunning.
- The instruction text is enforced only at the prompt layer; whether the
  evaluated model actually applies the deltas correctly is a behavioral
  question outside this harness's guarantees.
