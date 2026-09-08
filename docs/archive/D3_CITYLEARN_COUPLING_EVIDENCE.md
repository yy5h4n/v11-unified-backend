# D3 CityLearn multi-system coupling

The D3 route is implemented by `d3_citylearn_coupling_adapter.py` and
executed by `probe_d3_citylearn_coupling.py`. Every reported trajectory uses
one native CityLearn 2.5.0 `CityLearnEnv` instance with both
`electrical_storage` and `cooling_or_heating_device` actions active. The
public episode has 24 hourly steps; the 14-step LSTM warm-up is private
initialization in the same environment.

The generated JSON evidence records the deterministic baseline, a battery
charge contrast, and an HVAC-off contrast, together with source trace/schema,
thermal model, weather/PV/battery catalog, asset-manifest, and CityLearn
runtime SHA-256 provenance:

`generated/d3_citylearn_coupling_evidence.json`

The native net-electricity identity is checked at every step:

`net = non_shiftable_load + cooling + heating + dhw + storage - PV`

The representative episode is admitted by the D3 environment safety/resource
evaluator (`evaluator_gate`): occupied-step comfort guard, peak grid import,
cumulative import/export resource limits, injected energy-price cost limit,
and battery SOC bounds. The gate is threshold-profile driven and records its
own evaluator SHA-256; it has no notification or language assessment. A
second native episode from a different source window is contrasted in
`exogenous_condition_contrast`; weather, occupancy, non-shiftable load, and
PV all differ, demonstrating that strategy conditions are not fixed constants.

The probe fails closed when assets/version/provenance are missing, the source
window is invalid, the native episode does not terminate exactly at 24 steps,
replay is nondeterministic, either action channel is insensitive, an evaluator
threshold is malformed/violated, an exogenous condition does not change, or
the coupling identity exceeds `1e-5 kWh`.

Verified commands:

```text
python -m pytest -q tests/test_d3_citylearn_coupling.py
9 passed
python probe_d3_citylearn_coupling.py --check
```

Running bare `python -m pytest -q` from this directory is not a valid suite
boundary: pytest recursively collects vendored EnergyPlus/SustainGym package
tests built for incompatible Python/platform binaries. Those collection
errors are unrelated to D3; the D3-only suite passes.
