"""Pure offline Episode clause evaluator for the frozen v4-flash pilot contracts.

This module is deliberately self-contained: it imports only the standard
library, performs no native/backend/network calls, and never edits or imports
the contract builder or runners. It is the semantic scoring core used to check
whether an already-recorded run satisfies a machine-readable ``task_clauses``
specification.

Interface
---------
``score_run(run, clause) -> dict``
    Compatible with ``tools/build_v4_flash_offline_contracts.score_run``:
    the same two positional arguments are accepted and the returned dict always
    contains ``score`` (0.0..1.0), ``pass`` (bool) and ``reason`` (str). The
    return value is a superset of the builder report and additionally carries
    clause-level detail: actual observed values, applied thresholds, per-frame
    times, operator results and violation frames.

Run format (consumed, never produced)
-------------------------------------
``run`` is a dict with:
    ``ok``                  bool; anything falsy short-circuits to no_trace.
    ``trace``               non-empty list of step frames. Each frame is either
                            ``{'observation': <obs>, 'time_seconds': <t>}`` or
                            the raw observation itself. The last frame is the
                            terminal state. The first frame is the state after
                            the first pilot action (NOT the pre-action state
                            when ``initial_observation`` is supplied).
    ``initial_observation`` actual pre-action observation captured before the
                            witness/baseline actions start (the reset state).
                            When omitted, falls back to ``prefix_observation``
                            then ``trace[0]`` (legacy format compatibility).
    ``prefix_observation``  actual pre-action observation captured immediately
                            before the pilot horizon (equals the initial
                            observation when no documented prefix exists).
                            When omitted, falls back to the initial anchor.

Clause format
-------------
``clause`` is the frozen ``task_clauses`` dict, e.g.
    {
      'target':       <predicate>,   # terminal goal, evaluated once on the
                                     # final trace observation only
      'constraints':  [<predicate>], # full-trajectory validity, evaluated on
                                     # EVERY trace observation (intermediate
                                     # violation fails the run)
      ...metadata...                 # 'steps', 'seed', 'schema_revision',
                                     # 'legal_counterexample', ... are ignored.
    }

A predicate is either
    ``{'all_of': [<predicate>, ...]}``          structural conjunction, or
    ``{'path': <path>, <operator>: <operand>, ...}``  leaf predicate.

Supported operators (all operators present in one predicate must AND):
    equals/one_of              exact JSON equality / membership (bool is never
                               equal to int; list equality is order-sensitive).
    range/min/max/finite/type  numeric range, bounds, finiteness, JSON type.
    min_delta_from_initial     observed - initial >= operand  (signed)
    max_delta_from_initial     observed - initial <= operand  (signed upper
                               bound, NOT an absolute value)
    min_delta_from_prefix      observed - prefix >= operand   (signed)

``path`` may be a dotted string or an explicit list of path tokens. Dotted
resolution only backtracks over dot-containing keys when the direct key is
absent, and rejects the path as ambiguous when more than one suffix is a real
key. Keys that themselves contain dots should be addressed with an explicit
token list.

Rejection model
---------------
Schema/authoring errors in the clause (unknown operator key, missing path,
empty all_of, bool/non-finite/ill-shaped numeric operands, an operator with no
path) raise ``ValueError``; they are bugs in the spec and must fail loudly.

Observation/run mismatches (a well-formed path missing from a particular
observation, an ambiguous dotted resolution, or a bool/non-finite value where
a numeric operator requires a finite number) fail closed: the clause reports
``pass False`` with a stable reason. A valid run with legal actions that never
reaches the terminal target therefore reports ``pass False``; validity never
substitutes for task success.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

__all__ = ["score_run", "ALLOWED_OPERATORS", "CLAUSE_METADATA_KEYS"]

# Signed-delta comparisons keep the same 1e-9 boundary slack as the original
# builder implementation so an exact JSON boundary (e.g. +0.10000000000000002
# vs threshold 0.1) is not rejected by float representation noise.
EPS = 1e-9

ALLOWED_OPERATORS = {
    "equals",
    "one_of",
    "range",
    "min",
    "max",
    "finite",
    "type",
    "min_delta_from_initial",
    "max_delta_from_initial",
    "min_delta_from_prefix",
}

CLAUSE_METADATA_KEYS = {
    "steps",
    "prefix_steps",
    "seed",
    "capability",
    "precondition",
    "schema_revision",
    "state_mapping_version",
    "legal_counterexample",
    "constraint_scope",
}

_TYPE_ALIASES = {
    "list": "list",
    "dict": "dict",
    "object": "dict",
    "str": "string",
    "string": "string",
    "number": "number",
    "bool": "bool",
    "boolean": "bool",
}


class _MissingPath(Exception):
    pass


class _AmbiguousPath(Exception):
    pass


def _is_finite_number(v: Any) -> bool:
    return (
        not isinstance(v, bool)
        and isinstance(v, (int, float))
        and math.isfinite(float(v))
    )


def _check_numeric_operand(v: Any, op: str) -> None:
    if isinstance(v, bool):
        raise ValueError(f"bool operand for {op}: bool is not a valid numeric operand")
    if not isinstance(v, (int, float)):
        raise ValueError(f"non-numeric operand for {op}: {v!r}")
    if not math.isfinite(float(v)):
        raise ValueError(f"non-finite operand for {op}: {v!r}")


def _json_eq(a: Any, b: Any) -> bool:
    """Exact JSON equality: bools never equal ints; int/float mix numerically."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if type(a) is not type(b):
        return False
    if isinstance(a, list):
        return len(a) == len(b) and all(_json_eq(x, y) for x, y in zip(a, b))
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(_json_eq(a[k], b[k]) for k in a)
    return a == b


