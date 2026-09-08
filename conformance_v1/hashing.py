"""RFC 8785 JSON Canonicalization Scheme + SHA-256 content addressing.

Canonicalization rules implemented (RFC 8785 / JCS):
  * object member names sorted in lexicographic order of UTF-16 code units
  * strings escaped minimally (shorthand escapes \\b \\t \\n \\f \\r, otherwise
    \\uXXXX with lowercase hex); characters >= U+0020 are emitted verbatim
  * numbers use ECMAScript/JCS shortest-round-trip boundary formatting
    (plain decimal for 1e-6 through values below 1e21), -0.0 becomes 0,
    and non-finite or non-I-JSON integers are rejected
  * no insignificant whitespace

Content hashes are SHA-256 over the UTF-8 bytes of the canonical string of
``{"schema_version": V, "content": <object>}`` so the schema version always
participates in the digest (normative doc section 2, 69-72).
"""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any


class HashingError(ValueError):
    """Raised when a value cannot be canonicalized under RFC 8785."""


_ESC: dict[str, str] = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


def _utf16_units(s: str) -> list[int]:
    """UTF-16 code units, used for RFC 8785 key ordering."""
    units: list[int] = []
    for ch in s:
        cp = ord(ch)
        if cp < 0x10000:
            units.append(cp)
        else:
            cp -= 0x10000
            units.append(0xD800 + (cp >> 10))
            units.append(0xDC00 + (cp & 0x3FF))
    return units


def _escape_string(s: str) -> str:
    out: list[str] = []
    for ch in s:
        o = ord(ch)
        if ch in _ESC:
            out.append(_ESC[ch])
        elif o < 0x20:
            out.append("\\u%04x" % o)
        else:
            out.append(ch)
    return "".join(out)


def _number(v: float | int) -> str:
    if isinstance(v, bool):
        raise HashingError("bool is not a JSON number")
    if isinstance(v, int):
        if abs(v) > 9007199254740991:
            raise HashingError("integer is outside the exact I-JSON binary64 range")
        return "0" if v == 0 else str(v)
    if not math.isfinite(v):
        raise HashingError("non-finite numbers are not permitted in JCS")
    if v == 0.0:  # covers -0.0
        return "0"
    # Python and ECMAScript both start from a shortest round-trip decimal, but
    # choose different plain/scientific notation at 1e-6 and 1e21.  Normalize
    # Python's representation into the ECMAScript form required by RFC 8785.
    s = repr(float(v)).lower()
    sign = ""
    if s.startswith("-"):
        sign, s = "-", s[1:]
    if "e" in s:
        coefficient, exponent_text = s.split("e", 1)
        exponent = int(exponent_text)
    else:
        coefficient, exponent = s, 0
    if "." in coefficient:
        integer_part, fraction_part = coefficient.split(".", 1)
    else:
        integer_part, fraction_part = coefficient, ""
    digits = integer_part + fraction_part
    decimal_position = len(integer_part) + exponent
    while len(digits) > 1 and digits.startswith("0"):
        digits = digits[1:]
        decimal_position -= 1
    while len(digits) > 1 and digits.endswith("0"):
        digits = digits[:-1]

    if 0 < decimal_position <= 21:
        if len(digits) <= decimal_position:
            body = digits + "0" * (decimal_position - len(digits))
        else:
            body = digits[:decimal_position] + "." + digits[decimal_position:]
    elif -6 < decimal_position <= 0:
        body = "0." + "0" * (-decimal_position) + digits
    else:
        body = digits[0]
        if len(digits) > 1:
            body += "." + digits[1:]
        scientific_exponent = decimal_position - 1
        body += "e" + ("+" if scientific_exponent >= 0 else "") + str(scientific_exponent)
    return sign + body


def canonical(data: Any) -> str:
    """Return the RFC 8785 canonical serialization of *data*."""
    if data is None:
        return "null"
    if data is True:
        return "true"
    if data is False:
        return "false"
    if isinstance(data, str):
        return '"%s"' % _escape_string(data)
    if isinstance(data, bool):  # bool is subclass of int; keep guard order above
        raise HashingError("unreachable")
    if isinstance(data, (int, float)):
        return _number(data)
    if isinstance(data, list):
        return "[" + ",".join(canonical(x) for x in data) + "]"
    if isinstance(data, tuple):
        return "[" + ",".join(canonical(x) for x in data) + "]"
    if isinstance(data, dict):
        keys = sorted(data.keys(), key=_utf16_units)
        body = ",".join(
            '"%s":%s' % (_escape_string(k), canonical(data[k])) for k in keys
        )
        return "{" + body + "}"
    raise HashingError(f"cannot canonicalize value of type {type(data).__name__}")


def canonical_bytes(data: Any) -> bytes:
    return canonical(data).encode("utf-8")


def jcs_load(text: str) -> Any:
    """Parse JSON, rejecting the non-finite constants JCS forbids."""

    def _reject(s: str) -> Any:
        raise HashingError(f"non-finite constant {s!r} forbidden in JCS")

    return json.loads(text, parse_constant=_reject)


def sha256_hex(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def content_hash(obj: dict, schema_version: str) -> str:
    """Content hash of a core object: schema version participates."""
    payload = {"schema_version": schema_version, "content": obj}
    return sha256_hex(canonical_bytes(payload))


def object_hash(obj: dict) -> str:
    """Content hash of a core object.

    The ``hash`` field and the mutable ``statuses`` lifecycle block are
    excluded so that a freeze/release transition never changes the content
    address of a frozen version.  Statuses are validated independently
    (STATUS_SEPARATION_VIOLATION covers collapse; the cross-object validator
    checks transition legality).
    """
    body = {k: v for k, v in obj.items() if k not in ("hash", "statuses")}
    return content_hash(body, body.get("schema_version", ""))
