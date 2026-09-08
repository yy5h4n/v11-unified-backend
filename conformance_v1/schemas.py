"""Minimal JSON Schema validator (stdlib only) and schema registry.

Supports the subset used by the frozen package schemas: type (incl. arrays of
types), properties, required, additionalProperties, const, enum, pattern,
minLength/maxLength, minimum/maximum, minItems/maxItems, items (single-schema
form), oneOf and internal ``$ref`` (``#/$defs/name``).  Unrecognized keywords
are ignored.  This is intentionally small: JSON Schema is necessary but
insufficient for conformance (normative doc section 12), and the cross-object
validator carries the semantic checks.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

_SCHEMA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schemas")


class SchemaValidationError(ValueError):
    """Raised with a human-readable list of schema violations."""


def _type_ok(instance: Any, type_name: str) -> bool:
    if type_name == "object":
        return isinstance(instance, dict)
    if type_name == "array":
        return isinstance(instance, list)
    if type_name == "string":
        return isinstance(instance, str)
    if type_name == "number":
        return isinstance(instance, (int, float)) and not isinstance(instance, bool)
    if type_name == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if type_name == "boolean":
        return isinstance(instance, bool)
    if type_name == "null":
        return instance is None
    return False


def _resolve_ref(schema: dict, root: dict, path: str) -> dict:
    ref = schema["$ref"]
    if not ref.startswith("#/"):
        raise SchemaValidationError(f"unsupported $ref {ref!r} (only internal refs)")
    node = root
    for part in ref[2:].split("/"):
        node = node[part]
    return node


def _validate(instance: Any, schema: dict, root: dict, path: str, errors: list[str]) -> None:
    if schema.get("$ref"):
        schema = _resolve_ref(schema, root, path)
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}, got {instance!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: value {instance!r} not in enum {schema['enum']}")
    types = schema.get("type")
    if types is not None:
        names = [types] if isinstance(types, str) else list(types)
        if not any(_type_ok(instance, t) for t in names):
            errors.append(
                f"{path}: expected type {names}, got {type(instance).__name__}"
            )
    if isinstance(instance, dict) and schema.get("type", "object") in ("object", None):
        props = schema.get("properties")
        if isinstance(props, dict):
            for k, v in instance.items():
                if k in props:
                    _validate(v, props[k], root, f"{path}.{k}", errors)
                else:
                    ap = schema.get("additionalProperties")
                    if ap is False:
                        errors.append(f"{path}: unexpected property {k!r}")
                    elif isinstance(ap, dict):
                        _validate(v, ap, root, f"{path}.{k}", errors)
        for req in schema.get("required", []):
            if req not in instance:
                errors.append(f"{path}: missing required property {req!r}")
    if isinstance(instance, list) and "items" in schema:
        items = schema["items"]
        if isinstance(items, dict):
            for i, v in enumerate(instance):
                _validate(v, items, root, f"{path}[{i}]", errors)
    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: fewer than minItems {schema['minItems']}")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: more than maxItems {schema['maxItems']}")
    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append(f"{path}: does not match pattern {schema['pattern']!r}")
    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: above maximum {schema['maximum']}")
    if "oneOf" in schema:
        passing: list[int] = []
        all_errors: dict[int, list[str]] = {}
        for i, sub in enumerate(schema["oneOf"]):
            sub_errors: list[str] = []
            _validate(instance, sub, root, path, sub_errors)
            all_errors[i] = sub_errors
            if not sub_errors:
                passing.append(i)
        if len(passing) == 1:
            return
        if not passing:
            best = min(all_errors.items(), key=lambda kv: (len(kv[1]), kv[0]))[1]
            errors.append(
                f"{path}: matches no oneOf branch ({len(schema['oneOf'])} tried)"
            )
            errors.extend(best[:5])
        else:
            errors.append(f"{path}: matches {len(passing)} oneOf branches")


def validate(instance: Any, schema: dict, path: str = "$") -> list[str]:
    """Return list of schema violation strings (empty means valid)."""
    errors: list[str] = []
    _validate(instance, schema, schema, path, errors)
    return errors


def validate_or_raise(instance: Any, schema: dict, path: str = "$") -> None:
    errors = validate(instance, schema, path)
    if errors:
        raise SchemaValidationError("; ".join(errors))


def load_schema(name: str) -> dict:
    with open(os.path.join(_SCHEMA_DIR, name), "r", encoding="utf-8") as f:
        return json.load(f)


_SCHEMA_CACHE: dict[str, dict] = {}


def get_schema(name: str) -> dict:
    if name not in _SCHEMA_CACHE:
        _SCHEMA_CACHE[name] = load_schema(name)
    return _SCHEMA_CACHE[name]


# object_type -> schema file
OBJECT_TYPE_SCHEMAS = {
    "CorpusCard": "corpus_card.schema.json",
    "EvidenceUnit": "evidence_unit.schema.json",
    "EvidenceBundle": "evidence_bundle.schema.json",
    "CanonicalResponsibility": "canonical_responsibility.schema.json",
    "Query": "query.schema.json",
    "Contract": "contract.schema.json",
    "OpportunityPredicate": "opportunity_predicate.schema.json",
    "PhysicalProcess": "physical_process.schema.json",
    "Episode": "episode.schema.json",
}


def schema_for_object(obj: dict) -> dict:
    ot = obj.get("object_type")
    if ot not in OBJECT_TYPE_SCHEMAS:
        raise SchemaValidationError(f"unknown object_type {ot!r}")
    return get_schema(OBJECT_TYPE_SCHEMAS[ot])
