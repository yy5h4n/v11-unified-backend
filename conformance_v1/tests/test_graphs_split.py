import unittest

from conformance_v1.config import CONFIG
from conformance_v1.graphs import (
    UnionFind,
    assign_splits,
    build_physical_provenance_graph,
    build_semantic_provenance_graph,
    partition_semantic,
    split_leakage_audit,
)


def _unit(uid, hh, **kw):
    u = {"id": uid, "household_id": hh}
    u.update(kw)
    return u


class TestUnionFind(unittest.TestCase):
    def test_components(self):
        uf = UnionFind(["a", "b", "c", "d"])
        uf.union("a", "b")
        uf.union("c", "d")
        comps = uf.components()
        self.assertEqual(len(comps), 2)


class TestSemanticGraph(unittest.TestCase):
    def test_e2e_units_components(self):
        fx = CONFIG.fixture("fixtures/e2e/corpus_to_episode.json")
        units = fx["corpus"]["evidence_units"]
        graph = build_semantic_provenance_graph(units)
        self.assertEqual(len(graph["components"]), 2)
        self.assertIn(sorted(["EU-002", "EU-003"]), graph["components"])
        self.assertIn(["EU-001"], graph["components"])

    def test_study_does_not_merge(self):
        units = [
            _unit("u1", "hh-1", study_id="s1"),
            _unit("u2", "hh-2", study_id="s1"),
            _unit("u3", "hh-3", study_id="s2"),
        ]
        graph = build_semantic_provenance_graph(units)
        self.assertEqual(len(graph["components"]), 3)

    def test_template_ancestor_merges(self):
        units = [
            _unit("u1", "hh-1", template_ancestor="t-1"),
            _unit("u2", "hh-2", template_ancestor="t-1"),
        ]
        graph = build_semantic_provenance_graph(units)
        self.assertEqual(len(graph["components"]), 1)


class TestPhysicalGraph(unittest.TestCase):
    def test_e2e_processes_are_separate(self):
        fx = CONFIG.fixture("fixtures/e2e/corpus_to_episode.json")
        procs = [{"id": p["id"], "physical_meta": p["physical_meta"]} for p in fx["process_drafts"]]
        graph = build_physical_provenance_graph(procs)
        self.assertEqual(len(graph["components"]), 3)

    def test_shared_building_merges(self):
        procs = [
            {"id": "p1", "physical_meta": {"building_id": "b", "generator_seed": 1, "weather_ref": "w"}},
            {"id": "p2", "physical_meta": {"building_id": "b", "generator_seed": 2, "weather_ref": "w"}},
        ]
        graph = build_physical_provenance_graph(procs)
        self.assertEqual(len(graph["components"]), 1)


class TestPartitionSemantic(unittest.TestCase):
    def test_deterministic(self):
        fx = CONFIG.fixture("fixtures/e2e/corpus_to_episode.json")
        units = fx["corpus"]["evidence_units"]
        p1 = partition_semantic(units, seed=7, ratios={"discovery": 1.0})
        p2 = partition_semantic(units, seed=7, ratios={"discovery": 1.0})
        self.assertEqual(p1, p2)


class TestSplitter(unittest.TestCase):
    def test_deterministic_assignment(self):
        episodes = [{"id": "E-1", "physical_label": "positive_opportunity"},
                    {"id": "E-2", "physical_label": "positive_opportunity"},
                    {"id": "E-3", "physical_label": "positive_opportunity"}]
        a1, _ = assign_splits(episodes, "seen_responsibility_unseen_process",
                              CONFIG.release_config, seed=101,
                              responsibility_of={e["id"]: "R" for e in episodes},
                              process_of={e["id"]: e["id"] for e in episodes})
        a2, _ = assign_splits(episodes, "seen_responsibility_unseen_process",
                              CONFIG.release_config, seed=101,
                              responsibility_of={e["id"]: "R" for e in episodes},
                              process_of={e["id"]: e["id"] for e in episodes})
        self.assertEqual(a1, a2)

    def test_shared_process_forces_same_split(self):
        episodes = [{"id": "E-1", "physical_label": "positive_opportunity"},
                    {"id": "E-2", "physical_label": "positive_opportunity"}]
        a1, _ = assign_splits(episodes, "seen_responsibility_unseen_process",
                              CONFIG.release_config, seed=101,
                              responsibility_of={"E-1": "R1", "E-2": "R2"},
                              process_of={"E-1": "PP-1", "E-2": "PP-1"})
        self.assertEqual(a1["E-1"], a1["E-2"])

    def test_split_leakage_audit(self):
        fx = CONFIG.fixture("fixtures/golden/golden_failure_closed.json")
        scenario = fx["scenario"]
        codes = split_leakage_audit(scenario["episodes"],
                                    {e["id"]: e["split"] for e in scenario["episodes"]},
                                    scenario["physical_component_of"])
        self.assertEqual(codes, ["SPLIT_LEAKAGE"])

    def test_quota_infeasible(self):
        cfg = {"quotas": {"per_split_min_episodes": {"train": 1, "dev": 1, "test": 1},
                          "per_stratum_min_episodes": {"boundary_opportunity": 1}}}
        episodes = [{"id": "E-1", "physical_label": "positive_opportunity"}]
        _, codes = assign_splits(episodes, "seen_responsibility_unseen_process", cfg, seed=1,
                                 responsibility_of={"E-1": "R"}, process_of={"E-1": "P"})
        self.assertIn("QUOTA_INFEASIBLE", codes)


if __name__ == "__main__":
    unittest.main()
