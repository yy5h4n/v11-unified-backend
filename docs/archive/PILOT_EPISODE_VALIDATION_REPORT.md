# V11 Episode pilot validation

Status: **PILOT_ONLY**, not a formal benchmark or frozen dataset certification. The pilot reuses the existing public backend interface and route metadata in the passed backend copy. It does not infer task membership, semantic equivalence, or release eligibility from backend success.

Command:

```sh
PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/bin/python tools/run_episode_pilot_v1.py --output-dir generated/episode_pilot_v1
```

The pilot produced eight records: two D0/D1/D2/D3 examples, covering eight native route families. D3 selection uses Modelica shared heat, EnergyPlus shared ventilation, and WNTR competition; CityLearn multi-system is deliberately excluded because its native D3 coupling capability is unsupported/unproven. One additional D2 FDS record exercises the approved real prefix-replay mode and makes no online-continuation claim.

Each record fixes a query template, route and seed, captures the backend-produced pre-action `initial_observation`, runs a private witness and same-seed replay, and runs a no-op/control branch. Private evaluation uses terminal time, monotone trajectory and witness-versus-no-op divergence. Witness actions, hidden route metadata and success predicates are only in `episodes_private.jsonl`; public records contain query, initial observation, route, seed and execution mode. No public record contains the witness action or private success predicate.

All eight pilot tasks passed the connectivity predicates. Same-seed replay matched for every record. The pilot does not certify frozen candidate membership, query-to-responsibility equivalence, witness acceptability, or formal protocol conformance; those require human/specification review. The D3 CityLearn route remains excluded from verified coupling selection.
