# V4 Flash all-backend Agent pilot

Status: **PILOT_ONLY; execution finished**. This is an engineering pilot over hand-authored task contracts, not a formal benchmark, frozen membership certification, or semantic-equivalence certification.

Unique output: `generated/episode_pilot_v4_flash_v1/`.

Command:

```sh
PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/bin/python tools/run_v4_flash_all_backend_agent_pilot.py --output-dir generated/episode_pilot_v4_flash_v1 --workers 2
```

The runner first completed a D0 calibration through the existing AIGC gateway using model `deepseek-v4-flash-meituan`; the model returned the required `<answer>JSON_OBJECT</answer>` action and correctly turned interior lights off. The full run then executed one independently constructed task contract for each of the 15 canonical routes with two concurrent workers, one model call per route, up to two transport retries, 120-second request timeout and 512 output-token cap. No old workflow Episode IDs, old Harness evaluator or historical 15-question entry point was used.

Each private route record contains the predeclared query/contract, source bindings, seed, native witness, control/target-violation branch, same-seed witness replay, raw response hash/byte count, provider completion token count when available, Agent action/receipt, append-only delta reconstruction result and evaluator fields. The evaluator checks native witness/control completion, actual control contrast, replay agreement, accepted Agent action and monotone positive time. Public records contain only the task/query/initial Agent-facing receipt and Agent outcome metadata; witness/control/replay actions and private evaluator contract remain in `episodes_private.jsonl`.

Final observed result: 15 route records, 9 Agent task passes and 6 Agent action rejection failures. Failures are retained as valid experimental outcomes: SustainGym cooling sequence, CityLearn battery scalar, EnergyPlus IAQ, WNTR isolation valve, FDS scalar replay, and Modelica Buildings scalar were rejected by their real route action validators after the model emitted incompatible shapes/values. Backend witness/control/replay evidence exists for all 15 routes and same-seed replay agreed for all 15. The FDS record uses the registry's approved `prefix_replay` continuation and makes no online-step claim. CityLearn single-building is retained as Episode capability evidence, never as verified D3 coupling.

This pilot does not claim all tasks succeeded, formal benchmark quality, candidate membership, semantic query equivalence, or frozen construction-spec certification. Human review is still required for task semantics, thresholds, and whether each hand-authored engineering contract is an acceptable responsibility.
