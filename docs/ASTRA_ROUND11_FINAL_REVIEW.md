# Round 11 final independent acceptance

**PASS for the agreed local Episode-generation scope, with the explicit capability limits below.** This supersedes the earlier Round11 rejection and applies to the updated `generated/backend_acceptance_round11_final` package. The two exact remaining B2 findings are closed. No implementation or original-project file was modified during review.

## B2 capability boundary — closed

The registry, current route metadata, embedded campaign metadata and route checks now agree: CityLearn multi-system supports Episode generation, but `d3_coupling_supported=false`, `coupling_supported=false` and `mechanism_gate=false`. The route remains executable with `episode_ready=true`.

The active dynamic mechanism catalog now gives this route `mechanism_status=EVIDENCE_PENDING` and `strong_coupling.verified=false`, while retaining its separate valid Episode/replay evidence. Its builder implements the same distinction, and an independent `build_dynamic_mechanism_catalog.py --check` exits 0. The builder's verified-route selection consequently excludes CityLearn multi-system. The public protocol and delivery report disclose the limitation. Existing historical route IDs and aliases do not establish coupling capability.

This is the accepted honest capability separation, not proof that CityLearn has acquired native cross-channel physics. It must not be published or sampled as a verified D3 coupling route. Its battery/HVAC direct-response Episodes remain usable.

## B2 raw-prefix evidence validation — closed

The checker now compares the paired raw prefix observations instead of relying only on the producer's `same_prefix` flag. Independent checks against copies of the current package, with ordinary artifact hashes refreshed after each mutation, returned:

| Check | Exit code |
|---|---:|
| Unmodified final package | 0 |
| Remove all cross-channel raw runs, retain summary bits | 1 |
| Replace the second raw prefix with a different object, retain summary bits | 1 |
| Remove the second raw prefix field | 1 |

These resolve the exact missing-runs and unequal/missing-prefix cases from the prior reviews. This is bounded acceptance evidence, not a claim that the checker proves arbitrary fabricated trajectories authentic. The actual CityLearn and EV raw interventions were independently rerun and matched in Round10; this closure changes capability reporting and evidence checks, not those native effects.

## Retained accepted evidence and integrity

B1 EnergyPlus origin/first-step/terminal-window evidence, B3 per-seed authoritative horizon checks, B4 owned-process-group lifecycle cleanup, EV's native conditional shared-transformer constraint evidence and the measured stability workload retain their previously accepted status. Unaffected expensive tests were not repeated.

The reused stability stream remains the verified round8 measurement: 600.226 seconds of active work, 30 queued jobs, 2999 total raw events, zero errors and zero seed mismatches in that workload. It is not described as a newly executed all-route soak. Package manifest hashes and CHANGED_FILES hashes match; original baseline hashes remain unchanged. Independent final-check evidence is stored in `astra_round11_final_evidence`.

## Accepted delivery scope

- Local offline Episode generation across the 15 configured route windows and verified host runtimes, using the established bounded execution/lifecycle controls.
- CityLearn multi-system is available for Episodes but is **not a verified D3 cross-channel coupling source**. Coupling-required selection must exclude it.
- EV supports the measured conditional shared-transformer feasibility interpretation; this does not claim native clipping or a changed fixed-port output.
- FDS real prefix/full-history replay is formally accepted for the configured Episode horizon; it does not provide native online continuation, and explicit online requests remain rejected.
- Process cleanup acceptance concerns the owned process-group model and measured local workload. It does not establish cross-platform operation or containment of deliberately detached processes.

There are no remaining blocking defects in the agreed final-review scope. The package is ready for the root agent's final installation/delivery step, preserving these capability restrictions in the delivered documentation and machine artifacts.