def _type_matches(v: Any, type_name: str) -> bool:
    canonical = _TYPE_ALIASES[type_name]
    if canonical == "list":
        return isinstance(v, list)
    if canonical == "dict":
        return isinstance(v, dict)
    if canonical == "string":
        return isinstance(v, str)
    if canonical == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    if canonical == "bool":
        return isinstance(v, bool)
    return False


def _path_label(path: Any) -> str:
    if isinstance(path, str):
        return path
    return ".".join(str(t) for t in path)


def _resolve(obj: Any, path: Any) -> Any:
    """Resolve a dotted string or explicit token-list path inside obj."""
    parts: List[Any] = path if isinstance(path, list) else path.split(".")
    if isinstance(path, list) and not parts:
        raise _MissingPath(_path_label(path))
    i = 0
    while i < len(parts):
        tok = parts[i]
        if isinstance(obj, dict):
            if isinstance(tok, str) and tok in obj:
                obj = obj[tok]
                i += 1
                continue
            if not isinstance(path, str):
                raise _MissingPath(_path_label(path))
            if not isinstance(tok, str):
                raise _MissingPath(_path_label(path))
            candidates = []
            for j in range(len(parts), i, -1):
                key = ".".join(str(t) for t in parts[i:j])
                if key in obj:
                    candidates.append(key)
            if len(candidates) > 1:
                raise _AmbiguousPath(_path_label(path))
            if not candidates:
                raise _MissingPath(_path_label(path))
            obj = obj[candidates[0]]
            i = len(parts)
            continue
        if isinstance(obj, list):
            try:
                idx = int(tok)
            except (TypeError, ValueError):
                raise _MissingPath(_path_label(path))
            if not 0 <= idx < len(obj):
                raise _MissingPath(_path_label(path))
            obj = obj[idx]
            i += 1
            continue
        raise _MissingPath(_path_label(path))
    return obj


def _parse_operand(op: str, arg: Any) -> None:
    """Validate an operator operand; raises ValueError for malformed specs."""
    if op == "equals":
        if isinstance(arg, float) and not math.isfinite(arg):
            raise ValueError("non-finite numeric operand for equals")
        return
    if op == "one_of":
        if not isinstance(arg, list) or not arg:
            raise ValueError("one_of must be a non-empty list")
        for item in arg:
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError("non-finite member inside one_of")
        return
    if op == "range":
        if not isinstance(arg, list) or len(arg) != 2:
            raise ValueError("range must be a two-element [lo, hi] list")
        lo, hi = arg
        _check_numeric_operand(lo, "range[0]")
        _check_numeric_operand(hi, "range[1]")
        if lo > hi:
            raise ValueError(f"range lower bound exceeds upper bound: {arg!r}")
        return
    if op in {"min", "max"}:
        _check_numeric_operand(arg, op)
        return
    if op == "finite":
        if arg is not True:
            raise ValueError("finite currently supports only the operand True")
        return
    if op == "type":
        if not isinstance(arg, str) or arg not in _TYPE_ALIASES:
            raise ValueError(f"unsupported type operand: {arg!r}")
        return
    if op in {"min_delta_from_initial", "max_delta_from_initial", "min_delta_from_prefix"}:
        _check_numeric_operand(arg, op)
        return
    raise ValueError(f"unknown operator: {op!r}")


