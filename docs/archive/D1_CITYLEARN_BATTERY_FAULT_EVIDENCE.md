# D1 CityLearn battery health/fault backend evidence

This release is backend-only. It reuses the pinned CityLearn 2.5.0
`Battery.charge` route and the existing real annual load/PV source trace. The
health schedule is fixed before reset, half-open and agent-independent; future
windows are not included in the public observation. The probe compares the
same requested action sequence against a healthy run after resetting both
routes with the same source window.

Implemented mechanisms:

- `capacity_degradation`: changes the native battery `capacity` property at
  onset and retains the reduced capacity thereafter.
- `power_derating`: scales the requested normalized battery command before
  calling native `Battery.charge`.
- `unavailable`: sends zero command during the window.
- `stuck`: repeats the last effective physical command during the window.

Run the independent probe with the pinned v10 runtime:

```text
.../v10_diversity_aware_compiler/.runtime/venv/bin/python \
  probe_citylearn_battery_fault.py
```

The generated `generated/d1_citylearn_battery_fault_profiles_v1/*.json`
records provide deterministic replay, healthy-vs-fault SoC and net-electricity
divergence, exact termination, and CityLearn/assets/runtime provenance. The
typed adapter remains `DATA_PROBED_PENDING_REPLAY` and returns no process when
its matching evidence gate is absent or stale. No benchmark episode, query,
evaluator, responsibility label, or threshold is asserted by this route.
