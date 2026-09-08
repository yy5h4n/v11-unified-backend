"""Config loaders and shared conformance error type.

Loads failure_codes.json, release_config.json and MANIFEST.json from the package
root.  ConformanceError carries a machine-readable code from the failure-code
catalog so every transition, exclusion and adjudication is attributable.
"""

from __future__ import annotations

import json
import os
from typing import Any

PACKAGE_DIR = os.path.dirname(os.path.abspath(__file__))

DEFAULT_SCHEMA_VERSION = "1.0"


class ConformanceError(Exception):
    """A conformance failure carrying a machine-readable reason code."""

    def __init__(self, code: str, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


class Config:
    """Loaded package configuration: failure codes, release config, manifest."""

    def __init__(self, package_dir: str = PACKAGE_DIR):
        self.package_dir = package_dir
        with open(os.path.join(package_dir, "failure_codes.json"), "r", encoding="utf-8") as f:
            self.failure_codes = json.load(f)
        with open(os.path.join(package_dir, "release_config.json"), "r", encoding="utf-8") as f:
            self.release_config = json.load(f)
        with open(os.path.join(package_dir, "MANIFEST.json"), "r", encoding="utf-8") as f:
            self.manifest = json.load(f)
        self.codes: dict[str, dict] = {c["code"]: c for c in self.failure_codes}

    def require_code(self, code: str) -> str:
        """Return the canonical code after verifying it exists in the catalog."""
        if code not in self.codes:
            raise ConformanceError("UNKNOWN_REASON_CODE", f"reason code {code!r} is not in the catalog")
        return code

    def error(self, code: str, message: str) -> ConformanceError:
        return ConformanceError(self.require_code(code), message)

    def fixture(self, relative_path: str) -> dict[str, Any]:
        with open(os.path.join(self.package_dir, relative_path), "r", encoding="utf-8") as f:
            return json.load(f)


CONFIG = Config()
