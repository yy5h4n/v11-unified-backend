"""Reference temporal evaluator and Temporal Contract DSL interpreter.

Implements the finite-window protocol from normative doc section 10: for each
discrete interval ``[t, t+1)`` publish observation and pending-obligation state
at ``t``; receive action; apply transition; evaluate clauses on
``(history_t, observation_t, action_t, observation_{t+1})``; then apply
releases and deadlines whose effective time is ``t+1``.  Simultaneous events
use the Contract's frozen priority order (releases are applied before new
obligations).

Missing observations are tri-state: a clause whose required observation is
missing defers (it is neither satisfied nor violated, and its deadline clock
pauses).  Terminal states distinguish ``released``, ``continues_beyond_window``,
``censored_pending`` and ``violated_at_truncation``; a pending obligation is
never counted as success merely because its deadline lies outside the window.
"""

from __future__ import annotations

import copy
from typing import Any, Callable

from conformance_v1.config import CONFIG, ConformanceError
from conformance_v1 import hashing, schemas

TRUE, FALSE, MISSING = True, False, None


def _cmp(a: Any, op: str, b: Any) -> bool | None:
    if a is None or b is None:
        return MISSING
    if op == "eq":
        return a == b
    if op == "ne":
        return a != b
    try:
        if op == "lt":
            return a < b
        if op == "le":
            return a <= b
        if op == "gt":
            return a > b
        if op == "ge":
            return a >= b
    except TypeError:
        return MISSING
    raise ConformanceError("SCHEMA_VIOLATION", f"unknown comparator {op!r}")


def _tri_not(a: bool | None) -> bool | None:
    return MISSING if a is MISSING else not a


def _tri_and(values: list[bool | None]) -> bool | None:
    if any(v is FALSE for v in values):
        return FALSE
    if any(v is MISSING for v in values):
        return MISSING
    return TRUE


def _tri_or(values: list[bool | None]) -> bool | None:
    if any(v is TRUE for v in values):
        return TRUE
    if any(v is MISSING for v in values):
        return MISSING
    return FALSE


class EvalContext:
    def __init__(self, step: int, obs_prev: dict, obs_cur: dict, action: Any, pending: dict[str, dict]):
        self.step = step
        self.obs_prev = obs_prev
        self.obs_cur = obs_cur
        self.action = action
        self.pending = pending


def eval_pred(pred: dict, ctx: EvalContext) -> bool | None:
    op = pred["op"]
    if op == "state":
        return _cmp(ctx.obs_prev.get(pred["variable"]), pred["cmp"], pred["value"])
    if op == "obs":
        return _cmp(ctx.obs_cur.get(pred["variable"]), pred["cmp"], pred["value"])
    if op == "action":
        a = ctx.action
        if isinstance(a, dict):
            a = a.get("name")
        value = pred["value"]
        if isinstance(value, bool):
            # boolean action predicate: value True means "the current action is
            # <variable>", False means "the current action is not <variable>"
            return (a == pred["variable"]) if value else (a != pred["variable"])
        return _cmp(a, pred["cmp"], value)
    if op == "time":
        return _cmp(ctx.step, pred["cmp"], pred["value"])
    if op == "pending":
        state = ctx.pending.get(pred["clause_id"])
        return bool(state and state.get("triggered") and not state.get("satisfied"))
    raise ConformanceError("SCHEMA_VIOLATION", f"unknown predicate op {op!r}")


def eval_expr(expr: dict, ctx: EvalContext) -> bool | None:
    op = expr["op"]
    if op in ("state", "obs", "action", "time", "pending"):
        return eval_pred(expr, ctx)
    if op == "const":
        return expr["value"]
    if op == "not":
        return _tri_not(eval_expr(expr["expr"], ctx))
    if op == "and":
        return _tri_and([eval_expr(e, ctx) for e in expr["exprs"]])
    if op == "or":
        return _tri_or([eval_expr(e, ctx) for e in expr["exprs"]])
    if op == "at":
        if expr["offset"] == 0:
            sub = EvalContext(ctx.step - 1, ctx.obs_prev, ctx.obs_prev, ctx.action, ctx.pending)
        else:
            sub = EvalContext(ctx.step, ctx.obs_cur, ctx.obs_cur, ctx.action, ctx.pending)
        return eval_pred(expr["pred"], sub)
    if op == "always":
        return eval_expr(expr["expr"], ctx)
    # clause-level operators are not valid nested inside a boolean expression
    raise ConformanceError("SCHEMA_VIOLATION", f"clause-level operator {op!r} nested in expression")


