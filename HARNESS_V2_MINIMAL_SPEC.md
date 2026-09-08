# Harness V2 Minimal Runtime Specification

Status: authoritative runtime boundary. The earlier trust/receipt machinery is
an optional offline conformance experiment and is not part of the Agent loop.

## One responsibility

Harness runs exactly one policy on exactly one Episode and returns a sealed
trace. Its loop is:

```text
backend.reset(episode)
→ expose public observation to policy
→ policy chooses act | install_rule | cancel_rule | ask | wait
→ validate the complete choice and call `execute_atomic`
→ harness calls backend.advance() to the next decision point
→ repeat until the backend terminates
```

`advance` is never an Agent action. `wait` only means “make no mutation at
this decision point”; the Harness still owns time advancement.

## Public/private boundary

The policy receives only the Episode's public bootstrap, current public
observation, last public execution feedback, and the allowed action kinds.
Backend-private state is recorded in a separate evaluator trace and is never
placed in the policy view.

The backend exposes a state digest before and after each action. A rejected
action must leave that digest unchanged; otherwise the run terminates with
`backend_atomicity_violation`. Rejection does not advance simulator time and
the policy may retry within its decision budget.

Harness does not know the responsibility Contract, evaluator target, oracle,
no-op baseline, query variants, dataset admission threshold, or aggregate
benchmark score.

## Separate construction-time validation

`EpisodeValidationSuite` may call `run_one()` repeatedly with no-op, oracle,
query deletion/paraphrase/shuffle, or action-perturbed policies. It collects
evidence for the Episode compiler and admission gate. None of these variants
changes the semantics of a single Harness run.

Query variants retain the same `episode_id`, seed, backend scenario, and
exogenous realization. The diagnostic arm name exists only in validation
evidence, so changing language cannot silently select a different world.

## Reproducibility

An Episode carries its seed. A run records ordered public and private traces
and a canonical SHA-256 digest. Re-running the same Episode, backend build and
policy must reproduce the same trace. Code/package version pinning belongs to
release metadata, not to the Agent interaction protocol.
