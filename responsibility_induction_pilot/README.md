# Responsibility Induction Pilot

## Status

Method dry run only. This directory is not a benchmark release and does not yet
constitute human annotation. Its purpose is to test whether published user-needs
evidence and naturally authored automation evidence can be converted into an
auditable set of responsibility candidates without inspecting backend
capabilities.

## Workflow

```text
public source material
  -> corpus_cards.json
  -> evidence_units.jsonl
  -> blinded annotation A / annotation B
  -> agreement and disagreement report
  -> adjudicated candidate responsibilities
  -> construct and evidence gates
```

## Non-negotiable distinctions

- A rule states what an automation does; it does not uniquely state why.
- `compatible_only` routine evidence cannot semantically establish a
  responsibility.
- A standing responsibility must be distinguished from a one-shot task, goal,
  preference, capability, or implementation.
- Backend availability, simulator variables, and existing V11 responsibility
  labels are hidden from the coding task.
- AI dual coding in this pilot is a schema/debugging exercise. Formal benchmark
  annotation requires independently recruited human annotators and reported
  pre-adjudication agreement.

## Planned artifacts

- `corpus_cards.json`: source access, provenance, unit, licensing, and bias.
- `evidence_units.jsonl`: source-grounded spans/rules with no responsibility
  labels.
- `coding_schema.json`: the frozen fields and label definitions for the dry run.
- `annotation_a.jsonl`, `annotation_b.jsonl`: independent pilot coding.
- `agreement_report.json`: exact and field-level agreement before adjudication.
- `adjudication.jsonl`: merge/split/unknown decisions with evidence links.

## Completed first dry run

- 3 corpus cards and 12 source-grounded evidence units;
- two mutually blinded AI annotations for schema debugging;
- pre-adjudication agreement report;
- `PILOT_FINDINGS.md` and a corrected `coding_schema_v2.json`.

No standing responsibility was admitted. Formal human annotation and
cross-household evidence expansion remain required.

Batch 2 added eight participant-authored desired behaviors and four SmartSense
device sequences. The V2 dry run showed perfect agreement on their limited
standing-responsibility support but poor agreement on a forced flat
task/preference/goal label. `coding_schema_v3.json` therefore replaces that
label with orthogonal semantic, temporal, ownership, and completeness axes.
