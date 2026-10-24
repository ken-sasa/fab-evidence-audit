"""Independent, exhaustive reference oracle for the FAB logic development pilot.

This module does not implement or certify the registered FAB production workflow.
It evaluates fixed, fully applicable monotone AND/OR formulas on artificial data.
Unlike a compositional four-state evaluator, it substitutes every completion of
the distinct unknown leaves and enumerates the resulting Boolean outcomes.

Formula grammar: an integer leaf index, or ("AND"|"OR", child, child, ...).
Leaf values: integer T in 0..4, None for an unknown T, or the literal "ERROR".
Thresholds: integers in 1..4.  Boolean values are not accepted as integers.
Only referenced leaves are active.  Any referenced ERROR dominates even when
another OR branch would succeed.  Repeated references to one leaf share a value.
"""

from collections.abc import Mapping, Sequence
from itertools import product


STATES = frozenset(("PASS", "FAIL", "UNKNOWN", "ERROR"))


def _leaf_indices(formula):
    """Validate the expression shape and return the distinct referenced indices."""
    indices = set()

    def visit(node):
        if type(node) is int:
            if node < 0:
                raise ValueError("Leaf indices must be nonnegative integers")
            indices.add(node)
            return
        if not isinstance(node, tuple) or len(node) < 3:
            raise ValueError("An internal node must be an operator and >=2 children")
        if node[0] not in ("AND", "OR"):
            raise ValueError("Only AND and OR operators are supported")
        for child in node[1:]:
            visit(child)

    visit(formula)
    return tuple(sorted(indices))


def _read_values(values, indices):
    if not isinstance(values, (Mapping, Sequence)) or isinstance(values, (str, bytes)):
        raise TypeError("values must be a sequence or a mapping indexed by leaf IDs")
    selected = {}
    for index in indices:
        try:
            value = values[index]
        except (KeyError, IndexError) as exc:
            raise ValueError(f"Missing value for referenced leaf {index}") from exc
        if value is None or value == "ERROR":
            selected[index] = value
        elif type(value) is int and 0 <= value <= 4:
            selected[index] = value
        else:
            raise ValueError(f"Invalid value for leaf {index}: {value!r}")
    return selected


def _boolean_evaluate(formula, completion, threshold):
    if type(formula) is int:
        return completion[formula] >= threshold
    # Lists are materialized before aggregation: no expression branch is skipped.
    children = [
        _boolean_evaluate(child, completion, threshold) for child in formula[1:]
    ]
    if formula[0] == "AND":
        return all(children)
    return any(children)


def oracle(formula, values, threshold):
    """Return PASS/FAIL/UNKNOWN/ERROR by exhaustive numeric completions.

    PASS means every completion meets the threshold, FAIL means none does, and
    UNKNOWN means some do and some do not.  The unknown leaf domain is exactly
    {0,1,2,3,4}; this is not a probabilistic imputation.  No real-world event or
    evidence rating is inferred.  Malformed expressions or values raise an
    exception rather than being silently converted to an experimental state.
    """
    if type(threshold) is not int or not 1 <= threshold <= 4:
        raise ValueError("threshold must be an integer in 1..4")
    indices = _leaf_indices(formula)
    selected = _read_values(values, indices)
    if any(value == "ERROR" for value in selected.values()):
        return "ERROR"
    unknowns = tuple(index for index in indices if selected[index] is None)
    outcomes = set()
    for replacement in product(range(5), repeat=len(unknowns)):
        completion = selected.copy()
        completion.update(zip(unknowns, replacement))
        outcomes.add(_boolean_evaluate(formula, completion, threshold))
        if len(outcomes) == 2:
            return "UNKNOWN"
    return "PASS" if True in outcomes else "FAIL"


def cross_system_oracle(formula, systems, threshold):
    """Evaluate complete systems first, then take the OR over whole systems.

    The same system must satisfy an entire permitted route.  A maximum over
    individual leaves from different systems is never taken.  Referenced errors
    dominate across systems, matching this pilot's global audit-error rule.
    Candidate absence is outside this model and therefore raises ValueError.
    """
    rows = list(systems)
    if not rows:
        raise ValueError("At least one system is required; absence is outside the pilot")
    states = [oracle(formula, values, threshold) for values in rows]
    if "ERROR" in states:
        return "ERROR"
    if "PASS" in states:
        return "PASS"
    if all(state == "FAIL" for state in states):
        return "FAIL"
    return "UNKNOWN"


def _self_test():
    conjunction = ("AND", 0, 1)
    disjunction = ("OR", 0, 1)
    assert oracle(conjunction, [4, 2], 3) == "FAIL"
    assert oracle(conjunction, [4, None], 3) == "UNKNOWN"
    assert oracle(conjunction, [0, None], 3) == "FAIL"
    assert oracle(disjunction, [4, None], 3) == "PASS"
    assert oracle(disjunction, [4, "ERROR"], 3) == "ERROR"
    assert oracle(conjunction, [4, 4, "ERROR"], 3) == "PASS"  # unreferenced
    assert oracle(("AND", 0, ("OR", 0, 1)), [None, 4], 3) == "UNKNOWN"
    systems = [[4, 0], [0, 4]]
    assert cross_system_oracle(conjunction, systems, 3) == "FAIL"
    assert oracle(conjunction, [4, 4], 3) == "PASS"  # fictitious mixed system
    assert cross_system_oracle(conjunction, [[4, 4], [None, 0]], 3) == "PASS"
    assert cross_system_oracle(conjunction, [[4, 4], ["ERROR", 4]], 3) == "ERROR"
    for invalid in (True, 0, 5, "3"):
        try:
            oracle(conjunction, [4, 4], invalid)
        except ValueError:
            pass
        else:
            raise AssertionError("Invalid threshold was accepted")
    return 15


if __name__ == "__main__":
    count = _self_test()
    print(f"Independent oracle self-test passed ({count} assertions/cases).")