def _parse_predicate(node: Any, where: str = "predicate") -> Dict[str, Any]:
    if not isinstance(node, dict):
        raise ValueError(f"{where} must be an object, got {type(node).__name__}")
    if "all_of" in node:
        for key in node:
            if key != "all_of":
                raise ValueError(
                    f"{where}: all_of cannot be combined with other keys ({key!r})"
                )
        children = node["all_of"]
        if not isinstance(children, list) or not children:
            raise ValueError(f"{where}: all_of must be a non-empty list")
        return {
            "kind": "all",
            "path": None,
            "path_label": None,
            "ops": [],
            "children": [_parse_predicate(c, where + "/all_of") for c in children],
        }
    if "path" not in node:
        raise ValueError(f"{where}: predicate is missing 'path'")
    path = node["path"]
    if (
        not isinstance(path, (str, list))
        or (isinstance(path, str) and not path)
        or (
            isinstance(path, list)
            and (
                not path
                or not all(
                    (isinstance(t, str) and bool(t))
                    or (isinstance(t, int) and not isinstance(t, bool))
                    for t in path
                )
            )
        )
    ):
        raise ValueError(f"{where}: path must be a non-empty string or token list")
    ops: List[Tuple[str, Any]] = []
    for key, arg in node.items():
        if key == "path":
            continue
        if key not in ALLOWED_OPERATORS:
            raise ValueError(
                f"{where}: unknown operator {key!r} (path {_path_label(path)!r})"
            )
        _parse_operand(key, arg)
        ops.append((key, arg))
    if not ops:
        raise ValueError(f"{where}: path {_path_label(path)!r} has no operator")
    return {
        "kind": "leaf",
        "path": path,
        "path_label": _path_label(path),
        "ops": ops,
        "children": [],
    }


def _parse_clause(clause: Any) -> Dict[str, Any]:
    if not isinstance(clause, dict):
        raise ValueError("clause must be an object")
    if "target" not in clause:
        raise ValueError("clause is missing 'target'")
    target = _parse_predicate(clause["target"], "target")
    raw_constraints = clause.get("constraints", [])
    if not isinstance(raw_constraints, list):
        raise ValueError("clause 'constraints' must be a list")
    constraints = [
        _parse_predicate(c, f"constraints[{i}]") for i, c in enumerate(raw_constraints)
    ]
    raw_scope = clause.get("constraint_scope", {"include_trace": True})
    if not isinstance(raw_scope, dict):
        raise ValueError("clause 'constraint_scope' must be an object")
    allowed_scope = {"include_initial", "include_prefix", "include_trace"}
    unknown_scope = set(raw_scope) - allowed_scope
    if unknown_scope:
        raise ValueError(f"constraint_scope has unknown fields: {sorted(unknown_scope)!r}")
    scope = {"include_initial": False, "include_prefix": False, "include_trace": True}
    for key, value in raw_scope.items():
        if not isinstance(value, bool):
            raise ValueError(f"constraint_scope.{key} must be boolean")
        scope[key] = value
    if not any(scope.values()):
        raise ValueError("constraint_scope must include at least one frame domain")
    return {"target": target, "constraints": constraints, "constraint_scope": scope}


def _frame_observation(frame: Any) -> Any:
    if isinstance(frame, dict) and "observation" in frame:
        return frame["observation"]
    return frame


def _frame_time(frame: Any) -> Optional[float]:
    if isinstance(frame, dict):
        t = frame.get("time_seconds")
        if isinstance(t, (int, float)) and not isinstance(t, bool):
            return float(t)
    return None


def _anchor_value(anchor_obs: Any, path: Any) -> Any:
    if anchor_obs is None:
        raise _MissingPath(_path_label(path))
    return _resolve(anchor_obs, path)


