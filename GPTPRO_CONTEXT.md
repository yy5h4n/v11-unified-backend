# V11 backend context for GPTPro

This file is the entry point for reviewing the backend as a whole. Read it
before reading individual adapters.

## Source of truth

The durable source tree is this directory. The accepted local runtime checkout
that produced the evidence was `/private/tmp/v11-production-repair/prototypes/v11_unified_process_compiler`;
that path is temporary and may be deleted by the operating system. The source
files in this directory were copied from its final Round 11 state. Native
simulator binaries, model packs, and generated campaign history are external
dependencies and are described in `RUNTIME_DEPENDENCIES.md`.

The final evidence package is under
`acceptance/backend_acceptance_round11_final/`. It is provenance evidence, not
a substitute for installing native runtimes on another host.

The repository is intentionally layered: current operating and acceptance
documents stay at the root, the final review material is under `docs/`, and
older prototype/audit material is preserved under `docs/archive/`. The archive
is historical context, not a second implementation.

## What passed

The local Astra acceptance passed the agreed Episode-generation gate for all 15
route records. The gate included full declared horizons, mechanism checks,
reset/close cycles, paired concurrency, queue work, active stability work,
source bindings, and negative evidence checks. The final review is in
`docs/ASTRA_ROUND11_FINAL_REVIEW.md`; the repair history and earlier defects
are in `docs/`.

## Capability boundaries that must remain visible

- CityLearn multi-system supports Episode generation, but its native
  cross-channel D3 coupling is not verified. It must remain excluded from
  verified-D3 coupling selections.
- FDS is a real full-history/prefix-replay backend. It does not provide native
  online lockstep stepping. Requests requiring native online stepping must be
  rejected; replay must never be described as online continuation.
- The accepted result is for the tested local macOS runtime set. A new host
  needs the pinned simulator runtimes and must rerun the read-only acceptance
  check.

## Architecture map

- `unified_compiler/agent_interface.py`: canonical public reset/observe/
  legal_actions/step/close boundary and receipt validation.
- `unified_compiler/route_registry.py`: authoritative route metadata,
  cadence, horizon, execution mode, and capability declarations.
- `unified_compiler/executor.py`: bounded queue, process isolation, deadlines,
  and owned worker cleanup.
- `unified_compiler/adapters/` and the top-level `d2_*`/`d3_*` adapters:
  native backend implementations.
- `tools/run_episode_campaign.py`: full-horizon, lifecycle, concurrency and
  causal campaign runner.
- `tools/backend_acceptance_runner.py`: fail-closed acceptance checker.
- `tests/`: protocol and regression tests.

## Reproduction

With the external runtimes mounted, run:

```sh
PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/bin/python \
  tools/backend_acceptance_runner.py --check \
  --output-dir acceptance/backend_acceptance_round11_final
```

Do not treat `READY_FOR_ASTRA` in an old copied package as proof for changed
source. Rebuild evidence after source changes and inspect the source hashes.

## Episode-generation boundary

The backend only supplies an environment, observations, legal actions, time,
and receipts. A released Episode still needs a frozen task contract:

1. freeze the human responsibility/query skeleton;
2. define `success_spec` and trajectory constraints independently of solver
   outcomes;
3. sample and reset a native scenario;
4. find a private feasible witness and replay it in the same backend;
5. test controlled violations and no-op/non-triviality gates;
6. publish only the query, initial public observation, success specification,
   hidden scenario reference, and provenance.

The witness action trace and hidden future state are private feasibility
evidence, not the Agent's gold answer.

## Frozen material

`docs/V11_PROJECT_AUDIT_2026-09-06.md` and the construction/conformance
documents are historical/frozen review material. Do not silently rewrite their
semantics or hashes when changing implementation. Record any new capability
as a dated supplement and rerun the acceptance package.
