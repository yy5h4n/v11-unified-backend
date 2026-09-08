#!/usr/bin/env python3
"""Split the timed-out isolated pair batch 02 into three five-pair directories."""

import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parent / "kimi_isolated_pair_judgment_v2_2"
SOURCE = ROOT / "batch_02" / "INPUT.jsonl"


def main() -> None:
    lines = [line for line in SOURCE.read_text(encoding="utf-8").splitlines() if line]
    for index in range(3):
        directory = ROOT / f"batch_02_sub_{index}"
        directory.mkdir(exist_ok=True)
        subset = lines[index * 5:(index + 1) * 5]
        (directory / "INPUT.jsonl").write_text("\n".join(subset) + "\n", encoding="utf-8")
        shutil.copyfile(ROOT / "batch_02" / "SCHEMA.json", directory / "SCHEMA.json")
        shutil.copyfile(ROOT / "batch_02" / "PROTOCOL.md", directory / "PROTOCOL.md")
        output = directory / "OUTPUT.jsonl"
        if output.exists():
            output.unlink()
    print("created 3 isolated sub-batches covering 15 pairs")


if __name__ == "__main__":
    main()
