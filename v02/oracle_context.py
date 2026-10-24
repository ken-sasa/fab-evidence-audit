"""Independent possible-world oracle for the v0.2 synthetic context pilot.

Only one fixed context and one configuration at a time can support a route.
Records describe consistency under that condition, not independent replication.
No production evaluator is imported.  Unknown Boolean requirements are completed
by exhaustive enumeration; no probabilities or real-world claims are inferred.
"""

from itertools import product


STATES = frozenset({"PASS", "FAIL", "UNKNOWN", "ERROR"})


def _nonempty_string(value, label):
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} must be a nonempty string")


def _validate(task):
    if not isinstance(task, dict):
        raise ValueError("task must be a dict")
    required = {"formula", "requirements", "configs", "target_context", "records"}
    if not required.issubset(task):
        raise ValueError("task is missing required fields")
    n = task["requirements"]
    if type(n) is not int or n < 1:
        raise ValueError("requirements must be a positive integer")
    configs = task["configs"]
    if not isinstance(configs, (list, tuple)) or not configs:
        raise ValueError("configs must be a nonempty list or tuple")
    for config in configs:
        _nonempty_string(config, "config_id")
    if len(set(configs)) != len(configs):
        raise ValueError("configuration IDs must be unique")
    _nonempty_string(task["target_context"], "target_context")
    active = set()

    def visit(node, depth=0):
        if depth > 64:
            raise ValueError("formula exceeds the supported validation depth")
        if type(node) is int:
            if not 0 <= node < n:
                raise ValueError("formula contains an out-of-range requirement")
            active.add(node)
            return
        if not isinstance(node, tuple) or len(node) < 3:
            raise ValueError("operator nodes must be tuples with >=2 children")
        if not isinstance(node[0], str) or node[0] not in {"AND", "OR"}:
            raise ValueError("only AND and OR are supported")
        for child in node[1:]:
            visit(child, depth + 1)

    visit(task["formula"])
    if not isinstance(task["records"], (list, tuple)):
        raise ValueError("records must be a list or tuple")
    record_ids = set()
    for record in task["records"]:
        if not isinstance(record, dict):
            raise ValueError("each record must be a dict")
        if not {"config_id", "context", "requirement", "result", "record_id"}.issubset(record):
            raise ValueError("record is missing required fields")
        _nonempty_string(record["config_id"], "record config_id")
        if record["config_id"] not in configs:
            raise ValueError("record references an undeclared configuration")
        _nonempty_string(record["context"], "record context")
        if type(record["requirement"]) is not int or not 0 <= record["requirement"] < n:
            raise ValueError("record references an out-of-range requirement")
        if not isinstance(record["result"], str) or record["result"] not in {"PASS", "FAIL", "ERROR"}:
            raise ValueError("invalid record result")
        _nonempty_string(record["record_id"], "record_id")
        if record["record_id"] in record_ids:
            raise ValueError("record IDs must be unique")
        record_ids.add(record["record_id"])
    return tuple(sorted(active)), tuple(configs)


def _fixed_condition_evidence(task, active, configs):
    """Select relevant rows, then classify evidence consistency per requirement."""
    observations = {config: {req: set() for req in active} for config in configs}
    for record in task["records"]:
        if record["context"] != task["target_context"]:
            continue
        if record["requirement"] not in active:
            continue
        observations[record["config_id"]][record["requirement"]].add(record["result"])
    classified = {}
    for config in configs:
        classified[config] = {}
        for req in active:
            values = observations[config][req]
            if not values:
                value = None
            elif values == {"PASS"}:
                value = True
            elif values == {"FAIL"}:
                value = False
            else:
                # ERROR alone, mixed ERROR, or contradictory PASS/FAIL evidence.
                value = "ERROR"
            classified[config][req] = value
    return classified


def _boolean(formula, assignment):
    if type(formula) is int:
        return assignment[formula]
    outcomes = [_boolean(child, assignment) for child in formula[1:]]
    return all(outcomes) if formula[0] == "AND" else any(outcomes)


