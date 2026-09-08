# V11 unified backend

This directory is the single source tree assembled from the final locally
accepted repair package. It contains the public backend boundary, route
adapters, bounded executor, acceptance tools, tests, protocol documents, and
the Round 11 acceptance evidence under `acceptance/`.

The root keeps only the documents needed for operating and reviewing the
backend. Historical research scripts and old pilot data were removed from the
repository; the current review material is under `docs/`.

The backend passed a fresh local Episode-generation acceptance for all 15
route records. The current evidence is under
`acceptance/backend_acceptance_local_final`. The following capability
boundaries are intentional:

- CityLearn multi-system can generate Episodes, but is not claimed as a
  verified native D3 cross-channel coupling backend.
- FDS uses full-history/prefix replay. Native online stepping is unsupported;
  requests that require it must be rejected.

Large simulator installations and runtime assets are external dependencies and
are not vendored in this repository. Use the pinned local runtimes described by
`TEST_COMMANDS.json` and the route metadata before running native campaigns.

Before every local campaign, run the fast fail-closed preflight:

```sh
PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/bin/python tools/local_preflight.py --strict
```

The read-only acceptance check for the current fresh package is:

```sh
PYTHONDONTWRITEBYTECODE=1 /opt/anaconda3/bin/python \
  tools/backend_acceptance_runner.py --check \
  --output-dir acceptance/backend_acceptance_local_final
```

For LLM multi-turn calls, use `unified_compiler.llm_conversation`. It keeps
the backend's full observations as the source of truth, sends the initial
observation once, and sends lossless V10 `observation_delta` messages on later
turns. It is a provider-neutral message formatter; it does not make model API
calls.
