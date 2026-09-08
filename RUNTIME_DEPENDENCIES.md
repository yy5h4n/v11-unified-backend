# Runtime dependencies

The source tree intentionally does not vendor simulator binaries, model packs,
or generated campaign history. Those files are large, platform-specific, and
must be installed or mounted separately on the machine that runs Episodes.

The accepted local campaign used:

- `/opt/anaconda3/bin/python` for the standard routes;
- the pinned V10 virtualenv for EV2Gym routes;
- the locally installed EnergyPlus, FDS, WNTR, CityLearn, Modelica and
  SustainGym runtimes recorded in the route metadata and acceptance evidence.

Before a deployment is called ready, configure the external runtime paths and
run the read-only acceptance check against a freshly generated evidence
directory. The checked-in `acceptance/backend_acceptance_round11_final`
package is provenance evidence; it is not a replacement for installing the
native runtimes on a new host.

