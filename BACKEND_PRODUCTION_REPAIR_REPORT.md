# V11 backend production repair report

Status: **READY_FOR_ASTRA for reliable Episode generation; CityLearn D3 coupling unsupported/unproven** (Round11 package passes the read-only checker).

Historical round-five package status was BLOCKED; the final round-seven package supersedes it.

Target supplement: reliable Episode generation using truthful native execution
modes. FDS full-history/prefix replay is an approved formal Episode mode; the
native-online capability remains false and explicit online requests are
rejected. See `FDS_REPLAY_TARGET_SUPPLEMENT.md`; frozen construction and
historical conformance text was not changed.

The shared public boundary now has canonical 15-route metadata, strict constructor and action validation, finite timing checks, native clock-origin preservation, monotone receipt validation, poisoned failure semantics, close invalidation, deep-copied whitelisted observations, and private workflow feedback handling. The executor enforces running-worker and pending-queue limits with absolute queued deadlines and owned process-group cleanup. The acceptance runner records per-route logs, source bindings, seed/horizon provenance, and rejects incomplete packages rather than accepting summary booleans without route artifacts.

Fresh native probes and catalogs were rebuilt after the source freeze. EV2Gym now uses its public SET_CHARGE_POWER action shape; SustainGym uses the verified 27-element legal vector; the generated Modelica D2 FMU was rebuilt and its actual XML GUID is used by the FMI binding. Direct fresh lifecycle probes for those repaired routes pass. The final route evidence package contains all 15 route records with 15/15 lifecycle and mechanism gates passing in the round-eight source-freeze package.

FDS is truthfully separated from interactive capability and is accepted under the approved replay target. The pinned FDS-6.11.1 route records full_history_real_backend_replay_not_online and online_step_supported=false, while the fresh campaign verifies three-seed 4-second replay histories, same-prefix divergence, deterministic clocks, and isolated process execution. Explicit synchronous-online requests remain rejected; interactive conformance remains false by design.

The round-eight source-freeze campaign contains all 15 routes with three seeds, full supported horizons, ten resource-recorded reset/close cycles, two concurrent episodes against serial controls, and the preserved measured 600-second active workload plus 30 mixed queued jobs. Route-specific native effect gates pass, including CityLearn effect traces and EV connected-demand transformer constraints. The assembled package is READY_FOR_ASTRA and the read-only checker exits 0. No formal Episodes or frozen semantic/conformance specifications were changed.

Reproduce from the work copy:

```sh
PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/bin/python tools/backend_acceptance_runner.py --all --output-dir generated/backend_acceptance_round8_final --timeout 180 --loops 10
PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/bin/python tools/backend_acceptance_runner.py --check --output-dir generated/backend_acceptance_round8_final
/opt/anaconda3/bin/python probe_agent_interface_d0_d1.py --output generated/agent_interface_d0_d1_v1.json
/opt/anaconda3/bin/python probe_d2_closed_loop.py --route all
/opt/anaconda3/bin/python probe_d2_modelica_buildings_aixlib.py
/opt/anaconda3/bin/python probe_d3_agent_interface.py
/opt/anaconda3/bin/python probe_d2_fds.py
/opt/anaconda3/bin/python build_dynamic_mechanism_catalog.py --output generated/dynamic_mechanism_catalog_v1.json
/opt/anaconda3/bin/python build_claim_backend_catalog.py
```

## Round-seven implementation status

Round-seven changes add execution-time seed/result binding, byte-correct resource samples, current FD/thread/owned-child samples, parsed event and paired-trace validation, and quarantine-preserving executor close failures. D3 EnergyPlus now measures elapsed time from its first native callback, so a native reset at 900 seconds is reported as a 900-second first delta and a 20,700-second elapsed 23-step window. D3 EV2Gym campaign dispatch must use the pinned v10 interpreter; its legal action range is [0,1].

The round-seven package remains blocked until the final source-freeze campaign is complete. The measured round-six stability stream remains preserved and is not rebound to new source hashes. FDS remains approved prefix replay with online_step_supported=false. Current mechanism gaps are D3 CityLearn cross-channel effect evidence and the route-specific D3 EV connected-demand causal run.

## Round-seven final delivery

Final source-freeze package: `generated/backend_acceptance_round7_final`. The package contains 15/15 passed route records, three seed runs, ten close cycles, paired native controls, full declared supported horizons, route-specific mechanism evidence, and the final stability measurement. The read-only checker exits 0.

