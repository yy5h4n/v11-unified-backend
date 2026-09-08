# V9 Episode Concurrency Audit

## Scope

V9 changes orchestration only. It imports V8's `evaluate_one`, so the JSON
envelope, full canonical action/observation history, token accounting,
evaluation, and strict within-Episode action/environment loop remain unchanged.
V1--V8, existing runs, and probe files are untouched.

The paper-facing token metric is `metrics_all.api_output_tokens`, exactly the
API completion/output count. Prompt tokens and the provider aggregate are
diagnostics only in `metrics_all.token_diagnostics`; no ambiguous generic
`total_tokens` report key is emitted.

## Concurrency contract

- `--workers` defaults to 30 and rejects non-positive values.
- Effective concurrency is `min(workers, remaining Episodes)`. The frozen
  15-Episode smoke set therefore uses at most 15 workers.
- Each worker creates a fresh `ChatClient`; V8 creates a fresh backend and
  policy inside `evaluate_one`. No mutable client/backend/policy is shared.
- Only Episodes are parallel. Calls within one Episode remain strictly serial.
- Only the main thread aggregates completed futures and atomically replaces the
  checkpoint.

## Ordering and recovery contract

- Futures may finish in any order, but every checkpoint and final report stores
  Episodes in frozen `SAMPLE_INDICES` order.
- A checkpoint may contain any completed subset, not only a prefix.
- Resume validates unique/known Episode IDs, canonical subset order, query,
  model, system hash, event selection, canonical conversation, public/model
  action agreement, token counters, privacy fields, metrics, and metadata.
- Duplicate, unknown, reordered, completed, or tampered checkpoints are
  rejected before new API work begins.
- A failed API future creates no pseudo-result. Other completed futures are
  retained atomically, `execution_finished` remains false, and the process
  exits non-zero. A later invocation retries only missing Episodes.

## Verification

The dedicated tests use fake clients/futures only; no real API call is made.
They cover actual parallelism, per-Episode ownership, out-of-order completion,
effective worker count, arbitrary-subset resume, partial failure preservation,
checkpoint refusal/tamper cases, and V8 semantic delegation. V8 regression is
run separately to ensure the imported Episode behavior remains unchanged.
