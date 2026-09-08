import unittest

from conformance_v1 import hashing
from conformance_v1.config import CONFIG


class TestHashingVectors(unittest.TestCase):
    def test_rfc8785_vectors(self):
        vectors = CONFIG.fixture("fixtures/hashing_vectors.json")["vectors"]
        self.assertTrue(len(vectors) >= 8)
        for v in vectors:
            if "raw" in v["input"]:
                # sha256 anchors: canonical string given directly
                self.assertEqual(hashing.sha256_hex(v["input"]["raw"]), v["sha256"])
                continue
            out = hashing.canonical(v["input"])
            self.assertEqual(out, v["canonical"], f"canonical mismatch for {v['id']}")
            self.assertEqual(hashing.sha256_hex(out), v["sha256"], f"sha mismatch for {v['id']}")

    def test_rfc8785_appendix_example(self):
        """The exact RFC 8785 section 3.2 example."""
        inp = {
            "numbers": [333333333.33333329, 1e30, 4.50, 2e-3, 0.000000000000000000000000001],
            "string": "€$\x0f\nA'B\"\\",
            "literals": [None, True, False],
        }
        expected = '{"literals":[null,true,false],"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27],"string":"€$\\u000f\\nA\'B\\"\\\\"}'
        self.assertEqual(hashing.canonical(inp), expected)

    def test_key_order_control_chars(self):
        self.assertEqual(
            hashing.canonical({"\u0001": "1", "aa": 1, "\u0000": "0", "a": 1}),
            '{"\\u0000":"0","\\u0001":"1","a":1,"aa":1}',
        )

    def test_number_rules(self):
        self.assertEqual(hashing.canonical({"a": -0.0, "b": 1.0, "c": -1.5e2, "d": 2e-3}),
                         '{"a":0,"b":1,"c":-150,"d":0.002}')

    def test_ecmascript_notation_boundaries(self):
        self.assertEqual(hashing.canonical(1e-7), "1e-7")
        self.assertEqual(hashing.canonical(1e-6), "0.000001")
        self.assertEqual(hashing.canonical(1e20), "100000000000000000000")
        self.assertEqual(hashing.canonical(1e21), "1e+21")

    def test_non_ijson_integer_rejected(self):
        from conformance_v1.hashing import HashingError
        with self.assertRaises(HashingError):
            hashing.canonical(9007199254740992)

    def test_nan_rejected(self):
        from conformance_v1.hashing import HashingError
        with self.assertRaises(HashingError):
            hashing.canonical({"a": float("nan")})
        with self.assertRaises(HashingError):
            hashing.jcs_load('{"a": NaN}')

    def test_content_hash_includes_schema_version(self):
        obj = {"object_type": "Query", "id": "Q-X", "text": "x"}
        self.assertNotEqual(hashing.content_hash(obj, "1.0"), hashing.content_hash(obj, "1.1"))

    def test_object_hash_excludes_statuses(self):
        base = {"object_type": "Query", "id": "Q-X", "text": "x", "schema_version": "1.0"}
        a = dict(base, statuses={"release_status": "provisional"})
        b = dict(base, statuses={"release_status": "frozen"})
        self.assertEqual(hashing.object_hash(a), hashing.object_hash(b))

    def test_utf16_key_order(self):
        # U+1F600 (surrogate pair) sorts before a BMP char with high code point
        d = {chr(0x1F600): 1, chr(0xE000): 2}
        keys = sorted(d.keys(), key=hashing._utf16_units)
        self.assertEqual(keys, [chr(0x1F600), chr(0xE000)])


if __name__ == "__main__":
    unittest.main()
