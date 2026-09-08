# Harness V2 Final Independent Review

## Verdict

**PASS — no blocking MAJOR findings.**

The final review was read-only and explicitly scoped to research validity,
information boundaries, reproducibility, and responsibility separation. It did
not require cryptographic registry or receipt machinery inside Harness Core.

## Evidence reviewed

- Responsibility capability Contracts and backend mapping both pass their
  independent `--check` modes.
- The mapping preserves all 129 responsibility identities and reports
  `FULL=2`, `PARTIAL=20`, and `UNSUPPORTED=107`.
- Matching uses the structured seven-dimensional capability Contract plus
  backend readiness. The matcher does not parse Query text and contains no
  responsibility-ID allow-list.
- The reviewed Query artifact covers both FULL responsibilities with eight
  variants, seven unique opening bigrams, and zero validator errors. Query DTOs
  carry only the public context required to resolve room and named-period
  references.
- The new batch contains ten public records, ten private records, and ten gate
  records: three kitchen Episodes and seven multi-room Episodes.
- All ten Episodes pass construction-time no-op/oracle gap, Query deletion and
  cross-responsibility shuffle, action and trajectory sensitivity, hard
  feasibility, paraphrase equivalence, full thermal-room visibility, and
  deterministic replicate gates.
- Each Episode has nine completed validation runs, and every replicate trace
  digest matches its first run.
- Agent bootstrap data contains no responsibility ID, evaluator, oracle,
  released gold action, or Query ID. The multi-room `target_room_ids` context is
  required to resolve the user's demonstrative room reference and is therefore
  public task information rather than evaluator leakage.
- Harness Core remains one-policy × one-Episode execution. Baselines, Query
  mutation, scoring, comparisons, and admission remain in
  `EpisodeValidationSuite` and the dataset builder.
- A clean temporary-directory rebuild reproduced `episodes_public.jsonl`,
  `episodes_private.jsonl`, `validation_gate.json`, `build_report.json`, and
  `DATASET_CARD.md` byte for byte.

## Regression evidence

- Harness V2 tests: **103 passed**.
- Backend mapping, Query, and SimuHome responsibility tests: **28 passed**.
- Fresh batch build: **10/10 passed**, with no validation failures.

## Claim boundary

This is a provisional synthetic SimuHome batch that reruns ten previously
certified physical windows through Harness V2. It is not ten newly mined
physical processes, not a calibrated-building result, and not yet a formal
benchmark release.

## Non-blocking notes

- Reproduction uses the recorded environment Python 3.11.15, Pydantic 2.12.5,
  and rfc8785 0.1.4. A bare system Python without those dependencies is not the
  declared build environment.
- Mapping rows retain `episode_release_status=NOT_COMPILED` because mapping is a
  pre-Episode stage. This is semantically correct but can be renamed in a future
  schema revision if readers find it ambiguous.
