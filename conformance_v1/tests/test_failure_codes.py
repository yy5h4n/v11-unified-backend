import unittest
import hashlib
from pathlib import Path

from conformance_v1.config import CONFIG
from conformance_v1 import hashing
from conformance_v1.build_release_manifest import normative_artifacts, normative_text_without_section_15


class TestFailureCodes(unittest.TestCase):
    def test_unique_codes(self):
        codes = [c["code"] for c in CONFIG.failure_codes]
        self.assertEqual(len(codes), len(set(codes)))

    def test_all_codes_in_schema(self):
        from conformance_v1 import schemas
        self.assertEqual(schemas.validate(CONFIG.failure_codes, schemas.get_schema("failure_codes.schema.json")), [])

    def test_fixture_referenced_codes_exist(self):
        required = set()
        required.add("POST_FREEZE_MUTATION")
        required.add("SEED_LEAKAGE")
        required.add("CERTIFICATION_OPTIMIZATION")
        required.add("BOUND_NOT_COVERING_PI_AUTH")
        required.add("GOLD_ACTION_IN_CONTRACT")
        required.add("ENTAILMENT_FAILURE")
        required.add("UNSUPPORTED_INFERENCE")
        required.add("STATUS_SEPARATION_VIOLATION")
        required.add("HASH_MISMATCH")
        required.add("SCHEMA_VIOLATION")
        required.add("INVALID_TRANSITION")
        required.add("MISSING_REQUIRED_COVERAGE")
        required.add("SPLIT_LEAKAGE")
        required.add("QUOTA_INFEASIBLE")
        required.add("FAILED_CLOSED")
        required.add("OPPORTUNITY_THRESHOLD_INVALID")
        required.add("POLICY_UNADMISSIBLE")
        required.add("REPLAY_MISMATCH")
        for code in required:
            self.assertIn(code, CONFIG.codes, f"{code} must be in the failure-code catalog")

    def test_manifest_frozen_claims(self):
        manifest = CONFIG.manifest
        self.assertEqual(manifest["status"], "frozen")
        self.assertEqual(manifest["test_command"], "python3 tests/run_all.py")
        self.assertTrue(manifest["conformance_claims"])

    def test_manifest_binds_normative_document_and_every_artifact(self):
        manifest = CONFIG.manifest
        package_dir = Path(__file__).resolve().parents[1]
        spec_path = package_dir.parent / "DATASET_CONSTRUCTION_PIPELINE_V1.md"
        spec_bytes = spec_path.read_bytes()
        self.assertEqual(hashlib.sha256(spec_bytes).hexdigest(), manifest["normative_document"]["full_sha256"])
        normative_text = normative_text_without_section_15(spec_bytes.decode("utf-8"))
        self.assertEqual(
            hashlib.sha256(normative_text.encode("utf-8")).hexdigest(),
            manifest["normative_document"]["normative_sha256_excluding_section_15"],
        )
        expected_paths = {p.relative_to(package_dir).as_posix() for p in normative_artifacts()}
        self.assertEqual(set(manifest["artifact_sha256"]), expected_paths)
        for rel, expected in manifest["artifact_sha256"].items():
            self.assertEqual(hashlib.sha256((package_dir / rel).read_bytes()).hexdigest(), expected, rel)
        tuple_payload = {
            "spec_version": manifest["spec_version"],
            "package_version": manifest["package_version"],
            "normative_document": manifest["normative_document"],
            "artifact_sha256": manifest["artifact_sha256"],
        }
        self.assertEqual(
            hashing.sha256_hex(hashing.canonical_bytes(tuple_payload)),
            manifest["release_tuple_sha256"],
        )

    def test_release_config_thresholds(self):
        cfg = CONFIG.release_config["opportunity_thresholds"]
        self.assertTrue(0 <= cfg["delta_noop"] < cfg["delta_positive"] <= 1)

    def test_seeds_disjoint(self):
        seeds = CONFIG.release_config["seeds"]
        self.assertEqual(set(seeds["search_seeds"]) & set(seeds["certification_seeds"]), set())

    def test_pi0_in_pi_cert(self):
        cfg = CONFIG.release_config
        ids = [e["id"] for e in cfg["pi_cert"]["entries"]]
        self.assertIn(cfg["pi_0"]["id"], ids)


if __name__ == "__main__":
    unittest.main()
