"""Validate a JSON task before invoking the preserved research evaluator.

Malformed input raises InvalidTask. When evaluation proceeds past the operational
preflight, explicit active evidence errors or contradictions return ERROR.
Capacity refusal, malformed input and evidence ERROR are different events.
Limits bound this reference implementation; it is not a hosted service.
"""
from . import _core

MAX_REQUIREMENTS = 16
MAX_CONFIGS = 64
MAX_RECORDS = 100000
MAX_FORMULA_NODES = 256
MAX_FORMULA_DEPTH = 24
MAX_IDENTIFIER_LENGTH = 256
MAX_SUPPORT_WORK = 1000000


class InvalidTask(ValueError):
    """Input does not satisfy the public task contract."""


class EvaluationLimit(RuntimeError):
    """Conservative support-work budget exceeded; no decision was evaluated."""


def _identifier(value, label):
    if not isinstance(value, str) or not value or len(value) > MAX_IDENTIFIER_LENGTH:
        raise InvalidTask(label + " must be a nonempty string of at most 256 characters")
    return value


def normalize(task):
    """Return a detached internal task; accept JSON arrays or Python tuples.

Optional JSON-serializable metadata is ignored for the decision. Mandatory
fields are validated without coercing numbers, booleans, labels or keys.
"""
    if not isinstance(task, dict):
        raise InvalidTask("task must be an object")
    required = {"formula", "requirements", "configs", "target_context", "records"}
    if not required <= task.keys():
        raise InvalidTask("missing task fields: " + ", ".join(sorted(required - task.keys())))
    n = task["requirements"]
    if type(n) is not int or not 1 <= n <= MAX_REQUIREMENTS:
        raise InvalidTask("requirements must be an integer from 1 to 16")
    nodes = 0

    def formula(node, depth=0):
        nonlocal nodes
        nodes += 1
        if nodes > MAX_FORMULA_NODES or depth > MAX_FORMULA_DEPTH:
            raise InvalidTask("formula exceeds the documented size or depth limit")
        if type(node) is int:
            if not 0 <= node < n:
                raise InvalidTask("formula leaf is outside requirement domain")
            return node
        if not isinstance(node, (list, tuple)) or len(node) < 3 or node[0] not in ("AND", "OR"):
            raise InvalidTask("formula must contain integer leaves or AND/OR with at least two children")
        return (node[0], *(formula(child, depth + 1) for child in node[1:]))

    parsed = formula(task["formula"])
    configs = task["configs"]
    if not isinstance(configs, (list, tuple)) or not 1 <= len(configs) <= MAX_CONFIGS:
        raise InvalidTask("configs must contain 1 to 64 identifiers")
    configs = [_identifier(c, "config identifier") for c in configs]
    if len(set(configs)) != len(configs):
        raise InvalidTask("config identifiers must be unique")
    context = _identifier(task["target_context"], "target_context")
    records = task["records"]
    if not isinstance(records, (list, tuple)) or len(records) > MAX_RECORDS:
        raise InvalidTask("records must be an array of at most 100000 records")
    ids, copied = set(), []
    keys = {"config_id", "context", "requirement", "result", "record_id"}
    for index, row in enumerate(records):
        if not isinstance(row, dict) or not keys <= row.keys():
            raise InvalidTask(f"record {index} must contain the five mandatory record fields")
        config = _identifier(row["config_id"], "record config_id")
        if config not in configs:
            raise InvalidTask(f"record {index} references an undeclared configuration")
        ctx = _identifier(row["context"], "record context")
        req = row["requirement"]
        if type(req) is not int or not 0 <= req < n:
            raise InvalidTask(f"record {index} requirement is outside domain")
        if not isinstance(row["result"], str) or row["result"] not in ("PASS", "FAIL", "ERROR"):
            raise InvalidTask(f"record {index} result must be PASS, FAIL or ERROR")
        rid = _identifier(row["record_id"], "record_id")
        if rid in ids:
            raise InvalidTask("record identifiers must be globally unique")
        ids.add(rid)
        copied.append({"config_id": config, "context": ctx, "requirement": req, "result": row["result"], "record_id": rid})
    return {"formula": parsed, "requirements": n, "configs": configs, "target_context": context, "records": copied}


def _preflight(task):
    """Bound candidate construction and pruning before calling the old core.

    For each node, assume every referenced leaf is positive: leaves contribute
    one candidate; OR adds child bounds and AND multiplies them. Charge each
    internal node its raw candidate bound plus its square (pairwise pruning),
    and sum child costs. A leaf costs one. Multiply by all configurations.
    Arithmetic saturates at budget + 1, so the estimator itself never constructs
    the Cartesian product. Deduplication and pruning can make the real work far
    smaller: valid tasks, including ones whose data would not produce PASS, can
    be stopped conservatively. This is not a time or memory guarantee.
    """
    cap = MAX_SUPPORT_WORK + 1

    def add(left, right):
        return cap if left >= cap - right else left + right

    def multiply(left, right):
        return cap if left and right > cap // left else left * right

    def bound(node):
        if type(node) is int:
            return 1, 1
        candidates = 0 if node[0] == "OR" else 1
        work = 0
        for child in node[1:]:
            child_candidates, child_work = bound(child)
            work = add(work, child_work)
            if node[0] == "OR":
                candidates = add(candidates, child_candidates)
            else:
                candidates = multiply(candidates, child_candidates)
        work = add(work, add(candidates, multiply(candidates, candidates)))
        return candidates, work

    _, per_configuration = bound(task["formula"])
    work = multiply(per_configuration, len(task["configs"]))
    if work > MAX_SUPPORT_WORK:
        raise EvaluationLimit(
            "conservative support-work estimate exceeds budget "
            + str(MAX_SUPPORT_WORK)
            + "; no evidence decision was evaluated"
        )


def audit(task):
    """Return {state, witness}; do not change the supplied object.

An active target-context ERROR in any declared configuration blocks the task,
including when another configuration has a successful route. Off-context errors
    are ignored. Missing cells remain unknown, not false. EvaluationLimit stops
    a task before any decision when its conservative support-work bound exceeds
    the budget. normalize() performs shape validation only. See docs/semantics.md.
"""
    t = normalize(task)
    _preflight(t)
    return _core.evaluate(t)


def check_witness(task, result):
    """Check a returned witness against the task, including its global policy.

This convenience checker shares the evaluator core. It is not the independent
    oracle used by the research audit, and does not establish source authenticity.
    The same EvaluationLimit preflight as audit() applies before verification.
"""
    t = normalize(task)
    _preflight(t)
    if not isinstance(result, dict) or set(result) != {"state", "witness"}:
        return False
    if result["state"] not in ("PASS", "FAIL", "UNKNOWN", "ERROR"):
        return False
    expected = _core.evaluate(t)
    if result["state"] != expected["state"]:
        return False
    w = result["witness"]
    if result["state"] != "PASS":
        return w is None
    if not isinstance(w, dict) or set(w) != {"config_id", "context", "record_ids"}:
        return False
    if w["config_id"] not in t["configs"] or w["context"] != t["target_context"]:
        return False
    ids = w["record_ids"]
    if not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids) or len(set(ids)) != len(ids):
        return False
    rows = {r["record_id"]: r for r in t["records"]}
    selected = []
    for rid in ids:
        if rid not in rows:
            return False
        r = rows[rid]
        if r["config_id"] != w["config_id"] or r["context"] != w["context"] or r["result"] != "PASS":
            return False
        selected.append(r)
    supported = {r["requirement"] for r in selected}
    return _core.truth(t["formula"], {i: i in supported for i in _core.leaves(t["formula"])})
