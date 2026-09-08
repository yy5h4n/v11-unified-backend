# D3 CityLearn multi-building shared-meter competition

This route is implemented by `d3_citylearn_multibuilding_adapter.py` and
probed by `probe_d3_citylearn_multibuilding.py`.  It uses CityLearn **2.5.0**
with two real ResStock LSTM buildings in one persistent `CityLearnEnv`, with
`central_agent=True`.  Each building exposes native electrical-storage and
cooling/heating actions.  A 14-step private LSTM warm-up is followed by a
24-step public episode; the evidence records exactly one native environment
instance per trajectory.

The generated evidence is:

`generated/d3_citylearn_multibuilding_evidence.json`

The evidence also pins `adapter_sha256`, `probe_sha256`, and public CityLearn
runtime provenance (version, tag commit, Python version, runtime file count,
and runtime SHA-256).  `--check` compares the complete deterministic JSON
payload; it does not ignore or normalize trajectory fields.

## Strong-coupling result

With the default 5.0 kWh shared-meter threshold, the real same-seed replay
reports:

- Building 0 battery intervention changes its native SOC by
  `0.9998374581336975`.
- The same intervention changes native district net electricity by
  `1.5585057735443115` kWh.
- Building 1's capacity-feasible headroom changes by
  `1.5585057735443115` kWh, while Building 1's native net trajectory remains
  unchanged (`0.0` maximum delta) under this intervention.
- The baseline native district peak is `6.6415625512599945` kWh.

Therefore the D3 gate passes: changing Building 0 changes the shared meter
headroom available to Building 1.  The route also records each building's
native net/SOC/temperature, native district sum, cumulative district peak,
shared headroom, capacity violation, and per-building feasible headroom.

## Authenticity boundary

`native_multi_building_dynamics=true` means that both buildings are created by
and transitioned by one native CityLearn environment.  `native_district_aggregation=true`
means district net is the sum of the native building net-electricity outputs.
CityLearn 2.5 does **not** provide a native transformer-capacity clipping
mechanism in this route.  Consequently the capacity field is explicitly
labelled `benchmark/shared-meter threshold`, `native_clipping=false`, and
actions are not silently clipped or rejected by the adapter.  The strong
coupling claim is the cross-system change in externally computed shared
headroom, not native electrical-network feedback between buildings;
`native_cross_building_physical_feedback=false` is recorded in the evidence.

## Verification

```text
../v10_diversity_aware_compiler/.runtime/venv/bin/python probe_d3_citylearn_multibuilding.py
../v10_diversity_aware_compiler/.runtime/venv/bin/python probe_d3_citylearn_multibuilding.py --check
```

The real probe passes deterministic replay, exact 24-step termination,
native energy-balance identities, action sensitivity, shared-headroom
intervention, and provenance checks.  The dedicated pytest module is
`tests/test_d3_citylearn_multibuilding.py`.
