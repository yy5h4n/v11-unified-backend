# Dual-coder unit annotation protocol

Read only `CODING_PACKET.jsonl` and `CODING_SCHEMA.json`. Produce exactly one
JSON object per input row, in input order, with no omitted or extra IDs.

Apply these conservative rules:

1. Code only what the supplied evidence text and authorship status support.
2. A requested trigger-action automation supports `delegation_acceptance =
   automation`, not `agent_control`, unless ongoing Agent discretion is explicit.
3. A mechanism or recurring event does not prove a standing responsibility.
4. `candidate_responsibility` must be device-independent. Use `null` when purpose,
   ownership or human outcome is not supportable.
5. `entails` requires explicit persistent/recurring maintenance, accountable
   ownership, beneficiary and meaningful failure. `strongly_implies` may miss at
   most one of those slots. Most isolated rules should be `compatible_only`.
6. Preserve refusals, ambiguity and conflicts. Do not repair missing information
   using common sense.
7. Do not inspect backend files, previous annotations, candidate catalogs or the
   other coder's output.

This is a surrogate dual-AI coding track. Its outputs cannot be labeled human
annotation or `human_validated`.
