# Luna V4 Flash all-backend Episode pilot delivery

This is the corrected local offline pilot package. It contains 15 frozen route contracts, native witness/baseline/targeted-counterexample/replay attempts, clause-level scoring, and transport/evaluator regressions. It is not a formal benchmark and contains no online model calls.

## Reproducible commands

```bash
cd /private/tmp/v11-production-repair/prototypes/v11_unified_process_compiler
python3 tools/test_v4_flash_clause_regression.py
python3 tools/test_v4_flash_persistence.py
/opt/anaconda3/bin/python tools/build_v4_flash_offline_contracts.py --routes d0_exogenous_context,d1_sustaingym_fault,d1_citylearn_battery_fault,d1_discrete_device_fault,energyplus_iaq,wntr_residential_water,fds_smoke_fire,modelica_buildings_aixlib,d3_citylearn_multi_system,d3_citylearn_multibuilding_competition,d3_wntr_water_competition,d3_modelica_shared_heat,d3_energyplus_shared_ventilation --resume --output-dir generated/episode_pilot_luna_final_v1
/private/tmp/v11-production-repair/prototypes/v10_diversity_aware_compiler/.runtime/venv/bin/python tools/build_v4_flash_offline_contracts.py --routes d1_ev2gym_fault,d3_ev2gym_electric_competition --resume --output-dir generated/episode_pilot_luna_final_v1
python3 tools/build_v4_flash_offline_contracts.py --aggregate-only --output-dir generated/episode_pilot_luna_final_v1
```

The final directory is `generated/episode_pilot_luna_final_v1/`. `task_contracts.json` and `task_contracts.md` are frozen before native execution. `offline_validation.json` records each route even when its dependency/runtime fails; no failure is converted to pass. Thirteen design gates pass; the summary count is derived from the JSON.

## Route status

All 15 routes now have native traces with independent witness, legal baseline, targeted counterexample, and same-seed replay records. Thirteen design gates pass. Two routes remain without a verified witness under the frozen clauses; they are retained as evidence, not converted into success:

| route | result |
|---|---|
| d0_exogenous_context | design gate passed |
| d1_sustaingym_fault | design gate passed with legal targeted violation |
| d1_citylearn_battery_fault | witness not found: fault-window SOC stayed flat; native fields are present and replay is recorded |
| d1_ev2gym_fault | design gate passed using the pinned EV v10 runtime |
| d1_discrete_device_fault | design gate passed using native load → start → empty actions |
| energyplus_iaq | design gate passed with legal targeted violation |
| wntr_residential_water | design gate passed: maintain-tank baseline is the witness and open-valve plan is the legal liquid-level violation |
| fds_smoke_fire | design gate passed with legal targeted violation under real prefix replay |
| modelica_buildings_aixlib | design gate passed |
| d3_citylearn_multi_system | witness not found: storage SOC stayed flat; this remains a single-building Episode capability, not D3 coupling |
| d3_citylearn_multibuilding_competition | design gate passed: rate 0.1 is feasible and legal rate 1.0 has negative shared headroom |
| d3_wntr_water_competition | design gate passed with native WNTR worker |
| d3_modelica_shared_heat | design gate passed |
| d3_ev2gym_electric_competition | design gate passed with the pinned EV v10 runtime |
| d3_energyplus_shared_ventilation | design gate passed |

Existing real calibration material under `generated/episode_pilot_kimi_v1/` is preserved as historical input and is not silently rebound as Luna final validation. The final native runs used `/opt/anaconda3/bin/python` for SustainGym, CityLearn, WNTR, washer, FDS, Modelica, and EnergyPlus routes, and `/private/tmp/v11-production-repair/prototypes/v10_diversity_aware_compiler/.runtime/venv/bin/python` for both EV routes. No dependency was installed.

## Correctness changes

The pilot evaluator now requires an explicitly captured `initial_observation` or `prefix_observation` for corresponding signed delta clauses. It never treats the first post-action trace frame as a hidden anchor. Contracts declare whether trajectory constraints include trace, initial, and prefix frames. Empty paths, empty token lists, boolean path tokens, unknown operators, ambiguous dotted paths, missing fields, boolean values under numeric operators, and non-finite values fail closed. The builder delegates scoring to this evaluator rather than retaining a weaker duplicate implementation.

The Agent bridge keeps the required `<answer>{"action": NATIVE_ACTION}</answer>` envelope while decoding scalar, list, and dict native values before `step()`. It publishes only neutral native action type/schema information, records parsed actions before native validation, preserves rejected actions, and emits lossless observation deltas for accepted actions. Usage is unavailable when the provider omits it.

No core backend, runtime, frozen construction specification, credentials, or historical generated directory was modified.
