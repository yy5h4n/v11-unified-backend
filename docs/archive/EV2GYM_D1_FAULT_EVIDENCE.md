# EV2Gym D1 charger-fault backend evidence

This is an independent backend-only adapter over the already verified
`ev2gym_claim.v1` route (`v10 EVRuntime` and its pinned official EV2Gym
checkout). It supports three fixed, agent-independent charger schedules:

The frozen v10 artifact pair is used only to provide the pinned native
EV2Gym configuration and source window for replay. It is not evidence that
this adapter selects or generates any new benchmark Episode.

- `derated`: charger output is capped at 50% of the native public maximum;
- `outage`: charger output is unavailable;
- `intermittent`: deterministic one-step unavailable/one-step available.

Windows are half-open (`[start_step, end_step)`) and are relative to the
native EV trajectory start. The wrapper retains the native public action
schema, uses the existing `charger_max_power_kw` state field, and records
requested/effective power plus native delivered charging and SoC fields. A
healthy replay with the identical requested action sequence is available from
`EV2GymFaultAdapter.healthy_fault_counterfactual`.

The native probe writes the machine-readable gate to
`generated/ev2gym_fault_replay_gate_v1.json`. The gate binds this adapter's
source hash and every schedule ID, and records deterministic replay and
healthy-vs-fault delivered-energy/SoC divergence. Missing native runtime or
invalid evidence remains `EVIDENCE_PENDING`; the adapter then returns no
processes.

Boundary: this file and the gate do not claim responsibility alignment,
benchmark validity, query/evaluator behavior, notification success, or any
other device physics.
