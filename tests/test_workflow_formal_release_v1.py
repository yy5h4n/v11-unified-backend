import json

from build_workflow_formal_release_v1 import OUTPUT, build


def _rows(name):
    return [json.loads(line) for line in (OUTPUT / name).read_text(encoding="utf-8").splitlines() if line]


def test_workflow_release_has_30_full_responsibilities_and_300_distinct_processes():
    report = build()
    assert report["status"] == "PASS"
    assert report["statistics"] == {
        "responsibility_count": 30,
        "episode_count": 300,
        "unique_process_count": 300,
        "unique_reference_environment_count": 300,
        "unique_noop_environment_count": 300,
        "unique_query_count": 120,
        "minimum_query_variants_per_responsibility": 4,
    }
    private = _rows("episodes_private.jsonl")
    assert all(row["support_status"] == "FULL" for row in private)
    assert all(all(row["admission_receipt"]["gates"].values()) for row in private)
    assert {
        "trust_registry.py",
        "trust_evidence.py",
        "semantic_validator.py",
    } <= set(report["implementation_hashes"])


def test_workflow_public_release_has_no_contract_or_gold_trace():
    public = _rows("episodes_public.jsonl")
    wire = json.dumps(public)
    assert "contract_digest" not in wire
    assert "reference_trace_digest" not in wire
    assert "responsibility_id" not in wire
    assert all(row["initial_observation"]["inventory"]["complete"] for row in public)


def test_workflow_release_has_multiple_natural_queries_per_responsibility():
    public = _rows("episodes_public.jsonl")
    private = _rows("episodes_private.jsonl")
    responsibility_by_episode = {row["episode_id"]: row["responsibility_id"] for row in private}
    by_responsibility = {}
    for row in public:
        responsibility_id = responsibility_by_episode[row["episode_id"]]
        by_responsibility.setdefault(responsibility_id, set()).add(row["query"])
    assert len(by_responsibility) == 30
    assert min(map(len, by_responsibility.values())) >= 4
    assert len({row["query"] for row in public}) == 120
