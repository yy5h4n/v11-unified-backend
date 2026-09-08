# V11 unified backend

This directory is the single source tree assembled from the final locally
accepted repair package. It contains the public backend boundary, route
adapters, bounded executor, acceptance tools, tests, protocol documents, and
the Round 11 acceptance evidence under `acceptance/`.

The root keeps only the documents needed for operating and reviewing the
backend. Earlier prototype audits, Harness iterations, pilot reports, and
route notes are preserved under `docs/archive/`; dashboards and old catalog
artifacts are under `docs/artifacts/`.

The backend passed the agreed local Episode-generation acceptance for all 15
route records. The following capability boundaries are intentional:

- CityLearn multi-system can generate Episodes, but is not claimed as a
  verified native D3 cross-channel coupling backend.
- FDS uses full-history/prefix replay. Native online stepping is unsupported;
  requests that require it must be rejected.

Large simulator installations and runtime assets are external dependencies and
are not vendored in this repository. Use the pinned local runtimes described by
`TEST_COMMANDS.json` and the route metadata before running native campaigns.

The read-only acceptance check is:

```sh
PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/bin/python \
  tools/backend_acceptance_runner.py --check \
  --output-dir acceptance/backend_acceptance_round11_final
```