_CLAUSE_LEVEL_OPS = {"within", "terminal_goal", "soft_cost", "release_when", "persist_after", "cooldown_after"}


class TemporalEvaluator:
    def __init__(self, config=CONFIG):
        self.config = config
        self._dsl_schema = schemas.get_schema("temporal_contract_dsl.schema.json")

    # -- DSL validation ----------------------------------------------------
    def validate_dsl(self, expr: dict) -> None:
        errors = schemas.validate(expr, self._dsl_schema)
        if errors:
            raise self.config.error("SCHEMA_VIOLATION", "; ".join(errors[:5]))

    def validate_contract(self, contract: dict) -> None:
        for clause in contract["clauses"]:
            self.validate_dsl(clause["formula"])
            op = clause["formula"]["op"]
            if clause["kind"] == "conditional_obligation" and op not in ("within", "persist_after"):
                raise self.config.error("SCHEMA_VIOLATION", f"clause {clause['clause_id']}: kind conditional_obligation requires within or persist_after")
            if clause["kind"] == "terminal_goal" and op != "terminal_goal":
                raise self.config.error("SCHEMA_VIOLATION", f"clause {clause['clause_id']}: kind terminal_goal requires terminal_goal")
            if clause["kind"] == "hard_invariant" and op == "within":
                raise self.config.error("SCHEMA_VIOLATION", f"clause {clause['clause_id']}: hard_invariant cannot be within")
            if clause["kind"] == "soft_cost" and op != "soft_cost":
                raise self.config.error("SCHEMA_VIOLATION", f"clause {clause['clause_id']}: kind soft_cost requires soft_cost")
            if clause["kind"] == "release_condition" and op != "release_when":
                raise self.config.error("SCHEMA_VIOLATION", f"clause {clause['clause_id']}: kind release_condition requires release_when")

    # -- transition ----------------------------------------------------------
    @staticmethod
    def apply_transition(obs: dict, action: Any, transition: dict, step: int) -> dict:
        if isinstance(action, dict):
            action = action.get("name")
        nxt = dict(obs)
        spec = transition.get(action, {})
        for var, change in spec.items():
            if isinstance(change, dict):
                if "delta" in change:
                    nxt[var] = round(float(nxt.get(var, 0.0)) + float(change["delta"]), 6)
                elif "set" in change:
                    nxt[var] = change["set"]
        for var, sch in transition.get("exogenous", {}).items():
            schedule = sch.get("schedule", [])
            if 0 <= step < len(schedule):
                nxt[var] = schedule[step]
        return nxt

    # -- evaluation ----------------------------------------------------------
    def evaluate(self, contract: dict, process: dict, policy: Callable | None = None, policy_id: str = "oracle", seed: int = 0) -> dict:
        """Replay-evaluate one Episode binding.

        ``process["trace"]`` is the preregistered deterministic rollout
        (obs[0..H], actions_used[0..H-1], transition).  When a policy is given
        it must reproduce the trace actions (REPLAY_MISMATCH otherwise) and the
        transition function must reproduce the observations.
        """
        self.validate_contract(contract)
        trace = process["trace"]
        H = trace["horizon"]
        obs = trace["obs"]
        actions = trace["actions_used"]
        transition = trace.get("transition", {})
        if len(obs) != H + 1 or len(actions) != H:
            raise self.config.error("REPLAY_MISMATCH", f"trace dims wrong: obs {len(obs)} != {H+1} or actions {len(actions)} != {H}")
        for t in range(H):
            if policy is not None:
                action = policy(obs[t], t)
                if action != actions[t]:
                    raise self.config.error("REPLAY_MISMATCH", f"policy action {action!r} != preregistered {actions[t]!r} at t={t}")
            expected = self.apply_transition(obs[t], actions[t], transition, t + 1)
            if expected != obs[t + 1]:
                raise self.config.error(
                    "REPLAY_MISMATCH",
                    f"transition at t={t} produced {expected} but trace recorded {obs[t+1]}",
                )

        clauses = {c["clause_id"]: c for c in contract["clauses"]}
        priority = list(contract.get("priority_order", []))
        unknown_priority = [cid for cid in clauses if cid not in priority]
        priority = priority + sorted(unknown_priority)

        obligations: dict[str, dict] = {}
        for cid in clauses:
            obligations[cid] = {
                "triggered": False,
                "satisfied": False,
                "violated": False,
                "triggered_step": None,
                "satisfied_step": None,
                "deadline_step": None,
                "pending": False,
                "cooldown_until": -1,
            }
        invariant_violations: list[dict] = []
        soft_cost_total = 0.0
        released = False
        release_step = None
        goal_states: dict[str, dict] = {}

        for t in range(H):
            step = t + 1
            ctx = EvalContext(step, obs[t], obs[t + 1], actions[t], obligations)
            # 1) releases first (frozen priority rule: release wins)
            for cid in priority:
                clause = clauses[cid]
                if clause["kind"] != "release_condition":
                    continue
                if eval_expr(clause["formula"]["expr"], ctx) is TRUE:
                    released = True
                    release_step = step
                    break
            if released:
                break
            # 2) evaluate remaining clauses in priority order
            for cid in priority:
                clause = clauses[cid]
                kind = clause["kind"]
                formula = clause["formula"]
                state = obligations[cid]
                if kind == "hard_invariant":
                    result = eval_expr(formula, ctx)
                    if result is FALSE:
                        invariant_violations.append({"clause_id": cid, "step": step})
                elif kind == "conditional_obligation":
                    self._eval_conditional(clause, ctx, state, t, obligations, transition, obs, actions, contract)
                elif kind == "terminal_goal":
                    self._eval_terminal_goal(clause, ctx, state, goal_states, H)
                elif kind == "soft_cost":
                    if eval_expr(formula["trigger"], ctx) is TRUE:
                        soft_cost_total += formula["amount"]

        verdict = self._terminal_verdict(contract, obligations, goal_states, invariant_violations, released, H)
        replay_digest = self._replay_digest(contract, process, policy_id, seed, actions, verdict)
        return {
            "terminal_verdict": verdict,
            "released": released,
            "release_step": release_step,
            "invariant_violations": invariant_violations,
            "obligations": {
                cid: {k: v for k, v in state.items() if k != "cooldown_until"}
                for cid, state in sorted(obligations.items())
            },
            "terminal_goals": {cid: s for cid, s in sorted(goal_states.items())},
            "soft_cost_total": soft_cost_total,
            "replay_digest": replay_digest,
            "actions_used": list(actions),
        }

    def _eval_conditional(self, clause, ctx, state, t, obligations, transition, obs, actions, contract) -> None:
        formula = clause["formula"]
        cid = clause["clause_id"]
        if formula["op"] == "within":
            if state["satisfied"] or state["violated"]:
                return
            cooldown = clause.get("cooldown")
            if cooldown and cooldown.get("intervals", 0) > 0:
                if ctx.step <= state["cooldown_until"]:
                    if not state["triggered"]:
                        return
            if not state["triggered"]:
                if eval_expr(formula["trigger"], ctx) is TRUE:
                    state["triggered"] = True
                    state["triggered_step"] = t
                    state["deadline_step"] = t + formula["deadline_intervals"]
                    if cooldown and eval_expr(cooldown.get("trigger", {"op": "const", "value": False}), ctx) is TRUE:
                        state["cooldown_until"] = ctx.step + cooldown["intervals"]
            if state["triggered"]:
                result = eval_expr(formula["obligation"], ctx)
                if result is TRUE:
                    state["satisfied"] = True
                    state["satisfied_step"] = ctx.step
                elif result is MISSING:
                    return  # missing observation pauses the deadline clock
                elif ctx.step >= state["deadline_step"]:
                    state["violated"] = True
        elif formula["op"] == "persist_after":
            if state["satisfied"] or state["violated"]:
                return
            if not state["triggered"]:
                if eval_expr(formula["trigger"], ctx) is TRUE:
                    state["triggered"] = True
                    state["triggered_step"] = t
            elif state["triggered"]:
                result = eval_expr(formula["expr"], ctx)
                if result is TRUE:
                    state["satisfied"] = True
                elif result is MISSING:
                    return
                else:
                    state["violated"] = True

    def _eval_terminal_goal(self, clause, ctx, state, goal_states, H) -> None:
        formula = clause["formula"]
        cid = clause["clause_id"]
        if cid in goal_states:
            return
        deadline_step = formula["deadline_intervals"]
        result = eval_expr(formula["goal"], ctx)
        if result is TRUE:
            goal_states[cid] = {"status": "achieved", "achieved_step": ctx.step}
        elif result is MISSING:
            return
        elif ctx.step >= deadline_step:
            goal_states[cid] = {"status": "violated", "violated_step": ctx.step}

    def _terminal_verdict(self, contract, obligations, goal_states, invariant_violations, released, H) -> str:
        if released:
            return "released"
        if invariant_violations or any(s["violated"] for s in obligations.values()):
            return "violated_at_truncation"
        pending = [
            cid for cid, s in obligations.items()
            if s["triggered"] and not s["satisfied"] and not s["violated"]
        ]
        pending_goals = [cid for cid, s in goal_states.items() if s["status"] == "violated"]
        # a goal violated inside the window is a truncation failure
        if pending_goals:
            return "violated_at_truncation"
        open_goals = [
            cid for cid, s in goal_states.items() if s["status"] == "achieved"
        ]
        # terminal goals that never became satisfied and whose deadline is beyond
        # the window are pending
        clauses = {c["clause_id"]: c for c in contract["clauses"]}
        for cid, clause in clauses.items():
            if clause["kind"] == "terminal_goal" and cid not in goal_states:
                if clause["formula"]["deadline_intervals"] > H:
                    pending.append(cid)
        if pending:
            rule = contract.get("anti_gaming_rule", self.config.release_config["anti_gaming"]["default_rule"])
            if rule == "terminal_guard_band":
                return "censored_pending"
            if rule == "terminal_viability":
                recoverable = self._recoverable(obligations, goal_states)
                return "censored_pending" if recoverable else "violated_at_truncation"
        return "continues_beyond_window"

    @staticmethod
    def _recoverable(obligations: dict, goal_states: dict) -> bool:
        """terminal-viability check: an open obligation at truncation is
        recoverable when it has not already violated and no deadline has passed
        within the window; irrecoverable or already-doomed terminal states are
        failures and are classified upstream as violated_at_truncation."""
        if any(s["violated"] for s in obligations.values()):
            return False
        if any(s["status"] == "violated" for s in goal_states.values()):
            return False
        return True

    @staticmethod
    def _replay_digest(contract, process, policy_id, seed, actions, verdict) -> str:
        payload = {
            "contract_id": contract["id"],
            "process_id": process["id"],
            "policy_id": policy_id,
            "seed": seed,
            "actions": actions,
            "terminal_verdict": verdict,
        }
        return hashing.sha256_hex(hashing.canonical_bytes(payload))


def utility(verdict: dict, config=CONFIG) -> float:
    """Frozen bounded construction utility U_C(tau) in [0,1] (spec section 7).

    Discrete reference version: 1.0 when the trajectory is clean and the window
    ends in a non-violation state, otherwise 0.0.
    """
    tv = verdict["terminal_verdict"]
    if tv == "violated_at_truncation":
        return 0.0
    if verdict.get("invariant_violations"):
        return 0.0
    if any(s.get("violated") for s in verdict.get("obligations", {}).values()):
        return 0.0
    return 1.0