def oracle(task):
    """Classify all Boolean completions with global active-error precedence."""
    active, configs = _validate(task)
    evidence = _fixed_condition_evidence(task, active, configs)
    if any(value == "ERROR" for row in evidence.values() for value in row.values()):
        return "ERROR"
    per_config = []
    for config in configs:
        row = evidence[config]
        unknown = tuple(req for req in active if row[req] is None)
        observed = set()
        for completion in product((False, True), repeat=len(unknown)):
            assignment = row.copy()
            assignment.update(zip(unknown, completion))
            observed.add(_boolean(task["formula"], assignment))
            if len(observed) == 2:
                break
        if observed == {True}:
            per_config.append("PASS")
        elif observed == {False}:
            per_config.append("FAIL")
        else:
            per_config.append("UNKNOWN")
    if "PASS" in per_config:
        return "PASS"
    return "FAIL" if all(state == "FAIL" for state in per_config) else "UNKNOWN"


def best_witness_exists(task):
    """Whether some one-configuration positive-evidence subset completes a route.

    Only uncontradicted PASS evidence in the target context can enter a witness.
    All other requirements are assigned False.  Every subset of supported
    requirements is enumerated, independently of oracle(task)'s final state.
    Thus a valid positive route may exist while an error elsewhere requires the
    overall audit result ERROR; this helper does not authorize ignoring it.
    """
    active, configs = _validate(task)
    evidence = _fixed_condition_evidence(task, active, configs)
    for config in configs:
        positive = tuple(req for req in active if evidence[config][req] is True)
        for subset in product((False, True), repeat=len(positive)):
            assignment = dict.fromkeys(active, False)
            assignment.update(zip(positive, subset))
            if _boolean(task["formula"], assignment):
                return True
    return False


def _self_test():
    def make(formula=("AND", 0, 1), configs=("A", "B"), observations=()):
        return {
            "formula": formula,
            "requirements": 3,
            "configs": list(configs),
            "target_context": "target",
            "records": [
                {"config_id": config, "context": context, "requirement": req,
                 "result": result, "record_id": f"record-{index}"}
                for index, (config, context, req, result) in enumerate(observations)
            ],
        }

    task = make(observations=(("A", "target", 0, "PASS"), ("A", "target", 1, "PASS")))
    assert oracle(task) == "PASS"
    assert best_witness_exists(task)
    task = make(observations=(("A", "target", 0, "PASS"), ("B", "target", 1, "PASS")))
    assert oracle(task) == "UNKNOWN"  # no complete configuration and missing data
    assert not best_witness_exists(task)
    task = make(configs=("A",), observations=(("A", "target", 0, "PASS"), ("A", "other", 1, "PASS")))
    assert oracle(task) == "UNKNOWN"
    assert not best_witness_exists(task)
    task = make(configs=("A",), observations=(("A", "target", 0, "PASS"), ("A", "target", 1, "FAIL"), ("A", "other", 1, "ERROR")))
    assert oracle(task) == "FAIL"
    task = make(configs=("A",), observations=(("A", "target", 0, "PASS"), ("A", "target", 1, "PASS"), ("A", "target", 2, "ERROR")))
    assert oracle(task) == "PASS"  # unreferenced requirement is inactive
    task = make(configs=("A",), observations=(("A", "target", 0, "PASS"), ("A", "target", 0, "FAIL")))
    assert oracle(task) == "ERROR"
    assert not best_witness_exists(task)
    task = make(formula=("OR", 0, 1), configs=("A",), observations=(("A", "target", 0, "PASS"), ("A", "target", 1, "ERROR")))
    assert oracle(task) == "ERROR"
    assert best_witness_exists(task)
    task = make(observations=(("A", "target", 0, "PASS"), ("A", "target", 1, "PASS"), ("B", "target", 0, "ERROR")))
    assert oracle(task) == "ERROR"
    assert best_witness_exists(task)
    task = make(formula=("AND", 0, ("OR", 0, 1)), configs=("A",), observations=(("A", "target", 1, "PASS"),))
    assert oracle(task) == "UNKNOWN"
    assert not best_witness_exists(task)
    task = make(configs=("A", "B"), observations=(("A", "target", 0, "PASS"), ("A", "target", 1, "FAIL"), ("B", "target", 0, "FAIL"), ("B", "target", 1, "PASS")))
    assert oracle(task) == "FAIL"
    assert not best_witness_exists(task)
    invalid = make()
    invalid["records"] = [{"config_id": "A", "context": "target", "requirement": True, "result": "PASS", "record_id": "bad"}]
    for fn in (oracle, best_witness_exists):
        try:
            fn(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("Malformed input was accepted")
    return 20


if __name__ == "__main__":
    print(f"v0.2 independent context oracle self-test passed ({_self_test()} cases).")