def _apply_operator(
    op: str,
    arg: Any,
    value: Any,
    resolved: bool,
    anchors: Dict[str, Any],
    path: Any,
) -> Dict[str, Any]:
    label = _path_label(path)
    result: Dict[str, Any] = {
        "operator": op,
        "argument": arg,
        "pass": False,
        "reason": "ok",
        "actual": value if resolved else None,
        "threshold": arg,
        "anchor": None,
    }
    if not resolved:
        result["reason"] = "missing_path"
        return result

    if op == "equals":
        ok = _json_eq(value, arg)
        result["pass"] = ok
        result["reason"] = "ok" if ok else "equals mismatch"
        return result
    if op == "one_of":
        ok = any(_json_eq(value, item) for item in arg)
        result["pass"] = ok
        result["reason"] = "ok" if ok else "no member of one_of equals the value"
        return result
    if op == "type":
        ok = _type_matches(value, arg)
        result["pass"] = ok
        result["reason"] = (
            "ok" if ok else f"type mismatch: expected {arg}, got {type(value).__name__}"
        )
        return result

    # Remaining operators require a finite, non-bool numeric observation.
    if not _is_finite_number(value):
        result["reason"] = "not_finite_number"
        return result

    if op == "range":
        lo, hi = arg
        ok = lo <= value <= hi
        result["pass"] = ok
        result["reason"] = (
            "ok" if ok else f"{value} outside range [{lo}, {hi}]"
        )
        return result
    if op == "min":
        ok = value >= arg
        result["pass"] = ok
        result["reason"] = "ok" if ok else f"{value} below min {arg}"
        return result
    if op == "max":
        ok = value <= arg
        result["pass"] = ok
        result["reason"] = "ok" if ok else f"{value} above max {arg}"
        return result
    if op == "finite":
        result["pass"] = True
        return result

    # Delta operators: signed difference against the pre-action anchor.
    if op in {
        "min_delta_from_initial",
        "max_delta_from_initial",
        "min_delta_from_prefix",
    }:
        role = "initial" if op.endswith("from_initial") else "prefix"
        anchor_obs = anchors.get(role)
        if anchor_obs is None:
            result["reason"] = f"missing_{role}_anchor"
            return result
        try:
            anchor_value = _anchor_value(anchor_obs, path)
        except (_MissingPath, _AmbiguousPath):
            result["reason"] = f"anchor_path_unavailable ({role})"
            return result
        if not _is_finite_number(anchor_value):
            result["reason"] = f"anchor_not_finite_number ({role})"
            return result
        delta = float(value) - float(anchor_value)
        result["actual"] = delta
        result["anchor"] = {"role": role, "value": anchor_value}
        if op.startswith("min_"):
            ok = delta + EPS >= arg
            result["reason"] = (
                "ok" if ok else f"delta {delta} below min_delta {arg}"
            )
        else:
            ok = delta - EPS <= arg
            result["reason"] = (
                "ok" if ok else f"delta {delta} above signed max_delta {arg}"
            )
        result["pass"] = ok
        return result

    raise ValueError(f"unknown operator: {op!r}")


def _resolve_leaf_value(
    predicate: Dict[str, Any], observation: Any,
) -> Tuple[Any, bool, str]:
    """Resolve one leaf path inside a frame observation.

    Returns (value, resolved, reason). ``reason`` is 'ok' when resolved,
    'missing_path' or 'ambiguous_path' otherwise.
    """
    try:
        value = _resolve(observation, predicate["path"])
        return value, True, "ok"
    except _MissingPath:
        return None, False, "missing_path"
    except _AmbiguousPath:
        return None, False, "ambiguous_path"


def _collect_leaves(
    predicate: Dict[str, Any],
    observation: Any,
    anchors: Dict[str, Any],
) -> List[Dict[str, Any]]:
    leaves: List[Dict[str, Any]] = []
    if predicate["kind"] == "leaf":
        value, resolved, resolution_reason = _resolve_leaf_value(
            predicate, observation
        )
        if resolved:
            operators = [
                _apply_operator(op, arg, value, True, anchors, predicate["path"])
                for op, arg in predicate["ops"]
            ]
        else:
            operators = [
                {
                    "operator": op,
                    "argument": arg,
                    "pass": False,
                    "reason": resolution_reason,
                    "actual": None,
                    "threshold": arg,
                    "anchor": None,
                }
                for op, arg in predicate["ops"]
            ]
        leaf_pass = bool(resolved) and all(op["pass"] for op in operators)
        leaf_reason = (
            "ok"
            if leaf_pass
            else resolution_reason
            if not resolved
            else next(
                (op["reason"] for op in operators if not op["pass"]), "failed"
            )
        )
        leaves.append(
            {
                "path": predicate["path_label"],
                "resolved": resolved,
                "value": value if resolved else None,
                "operators": operators,
                "pass": leaf_pass,
                "reason": leaf_reason,
            }
        )
        return leaves
    for child in predicate["children"]:
        leaves.extend(_collect_leaves(child, observation, anchors))
    return leaves


def _eval_predicate_on_frame(
    predicate: Dict[str, Any],
    frame: Any,
    frame_index: int,
    anchors: Dict[str, Any],
) -> Dict[str, Any]:
    observation = _frame_observation(frame)
    leaves = _collect_leaves(predicate, observation, anchors)
    frame_pass = all(leaf["pass"] for leaf in leaves)
    if frame_pass:
        frame_reason = "ok"
    else:
        frame_reason = next(
            (leaf["reason"] for leaf in leaves if not leaf["pass"]), "failed"
        )
    return {
        "index": frame_index,
        "time_seconds": _frame_time(frame),
        "pass": frame_pass,
        "reason": frame_reason,
        "leaves": leaves,
    }


