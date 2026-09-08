from build_responsibility_backend_coverage_v1 import build


def test_coverage_accounts_for_all_129_responsibilities_fail_closed():
    result = build()
    assert result["statistics"] == {
        "catalog_responsibility_count": 129,
        "support_status_counts": {"FULL": 30, "PARTIAL": 24, "UNSUPPORTED": 75},
        "formal_episode_count": 300,
        "full_responsibility_with_episodes": 30,
    }
    assert len({row["responsibility_id"] for row in result["rows"]}) == 129


def test_only_admitted_workflow_responsibilities_are_full():
    result = build()
    full = [row for row in result["rows"] if row["support_status"] == "FULL"]
    partial = [row for row in result["rows"] if row["support_status"] == "PARTIAL"]
    assert len(full) == 30
    assert all(row["backend_route"] == "household_workflow_t2" for row in full)
    assert all(row["formal_episode_count"] == 10 for row in full)
    assert all(row["formal_episode_count"] == 0 for row in partial)
