#!/usr/bin/env python3
"""Validate and summarize an evidence-grounded construction batch."""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from query_construction.evidence_batch import load_and_validate


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    _, audit = load_and_validate(args.batch)
    payload = json.dumps(audit, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")


if __name__ == "__main__":
    main()