def _frame_pass(frame_report: Dict[str, Any]) -> bool:
    return frame_report["pass"]


def score_run(run: Any, clause: Any) -> Dict[str, Any]:
    """Score one recorded run against one frozen clause spec.

    Returns a dict with keys ``score``, ``pass``, ``reason`` (builder
    compatible) plus ``schema``, ``ok_run``, ``checks``, ``anchors``,
    ``target`` and ``constraints`` clause-level reports.
    """
    parsed = _parse_clause(clause)
    no_trace_report = {
        "schema": "v4.flash.pilot.evaluator.v1",
        "score": 0.0,
        "pass": False,
        "reason": "no_trace",
        "ok_run": False,
        "checks": [],
        "anchors": {},
        "target": None,
        "constraints": [],
    }
    if not isinstance(run, dict) or not run.get("ok") or not run.get("trace"):
        return no_trace_report
    trace = list(run["trace"])
    if not trace:
        return no_trace_report
    observations = [_frame_observation(f) for f in trace]

    has_initial = "initial_observation" in run and run.get("initial_observation") is not None
    has_prefix = "prefix_observation" in run and run.get("prefix_observation") is not None
    # A delta is meaningful only against the explicitly captured pre-action
    # state.  Falling back to trace[0] silently changes the task semantics.
    initial_obs = run.get("initial_observation") if has_initial else None
    prefix_obs = run.get("prefix_observation") if has_prefix else None
    anchors: Dict[str, Any] = {"initial": initial_obs, "prefix": prefix_obs}

    terminal_index = len(trace) - 1
    target_frame_report = _eval_predicate_on_frame(
        parsed["target"], trace[terminal_index], terminal_index, anchors
    )
    target_pass = target_frame_report["pass"]
    target_report = {
        "kind": "target",
        "index": None,
        "label": "target",
        "pass": target_pass,
        "reason": target_frame_report["reason"],
        "domain": "terminal",
        "frames": [target_frame_report],
        "violating_frames": [] if target_pass else [terminal_index],
    }
    constraint_reports = []
    scope = parsed["constraint_scope"]
    scoped_frames = []
    if scope["include_initial"]:
        scoped_frames.append(("initial", -1, {"observation": initial_obs}))
    if scope["include_prefix"]:
        scoped_frames.append(("prefix", -2, {"observation": prefix_obs}))
    if scope["include_trace"]:
        scoped_frames.extend(("trace", j, f) for j, f in enumerate(trace))
    for i, predicate in enumerate(parsed["constraints"]):
        frames = [
            dict(
                _eval_predicate_on_frame(predicate, f, j, anchors),
                scope_domain=domain,
            )
            for domain, j, f in scoped_frames
        ]
        pass_all = all(_frame_pass(f) for f in frames)
        violating = [f["index"] for f in frames if not f["pass"]]
        first_bad = next((f for f in frames if not f["pass"]), None)
        reason = (
            "ok"
            if pass_all
            else (f"violated at frame {first_bad['index']}: {first_bad['reason']}"
                  if first_bad else "violated")
        )
        constraint_reports.append(
            {
                "kind": "constraint",
                "index": i,
                "label": f"constraint_{i}",
                "pass": pass_all,
                "reason": reason,
                "domain": "trajectory",
                "scope": dict(scope),
                "frames": frames,
                "violating_frames": violating,
            }
        )

    checks: List[Tuple[str, bool]] = [("target", target_pass)]
    checks += [
        (f"constraint_{i}", cr["pass"]) for i, cr in enumerate(constraint_reports)
    ]
    overall_pass = all(v for _, v in checks)
    if overall_pass:
        overall_reason = "ok"
    else:
        if not target_pass:
            overall_reason = f"target failed at terminal frame: {target_report['reason']}"
        else:
            overall_reason = next(
                (cr["reason"] for cr in constraint_reports if not cr["pass"]),
                "failed",
            )
    return {
        "schema": "v4.flash.pilot.evaluator.v1",
        "score": float(sum(bool(v) for _, v in checks)) / len(checks),
        "pass": overall_pass,
        "reason": overall_reason,
        "ok_run": True,
        "checks": checks,
        "anchors": {
            "initial_observation_available": has_initial,
            "prefix_observation_available": has_prefix,
            "initial_anchor_is_trace_first_frame": False,
        },
        "constraint_scope": dict(scope),
        "target": target_report,
        "constraints": constraint_reports,
    }