D3 CityLearn causal evidence now retains native effects. A battery branch changed SOC from 0 to 0.971160, storage electricity from 0 to 1.045018 kWh, and net electricity accordingly. D3 EV2Gym uses the pinned v10 interpreter and a connected-demand prefix at native t=24300s; legal charger action 0 versus 1 produced native port power 0 versus 11 kW and transformer remaining capacity 15 versus 4 kW. D3 EnergyPlus public elapsed time is measured from the first native callback, with a 900-second first delta and 20,700-second supported elapsed window.

The final stability stream records 600.245869 seconds of active work, 30 queue jobs, 0 errors, and zero seed attribution mismatches. macOS process enumeration can deny descendant listing; such samples are recorded as null and are not interpreted as zero residuals. RSS, FD, and thread samples remain measured. FDS remains formally accepted as prefix replay with online_step_supported=false.

## Round-eight final correction

A final source-freeze campaign and stability run produced `generated/backend_acceptance_round8_final`. D3 EnergyPlus now returns reset elapsed 0 through the normalizer, first public transition 900 seconds, and the 23-transition native window ends at elapsed 20700 seconds; its direct adapter lifecycle contract remains unchanged. CityLearn and EV mechanism records retain direct effect measurements while the causal gate uses fixed-channel counterfactual traces: CityLearn battery intervention records SOC/storage/net physical effects, and EV connected-demand intervention records native port power and transformer feasible capacity on the connected peer. The checker recomputes every seed trace's action/observation digests, step count, cadence, terminal reason, and final horizon. Per-cycle resource before/after samples and cleanup fields are required. Final status is READY_FOR_ASTRA with the read-only checker exiting 0; a malformed t0 seed negative package exits 1.

## Round-nine final lifecycle evidence

The final package is `generated/backend_acceptance_round9_final`. All fifteen route campaigns were rerun after the lifecycle changes. Each route's ten cycles now execute in a bounded owned worker process group and record RSS/thread samples before and after, a session directory, close status, and process-group disappearance verification with `owned_children_remaining=0`. The package checker requires these fields and rejects null or failed cleanup. The final route matrix is 15/15 passed and D3 EnergyPlus records first elapsed 900s and final elapsed 20700s over 23 transitions. The malformed one-step full-horizon negative package exits 1.

## Round-nine B2/B3 closure

The current package is `generated/backend_acceptance_round10_final` and its checker exits 0. CityLearn D3 now records two same-prefix native interventions: battery varied with HVAC fixed and HVAC varied with battery fixed. Each retains raw native effects and explicitly records that this adapter has no native cross-channel clipping; the gate is limited to the independently measured channel responses and does not claim nonexistent coupling. EV2Gym D3 now uses seed 3 at a native prefix where both ports are connected, runs both fixed-peer counterfactual directions, and retains native action-mask, port-power, transformer loading, and remaining-capacity fields. Both probes diverge from the identical prefix.

The checker binds every seed's cadence and declared horizon to `route_metadata()` and requires the terminal trace endpoint, rather than trusting seed-local declarations. A valid one-step D0 mutation with local horizon 60 seconds and refreshed artifact hashes exits 1; the production package exits 0. B1 timing and B4 owned-group cleanup evidence is preserved from the accepted Round9 package because those implementations were unchanged.


## Round 11 CityLearn capability boundary

The current package is `generated/backend_acceptance_round11_final`. CityLearn D3 remains available for reliable Episode generation, but its native battery and HVAC interventions are independent in this configured model. The route metadata therefore declares `d3_coupling_supported=false`; its coupling/mechanism gate is false by design, while `episode_ready=true`. The package does not claim native cross-channel coupling or clipping. EV retains the accepted conditional shared-transformer feasible-domain evidence.

The checker requires two raw runs per declared D3 intervention, matching prefix observations, fixed-peer action equality, varied action inequality, and non-null native effects. Removing CityLearn probe runs while retaining all summary booleans and refreshing artifact hashes exits 1.

## Round 11 final B2 closure

The active mechanism catalog and its builder now classify `citylearn_multi_system` as `episode_generation_supported=true`, `d3_coupling_supported=false`, `mechanism_status=EVIDENCE_PENDING`, and `strong_coupling.verified=false`. Downstream verified D3 coupling selection therefore excludes it; the historical route ID and frozen evidence remain unchanged. The public protocol documents this boundary.

The final package is `generated/backend_acceptance_round11_final`. Its checker compares the two raw prefix observations and timestamps in each fixed-peer probe, verifies fixed and varied action fields and non-null native effects, and still rejects both empty-run and unequal-prefix mutations after refreshed hashes. The original package exits 0.
