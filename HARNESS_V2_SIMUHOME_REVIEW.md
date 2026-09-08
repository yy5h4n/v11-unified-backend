# Harness V2 Real SimuHome Adapter Review

Status: **PASS** (2026-09-01)

## Reviewed boundary

- Harness Core runs one policy on one Episode and seals public/private traces.
- The SimuHome adapter owns reset, semantic thermal commands, rule execution,
  physical advancement, causal state digest, and transactional rollback.
- Baselines, Query interventions, evaluator logic, and Episode admission remain
  in `EpisodeValidationSuite` or later construction stages, not Harness Core.

## Blocking findings and resolution

1. Fire and release commands were incorrectly deduplicated as one batch. They
   are now validated as two separate transactions; duplicates inside either
   batch fail, while the same device may appear once in both batches.
2. A failed rule command or fast-forward could leave a partially changed world.
   One complete `advance` is now transactional and rebuilds the accepted
   history on any failure.
3. Harness now seals advance failures as `backend_advance_failed`, or as
   `backend_advance_atomicity_violation` when rollback changes the causal
   digest. Private errors include normalized phase and error code.
4. The digest now includes aggregator latent state, scheduler/workflow state,
   rules, accepted history, events, and applied commands in addition to the
   visible Home state.

## Evidence

- Harness V2 suite: `96 passed`.
- Existing relevant SimuHome regression suite: `12 passed`.
- Attack coverage includes invalid prevalidation, partial direct-action failure,
  fire failure, release failure, fast-forward failure, digest sensitivity, and
  deterministic rule fire/release replay.

Independent re-review found no remaining causal blocker and approved the real
adapter for formal Episode construction.
