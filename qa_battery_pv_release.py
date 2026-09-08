#!/usr/bin/env python3
"""Deterministic release QA for the battery/PV responsibility-episode release."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Callable, Iterator

ROOT = Path(__file__).resolve().parent
DEFAULT_RELEASE_DIR = ROOT / "generated" / "battery_pv_release"
DEFAULT_PROCESS_POOL = ROOT / "generated" / "battery_pv_process_pool.json"
DEFAULT_REPLAY_GATE = ROOT / "generated" / "battery_pv_process_replay_gate.json"

EXPECTED_HORIZON_STEPS = 24
REQUIRED_OBSERVATION_FIELDS = frozenset(
    {
        "virtual_hour",
        "battery_soc",
        "non_shiftable_load_kwh",
        "solar_generation_kwh",
        "net_electricity_without_storage_kwh",
    }
)
HIDDEN_PUBLIC_KEYS = frozenset(
    {
        "process_id",
        "contract_id",
        "building_id",
        "responsibility_contract",
        "backend_binding",
        "selection_lineage",
        "clauses",
        "derived_variables",
        "selection_metrics",
        "source_start_row",
        "source_end_row",
        "source_trace_sha256",
        "replay_gate_trajectory_digests",
    }
)
SECRET_KEY_PATTERN = re.compile(
    r"(secret|password|passwd|api[_-]?key|access[_-]?key|private[_-]?key"
    r"|credential|bearer|sshpass|ssh[_-]?key|token|auth(?:orization)?"
    r"|cookie|session)",
    re.IGNORECASE,
)
ABSOLUTE_PATH_PATTERN = re.compile(r"^(/[^/]|[A-Za-z]:[\\/]|\\\\|~[/\\])")
HIDDEN_PUBLIC_KEY_PATTERN = re.compile(r"(contract|backend|source|gold)", re.IGNORECASE)

CHECK_NAMES = (
    "episode_id_pairing",
    "unique_ids",
    "primary_contract_per_process",
    "horizon_steps",
    "action_bounds",
    "public_observation_fields",
    "public_hidden_keys",
    "no_paths_or_secrets",
    "replay_gate_passed",
    "process_pool_coverage",
    "building_single_split",
    "one_process_per_building_season",
    "report_counts",
    "no_gold_actions",
)

Fail = Callable[[str, str, str], None]


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} is not a JSON object")
        records.append(value)
    return records


def walk_entries(node: Any, path: str = "") -> Iterator[tuple[str, str, Any]]:
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            yield child, str(key), value
            yield from walk_entries(value, child)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from walk_entries(item, f"{path}[{index}]")


def limited(values: list[Any], keep: int = 5) -> str:
    shown = values[:keep]
    suffix = f" (+{len(values) - keep} more)" if len(values) > keep else ""
    return ", ".join(str(item) for item in shown) + suffix


def check_episode_id_pairing(
    fail: Fail,
    public_records: list[dict[str, Any]],
    private_records: list[dict[str, Any]],
) -> None:
    public_ids = [record.get("episode_id") for record in public_records]
    private_ids = [record.get("episode_id") for record in private_records]
    if len(public_ids) != len(private_ids):
        fail(
            "episode_id_pairing",
            "release",
            f"public episode count {len(public_ids)} != private episode count {len(private_ids)}",
        )
    only_public = sorted(set(public_ids) - set(private_ids))
    only_private = sorted(set(private_ids) - set(public_ids))
    if only_public:
        fail("episode_id_pairing", "episodes_public.jsonl", f"ids missing privately: {limited(only_public)}")
    if only_private:
        fail("episode_id_pairing", "episodes_private.jsonl", f"ids missing publicly: {limited(only_private)}")
    public_by_id = {record.get("episode_id"): record for record in public_records}
    for record in private_records:
        paired = public_by_id.get(record.get("episode_id"))
        if paired is None:
            continue
        if paired.get("split") != record.get("split"):
            fail(
                "episode_id_pairing",
                str(record.get("episode_id")),
                f"public split {paired.get('split')!r} != private split {record.get('split')!r}",
            )


def check_unique_ids(
    fail: Fail,
    public_records: list[dict[str, Any]],
    private_records: list[dict[str, Any]],
    process_pool: dict[str, Any] | None = None,
    replay_gate: dict[str, Any] | None = None,
) -> None:
    id_groups = {
        "public episode_id": [record.get("episode_id") for record in public_records],
        "private episode_id": [record.get("episode_id") for record in private_records],
        "contract_id": [record.get("contract_id") for record in private_records],
        "process_id": [record.get("process_id") for record in private_records],
    }
    for label, ids in id_groups.items():
        duplicates = sorted(str(item) for item, count in Counter(ids).items() if count > 1)
        if duplicates:
            fail("unique_ids", label, f"duplicated ids: {limited(duplicates)}")
    for label, payload in (("process_pool", process_pool), ("replay_gate", replay_gate)):
        if payload is None:
            continue
        ids = [item.get("process_id") for item in payload.get("processes", [])]
        duplicates = sorted(str(item) for item, count in Counter(ids).items() if count > 1)
        if duplicates:
            fail("unique_ids", label, f"duplicated process ids: {limited(duplicates)}")


def check_primary_contract_per_process(
    fail: Fail,
    private_records: list[dict[str, Any]],
    build_report: dict[str, Any],
) -> None:
    roles_by_process: dict[Any, list[Any]] = defaultdict(list)
    for record in private_records:
        contract = record.get("responsibility_contract") or {}
        roles_by_process[record.get("process_id")].append(contract.get("role"))
    for process_id in sorted(roles_by_process, key=str):
        primary_count = sum(role == "PRIMARY" for role in roles_by_process[process_id])
        if primary_count != 1:
            fail(
                "primary_contract_per_process",
                str(process_id),
                f"expected exactly one PRIMARY contract, found {primary_count}",
            )
    released_primary = sum(
        (record.get("responsibility_contract") or {}).get("role") == "PRIMARY"
        for record in private_records
    )
    if build_report.get("primary_contract_count") != released_primary:
        fail(
            "primary_contract_per_process",
            "build_report.json",
            f"primary_contract_count {build_report.get('primary_contract_count')!r} "
            f"!= released PRIMARY contracts {released_primary}",
        )


def check_horizon_steps(fail: Fail, public_records: list[dict[str, Any]]) -> None:
    for record in public_records:
        if record.get("horizon_steps") != EXPECTED_HORIZON_STEPS:
            fail(
                "horizon_steps",
                str(record.get("episode_id")),
                f"horizon_steps {record.get('horizon_steps')!r} != {EXPECTED_HORIZON_STEPS}",
            )


def check_action_bounds(fail: Fail, public_records: list[dict[str, Any]]) -> None:
    for record in public_records:
        action = (record.get("allowed_actions") or {}).get("battery_rate") or {}
        problems = []
        if action.get("type") != "continuous":
            problems.append(f"type {action.get('type')!r} != 'continuous'")
        if action.get("minimum") != -1.0:
            problems.append(f"minimum {action.get('minimum')!r} != -1.0")
        if action.get("maximum") != 1.0:
            problems.append(f"maximum {action.get('maximum')!r} != 1.0")
        if problems:
            fail(
                "action_bounds",
                str(record.get("episode_id")),
                "; ".join(problems),
            )


def check_public_observation_fields(fail: Fail, public_records: list[dict[str, Any]]) -> None:
    for record in public_records:
        observation = record.get("initial_observation") or {}
        missing = sorted(REQUIRED_OBSERVATION_FIELDS - set(observation))
        if missing:
            fail(
                "public_observation_fields",
                str(record.get("episode_id")),
                f"initial_observation missing fields: {', '.join(missing)}",
            )
        schema = record.get("observation_schema") or {}
        # The schema names the post-action net exchange differently from the
        # initial-observation field, so check its own required vocabulary.
        schema_required = set(REQUIRED_OBSERVATION_FIELDS) - {"net_electricity_without_storage_kwh"}
        schema_required.add("net_electricity_kwh")
        schema_missing = sorted(schema_required - set(schema))
        if schema_missing:
            fail(
                "public_observation_fields",
                str(record.get("episode_id")),
                f"observation_schema missing fields: {', '.join(schema_missing)}",
            )


def check_public_hidden_keys(fail: Fail, public_records: list[dict[str, Any]]) -> None:
    for record in public_records:
        episode_id = str(record.get("episode_id"))
        for path, key, _value in walk_entries(record):
            if key in HIDDEN_PUBLIC_KEYS or HIDDEN_PUBLIC_KEY_PATTERN.search(key):
                fail(
                    "public_hidden_keys",
                    episode_id,
                    f"hidden key {path!r} leaked into the public record",
                )


def check_no_paths_or_secrets(
    fail: Fail,
    public_records: list[dict[str, Any]],
    private_records: list[dict[str, Any]],
) -> None:
    labelled = [("public", record) for record in public_records]
    labelled += [("private", record) for record in private_records]
    for label, record in labelled:
        episode_id = str(record.get("episode_id"))
        for path, key, value in walk_entries(record):
            if SECRET_KEY_PATTERN.search(key):
                fail(
                    "no_paths_or_secrets",
                    episode_id,
                    f"{label} record carries secret-like field {path!r}",
                )
            if isinstance(value, str) and is_absolute_filesystem_path(value):
                fail(
                    "no_paths_or_secrets",
                    episode_id,
                    f"{label} record field {path!r} holds an absolute filesystem path",
                )


def is_absolute_filesystem_path(value: str) -> bool:
    """Recognize POSIX, Windows, UNC, and home-relative absolute paths."""
    return bool(ABSOLUTE_PATH_PATTERN.search(value))


def check_replay_gate_passed(
    fail: Fail,
    replay_gate: dict[str, Any],
    process_pool: dict[str, Any],
    private_records: list[dict[str, Any]],
) -> None:
    if replay_gate.get("process_pool_digest") != process_pool.get("process_pool_digest"):
        fail(
            "replay_gate_passed",
            "replay gate",
            "replay gate process_pool_digest does not match the process pool",
        )
    if replay_gate.get("all_passed") is not True:
        fail("replay_gate_passed", "replay gate", "all_passed is not true")
    if replay_gate.get("failed_count", 0) != 0:
        fail(
            "replay_gate_passed",
            "replay gate",
            f"failed_count {replay_gate.get('failed_count')!r} != 0",
        )
    pool_ids = {item.get("process_id") for item in process_pool.get("processes", [])}
    gate_items = replay_gate.get("processes", [])
    gate_by_id = {item.get("process_id"): item for item in gate_items}
    gate_ids = set(gate_by_id)
    if gate_ids != pool_ids:
        fail(
            "replay_gate_passed",
            "replay gate",
            "gate process ids do not exactly match process pool "
            f"(missing={limited(sorted(pool_ids - gate_ids))}, "
            f"extra={limited(sorted(gate_ids - pool_ids))})",
        )
    if replay_gate.get("process_count") != len(gate_items):
        fail("replay_gate_passed", "replay gate", "process_count does not match gate entries")
    for item in gate_items:
        if item.get("passed") is not True:
            fail("replay_gate_passed", str(item.get("process_id")), "process did not pass the replay gate")
    for record in private_records:
        process_id = record.get("process_id")
        if process_id not in gate_by_id:
            fail(
                "replay_gate_passed",
                str(process_id),
                "released process has no replay gate entry",
            )


def check_process_pool_coverage(
    fail: Fail,
    process_pool: dict[str, Any],
    private_records: list[dict[str, Any]],
) -> None:
    pool_ids = {process.get("process_id") for process in process_pool.get("processes", [])}
    released_ids = {record.get("process_id") for record in private_records}
    missing = sorted(pool_ids - released_ids)
    extra = sorted(released_ids - pool_ids)
    if missing:
        fail("process_pool_coverage", "process pool", f"pool processes not released: {limited(missing)}")
    if extra:
        fail("process_pool_coverage", "episodes_private.jsonl", f"released ids outside the pool: {limited(extra)}")


def check_building_single_split(
    fail: Fail,
    public_records: list[dict[str, Any]],
    private_records: list[dict[str, Any]],
    build_report: dict[str, Any],
) -> None:
    public_split_by_id = {record.get("episode_id"): record.get("split") for record in public_records}
    splits_by_building: dict[Any, set] = defaultdict(set)
    for record in private_records:
        building_id = (record.get("backend_binding") or {}).get("building_id")
        splits_by_building[building_id].add(record.get("split"))
        paired = public_split_by_id.get(record.get("episode_id"))
        if paired is not None:
            splits_by_building[building_id].add(paired)
    leaked = False
    for building_id in sorted(splits_by_building, key=str):
        splits = sorted(str(split) for split in splits_by_building[building_id])
        if len(splits) > 1:
            leaked = True
            fail(
                "building_single_split",
                str(building_id),
                f"building appears in multiple splits: {', '.join(splits)}",
            )
    if "building_leakage" in build_report and build_report["building_leakage"] != leaked:
        fail(
            "building_single_split",
            "build_report.json",
            f"building_leakage {build_report['building_leakage']!r} != computed leakage {leaked}",
        )


def check_one_process_per_building_season(
    fail: Fail,
    process_pool: dict[str, Any],
    private_records: list[dict[str, Any]] | None = None,
) -> None:
    counts: Counter[tuple[Any, Any]] = Counter()
    for process in process_pool.get("processes", []):
        counts[(process.get("building_id"), process.get("season_index"))] += 1
    for (building_id, season_index), count in sorted(counts.items(), key=lambda item: (str(item[0][0]), str(item[0][1]))):
        if count != 1:
            fail(
                "one_process_per_building_season",
                f"{building_id} season {season_index}",
                f"expected one selected process, found {count}",
            )
    if private_records is not None:
        released_counts: Counter[tuple[Any, Any]] = Counter()
        for record in private_records:
            binding = record.get("backend_binding") or {}
            lineage = record.get("selection_lineage") or {}
            released_counts[(binding.get("building_id"), lineage.get("season_index"))] += 1
        for (building_id, season_index), count in sorted(
            released_counts.items(), key=lambda item: (str(item[0][0]), str(item[0][1]))
        ):
            if count != 1:
                fail(
                    "one_process_per_building_season",
                    f"{building_id} season {season_index}",
                    f"release contains {count} selected processes",
                )
        expected = {
            (process.get("building_id"), process.get("season_index"))
            for process in process_pool.get("processes", [])
        }
        released = set(released_counts)
        if expected != released:
            fail(
                "one_process_per_building_season",
                "release",
                "release building-season keys differ from process pool "
                f"(missing={limited(sorted(expected - released))}, "
                f"extra={limited(sorted(released - expected))})",
            )


def check_report_counts(
    fail: Fail,
    public_records: list[dict[str, Any]],
    private_records: list[dict[str, Any]],
    build_report: dict[str, Any],
    replay_gate: dict[str, Any],
) -> None:
    family_counts = Counter(record.get("family_id") for record in public_records)
    split_counts = Counter(record.get("split") for record in public_records)
    if build_report.get("family_counts") != dict(family_counts):
        fail(
            "report_counts",
            "build_report.json",
            f"family_counts {build_report.get('family_counts')!r} != computed {dict(family_counts)!r}",
        )
    if build_report.get("split_counts") != dict(split_counts):
        fail(
            "report_counts",
            "build_report.json",
            f"split_counts {build_report.get('split_counts')!r} != computed {dict(split_counts)!r}",
        )
    if build_report.get("episode_count") != len(public_records):
        fail(
            "report_counts",
            "build_report.json",
            f"episode_count {build_report.get('episode_count')!r} != {len(public_records)}",
        )
    process_count = len({record.get("process_id") for record in private_records})
    if build_report.get("process_count") != process_count:
        fail(
            "report_counts",
            "build_report.json",
            f"process_count {build_report.get('process_count')!r} != {process_count}",
        )
    if build_report.get("replay_gate_all_passed") != replay_gate.get("all_passed"):
        fail(
            "report_counts",
            "build_report.json",
            "replay_gate_all_passed disagrees with the replay gate",
        )


def check_no_gold_actions(
    fail: Fail,
    private_records: list[dict[str, Any]],
    build_report: dict[str, Any],
    replay_gate: dict[str, Any],
) -> None:
    if build_report.get("gold_actions_released") is not False:
        fail("no_gold_actions", "build_report.json", "gold_actions_released is not false")
    if replay_gate.get("gold_actions_released") is not False:
        fail("no_gold_actions", "replay gate", "gold_actions_released is not false")
    for record in private_records:
        episode_id = str(record.get("episode_id"))
        lineage = record.get("selection_lineage") or {}
        if lineage.get("gold_actions_released") is not False:
            fail("no_gold_actions", episode_id, "selection_lineage gold_actions_released is not false")
        for path, key, _value in walk_entries(record):
            if "gold" in key.lower() and key != "gold_actions_released":
                fail(
                    "no_gold_actions",
                    episode_id,
                    f"gold-like field {path!r} present in the private record",
                )


def run_checks(release_dir: Path, process_pool_path: Path, replay_gate_path: Path) -> dict[str, Any]:
    release_dir = Path(release_dir)
    process_pool_path = Path(process_pool_path)
    replay_gate_path = Path(replay_gate_path)
    try:
        public_records = load_jsonl(release_dir / "episodes_public.jsonl")
        private_records = load_jsonl(release_dir / "episodes_private.jsonl")
        build_report = json.loads((release_dir / "build_report.json").read_text(encoding="utf-8"))
        process_pool = json.loads(process_pool_path.read_text(encoding="utf-8"))
        replay_gate = json.loads(replay_gate_path.read_text(encoding="utf-8"))
        for label, value in (("build_report", build_report), ("process_pool", process_pool), ("replay_gate", replay_gate)):
            if not isinstance(value, dict):
                raise ValueError(f"{label} is not a JSON object")
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return {
            "ok": False,
            "release_dir": str(release_dir),
            "episode_count": 0,
            "process_count": 0,
            "check_count": len(CHECK_NAMES),
            "checks": {name: False for name in CHECK_NAMES},
            "failure_count": 1,
            "failures": [{"check": "input_files", "subject": str(release_dir), "detail": str(exc)}],
        }

    findings: list[dict[str, str]] = []

    def fail(check: str, subject: str, detail: str) -> None:
        findings.append({"check": check, "subject": subject, "detail": detail})

    checks: tuple[tuple[str, Callable[[], None]], ...] = (
        ("episode_id_pairing", lambda: check_episode_id_pairing(fail, public_records, private_records)),
        ("unique_ids", lambda: check_unique_ids(fail, public_records, private_records, process_pool, replay_gate)),
        ("primary_contract_per_process", lambda: check_primary_contract_per_process(fail, private_records, build_report)),
        ("horizon_steps", lambda: check_horizon_steps(fail, public_records)),
        ("action_bounds", lambda: check_action_bounds(fail, public_records)),
        ("public_observation_fields", lambda: check_public_observation_fields(fail, public_records)),
        ("public_hidden_keys", lambda: check_public_hidden_keys(fail, public_records)),
        ("no_paths_or_secrets", lambda: check_no_paths_or_secrets(fail, public_records, private_records)),
        ("replay_gate_passed", lambda: check_replay_gate_passed(fail, replay_gate, process_pool, private_records)),
        ("process_pool_coverage", lambda: check_process_pool_coverage(fail, process_pool, private_records)),
        ("building_single_split", lambda: check_building_single_split(fail, public_records, private_records, build_report)),
        ("one_process_per_building_season", lambda: check_one_process_per_building_season(fail, process_pool, private_records)),
        ("report_counts", lambda: check_report_counts(fail, public_records, private_records, build_report, replay_gate)),
        ("no_gold_actions", lambda: check_no_gold_actions(fail, private_records, build_report, replay_gate)),
    )
    for check_name, check in checks:
        try:
            check()
        except Exception as exc:  # malformed nested data is a failed check, not a traceback
            fail(check_name, "checker", f"{type(exc).__name__}: {exc}")

    failed_checks = {finding["check"] for finding in findings}
    return {
        "ok": not findings,
        "release_dir": str(release_dir),
        "episode_count": len(public_records),
        "process_count": len({record.get("process_id") for record in private_records}),
        "check_count": len(CHECK_NAMES),
        "checks": {name: name not in failed_checks for name in CHECK_NAMES},
        "failure_count": len(findings),
        "failures": findings,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-dir", type=Path, default=DEFAULT_RELEASE_DIR)
    parser.add_argument("--process-pool", type=Path, default=DEFAULT_PROCESS_POOL)
    parser.add_argument("--replay-gate", type=Path, default=DEFAULT_REPLAY_GATE)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_RELEASE_DIR / "qa_report.json",
        help="write the JSON summary here atomically (default: release qa_report.json)",
    )
    args = parser.parse_args()
    summary = run_checks(args.release_dir, args.process_pool, args.replay_gate)
    rendered = json.dumps(summary, sort_keys=True, indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # Keep the report either old or new; a killed QA process must not leave a
    # partially written report that looks valid to downstream tooling.
    fd, temporary_name = tempfile.mkstemp(prefix=f".{args.output.name}.", dir=args.output.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temporary:
            temporary.write(rendered)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, args.output)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    print(rendered, end="")
    if not summary["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
