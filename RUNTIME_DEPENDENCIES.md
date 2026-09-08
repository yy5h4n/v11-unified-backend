# Runtime dependencies

The source tree intentionally does not vendor simulator binaries, model packs,
or generated campaign history. Those files are large, platform-specific, and
must be installed or mounted separately on the machine that runs Episodes.
On this Mac, the accepted runtime mounts resolve to the durable user directory
under `/Users/shanyingyu/DRPIE/...`, while the V11 source remains in the
Documents checkout. They are not under `/private/tmp`.

The accepted local campaign used:

- `/opt/anaconda3/bin/python` for the standard routes;
- the pinned V10 virtualenv for EV2Gym routes;
- the locally installed EnergyPlus, FDS, WNTR, CityLearn, Modelica and
  SustainGym runtimes recorded in the route metadata and acceptance evidence.

Before a deployment is called ready, run
`tools/local_preflight.py --strict`, then run the full campaign commands in
`TEST_COMMANDS.json` when fresh evidence is required. The checked-in
`acceptance/backend_acceptance_local_final` package is the current local
provenance evidence; it is not a replacement for installing the native
runtimes on a new host.
