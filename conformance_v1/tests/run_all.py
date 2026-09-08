"""Run all conformance tests and report a summary.

Usage:
    cd conformance_v1
    python3 tests/run_all.py
"""

import importlib
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG_PARENT = os.path.dirname(os.path.dirname(HERE))
if PKG_PARENT not in sys.path:
    sys.path.insert(0, PKG_PARENT)

MODULES = [
    "test_hashing",
    "test_schemas",
    "test_state_machine",
    "test_validator",
    "test_evaluator",
    "test_sandbox",
    "test_graphs_split",
    "test_opportunity",
    "test_golden",
    "test_negative",
    "test_e2e",
    "test_failure_codes",
]


def main() -> int:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for name in MODULES:
        module = importlib.import_module(f"conformance_v1.tests.{name}")
        for attr in dir(module):
            obj = getattr(module, attr)
            if isinstance(obj, type) and issubclass(obj, unittest.TestCase):
                suite.addTests(loader.loadTestsFromTestCase(obj))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    print("\n=== CONFORMANCE SUMMARY ===")
    print(f"tests_run={result.testsRun} failures={len(result.failures)} errors={len(result.errors)} skipped={len(result.skipped)}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
