"""Outcome-free, synthetic FAB-inspired logic prototype; not a production rater.

Expressions: ('AND', child, child, ...) / ('OR', child, child, ...).
Leaves are integer positions. Values are 0..4, None (unknown), or 'ERROR'.
All requirements are applicable and each system configuration stays separate.
"""
from __future__ import annotations

PASS, FAIL, UNKNOWN, ERROR = 'PASS', 'FAIL', 'UNKNOWN', 'ERROR'


def validate(formula, values, threshold):
    if type(threshold) is not int or not 1 <= threshold <= 4:
        raise ValueError('threshold must be an integer from 1 to 4')
    if not isinstance(values, (list, tuple)):
        raise ValueError('values must be a list or tuple')
    used = set()
    def visit(node, depth=0):
        if depth > 64:
            raise ValueError('maximum expression depth exceeded')
        if type(node) is int:
            if not 0 <= node < len(values):
                raise ValueError('missing or invalid leaf reference')
            used.add(node)
            return
        if not isinstance(node, tuple) or len(node) < 3 or node[0] not in ('AND', 'OR'):
            raise ValueError('invalid expression or operator')
        for child in node[1:]:
            visit(child, depth + 1)
    visit(formula)
    for i in used:
        value = values[i]
        if value is None or value == ERROR:
            continue
        if type(value) is not int or not 0 <= value <= 4:
            raise ValueError('invalid leaf value')
    return used


def evaluate(formula, values, threshold):
    """Strict four-state evaluation; input errors cannot hide behind success."""
    validate(formula, values, threshold)
    def visit(node):
        if type(node) is int:
            value = values[node]
            if value == ERROR:
                return ERROR
            if value is None:
                return UNKNOWN
            return PASS if value >= threshold else FAIL
        children = [visit(child) for child in node[1:]]
        if ERROR in children:
            return ERROR
        if node[0] == 'AND':
            return FAIL if FAIL in children else (PASS if all(x == PASS for x in children) else UNKNOWN)
        return PASS if PASS in children else (FAIL if all(x == FAIL for x in children) else UNKNOWN)
    return visit(formula)


def score(formula, values):
    """Known-value min/max score for a single, fixed system configuration."""
    if type(formula) is int:
        return values[formula]
    fn = min if formula[0] == 'AND' else max
    return fn(score(child, values) for child in formula[1:])


def zero_imputation(formula, values, threshold):
    fixed = [v if type(v) is int else 0 for v in values]
    return PASS if score(formula, fixed) >= threshold else FAIL


def mean_aggregation(formula, values, threshold):
    used = validate(formula, values, threshold)
    fixed = [values[i] if type(values[i]) is int else 0 for i in used]
    return PASS if sum(fixed) >= threshold * len(fixed) else FAIL


def short_circuit(formula, values, threshold):
    """Deliberately unsafe comparator: a decisive child can hide a later error."""
    def visit(node):
        if type(node) is int:
            value = values[node]
            if value == ERROR:
                return ERROR
            if value is None:
                return UNKNOWN
            return PASS if value >= threshold else FAIL
        states = []
        for child in node[1:]:
            result = visit(child)
            states.append(result)
            if node[0] == 'AND' and result == FAIL:
                return FAIL
            if node[0] == 'OR' and result == PASS:
                return PASS
        if ERROR in states:
            return ERROR
        return UNKNOWN if UNKNOWN in states else (PASS if node[0] == 'AND' else FAIL)
    return visit(formula)


FORMULAS = [
    ('AND2', ('AND', 0, 1), 2),
    ('OR2', ('OR', 0, 1), 2),
    ('AND3', ('AND', 0, 1, 2), 3),
    ('OR3', ('OR', 0, 1, 2), 3),
    ('AND_OR3', ('AND', ('OR', 0, 1), 2), 3),
    ('OR_AND3', ('OR', ('AND', 0, 1), 2), 3),
    ('AND4', ('AND', 0, 1, 2, 3), 4),
    ('OR4', ('OR', 0, 1, 2, 3), 4),
    ('AND_OR_OR4', ('AND', ('OR', 0, 1), ('OR', 2, 3)), 4),
    ('OR_AND_AND4', ('OR', ('AND', 0, 1), ('AND', 2, 3)), 4),
    ('NESTED_AND4', ('AND', 0, ('OR', 1, ('AND', 2, 3))), 4),
    ('NESTED_OR4', ('OR', 0, ('AND', 1, ('OR', 2, 3))), 4),
]
