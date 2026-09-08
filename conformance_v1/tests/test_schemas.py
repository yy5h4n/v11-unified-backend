import json
import os
import unittest

from conformance_v1 import schemas
from conformance_v1.config import CONFIG, PACKAGE_DIR

DSL = schemas.get_schema("temporal_contract_dsl.schema.json")


class TestSchemaRegistry(unittest.TestCase):
    def test_all_schema_files_are_valid_json(self):
        schema_dir = os.path.join(PACKAGE_DIR, "schemas")
        files = sorted(f for f in os.listdir(schema_dir) if f.endswith(".json"))
        self.assertGreaterEqual(len(files), 13)
        for f in files:
            with self.subTest(file=f):
                schemas.get_schema(f)

    def test_release_config_validates(self):
        cfg = CONFIG.release_config
        self.assertEqual(schemas.validate(cfg, schemas.get_schema("release_config.schema.json")), [])

    def test_failure_codes_validates(self):
        codes = CONFIG.failure_codes
        self.assertEqual(schemas.validate(codes, schemas.get_schema("failure_codes.schema.json")), [])

    def test_manifest_validates(self):
        manifest = CONFIG.manifest
        self.assertEqual(schemas.validate(manifest, schemas.get_schema("manifest.schema.json")), [])

    def test_e2e_fixture_drafts_pass_object_schemas(self):
        fx = CONFIG.fixture("fixtures/e2e/corpus_to_episode.json")
        self.assertEqual(schemas.validate(fx["corpus"]["corpus_card"], schemas.get_schema("corpus_card.schema.json")), [])
        for unit in fx["corpus"]["evidence_units"]:
            self.assertEqual(schemas.validate(unit, schemas.get_schema("evidence_unit.schema.json")), [])


class TestCoreObjectSchemas(unittest.TestCase):
    def _mini(self, ot, **kw):
        obj = {
            "object_type": ot, "id": "X-1", "schema_version": "1.0", "version": 1,
            "created_at": "2026-08-30T00:00:00Z",
            "statuses": {
                "semantic_status": "provisional_ai_pilot", "authorization_status": "unknown",
                "physical_status": "unassigned", "release_status": "provisional",
            },
            "hash": "0" * 64,
        }
        obj.update(kw)
        return obj

    def test_missing_required_field(self):
        obj = self._mini("Query")
        errors = schemas.validate(obj, schemas.get_schema("query.schema.json"))
        self.assertIn("missing required property", "; ".join(errors))

    def test_bad_enum_rejected(self):
        obj = self._mini("Query", responsibility_id="R", text="q",
                         paraphrase_set=["q"], equivalence_verdict="nonsense")
        errors = schemas.validate(obj, schemas.get_schema("query.schema.json"))
        self.assertTrue(any("not in enum" in e for e in errors))

    def test_collapsed_status_rejected(self):
        obj = self._mini("Query", responsibility_id="R", text="q", paraphrase_set=["q"],
                         equivalence_verdict="bidirectional_entailed")
        obj["status"] = obj.pop("statuses")
        errors = schemas.validate(obj, schemas.get_schema("query.schema.json"))
        self.assertTrue(any("missing required property 'statuses'" in e for e in errors))

    def test_status_separation_schema(self):
        s = schemas.get_schema("statuses.schema.json")
        good = {"semantic_status": "human_validated", "authorization_status": "authorized_agent_control",
                "physical_status": "positive_opportunity", "release_status": "frozen"}
        self.assertEqual(schemas.validate(good, s), [])
        bad = {"semantic_status": "human_validated", "authorization_status": "authorized_agent_control"}
        self.assertTrue(schemas.validate(bad, s))


class TestDSLSchema(unittest.TestCase):
    def test_valid_formula(self):
        formula = {"op": "within",
                   "trigger": {"op": "and", "exprs": [
                       {"op": "obs", "variable": "outside_temp", "cmp": "lt", "value": 10},
                       {"op": "time", "cmp": "ge", "value": 1}]},
                   "obligation": {"op": "obs", "variable": "living_room_temp", "cmp": "ge", "value": 18.0},
                   "deadline_intervals": 4}
        self.assertEqual(schemas.validate(formula, DSL), [])

    def test_unknown_op_rejected(self):
        self.assertTrue(schemas.validate({"op": "wibble"}, DSL))

    def test_missing_required_key_rejected(self):
        formula = {"op": "within", "trigger": {"op": "const", "value": True},
                   "obligation": {"op": "const", "value": True}}
        errors = schemas.validate(formula, DSL)
        self.assertTrue(any("missing required property 'deadline_intervals'" in e for e in errors))

    def test_nested_and_rejected(self):
        formula = {"op": "and", "exprs": [{"op": "const", "value": True},
                                          {"op": "or", "exprs": [{"op": "const", "value": False}]}]}
        self.assertEqual(schemas.validate(formula, DSL), [])

    def test_bad_comparator_rejected(self):
        formula = {"op": "obs", "variable": "x", "cmp": "between", "value": 1}
        self.assertTrue(schemas.validate(formula, DSL))


if __name__ == "__main__":
    unittest.main()
