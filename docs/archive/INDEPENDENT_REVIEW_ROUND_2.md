# Independent Review Round 2

## Verdict

`MAJOR / DO NOT RELEASE`

The 30-responsibility, 300-Episode package is a candidate only. Its trusted
replay chain was executable before subsequent source edits, but independent
review found semantic and distinctness failures that invalidate formal release.

## Blocking findings

1. Several responsibility evaluators did not enforce all machine Contract
   clauses: notification recipients, continuous security guards, the fridge
   intervention delay, bathroom privacy, alarm timing/duplicates, shower
   release/content, and garage obstruction recovery.
2. `FULL` capability matching was not yet tied to evaluator-side primitive
   implementations and adversarial tests.
3. Process uniqueness used a full global config digest. Unrelated configuration
   changes could therefore make an Episode appear unique. After stripping
   receipt identifiers, many responsibility variants shared the same realized
   environment trajectory; fridge reference/no-op variants had only one each.
4. The policy evaluation entry point and legacy validator fixtures lagged the
   trusted-runtime refactor.
5. The generated package hashes no longer match the in-progress source fixes;
   the package must be rebuilt only after code stabilizes.

## Release condition

Rebuild only after all evaluator attack tests pass and every responsibility has
ten distinct, responsibility-relevant realized processes. Then run the complete
code-side validator and obtain a fresh independent semantic and package review.

Friday review MCP was unavailable in this session. This report records two
independent read-only agent audits and does not claim Friday GLM review.
