# Open-corpus workflow pilot v0

This is a small executable test set derived from participant-authored rules in
`andrematt/trigger_action_rules`.  It contains seven Query families and 17
Episode variants.  Every family is bound to a `FULL` capability match in the
current Harness V2 workflow backend.

The pilot deliberately separates:

- public Episode inputs in `episodes_public.jsonl`;
- source provenance in `families_with_provenance.json`;
- hidden variants, oracle/no-op results, and evaluator details in
  `episodes_private_validation.jsonl`.

Build it with the pinned workflow Python environment:

```bash
PYTHONPATH=v11_unified_process_compiler /opt/anaconda3/bin/python \
  v11_unified_process_compiler/tools/build_open_corpus_pilot_v0.py
```

The source repository currently has no visible license file.  This artifact is
therefore marked `internal_executable_pilot`; resolve redistribution rights
before publishing copied source text.  The pilot claims archival human
grounding, not a new human revalidation study.

To inspect the generated data in a browser, serve the repository root and open
`open_corpus_pilot_v0/viewer.html`.  The viewer reads the generated JSON/JSONL
files directly and provides search, route filtering, provenance, trigger timing,
and oracle/no-op validation details.
