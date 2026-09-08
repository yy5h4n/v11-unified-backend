"""Conformance test package.

Importing this package puts the parent of conformance_v1 on sys.path so that
``from conformance_v1 import ...`` works whether tests are run via
``python tests/run_all.py`` or ``python -m unittest``.
"""

import os
import sys

_PKG_PARENT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PKG_PARENT not in sys.path:
    sys.path.insert(0, _PKG_PARENT)
