"""Public-contract tests with explicit synthetic tasks and expected outcomes.

These tests inspect neither the internal evaluator nor participant data. They
exercise semantic boundaries and reject malformed/forged external inputs.
"""
import copy
import unittest

from fab_evidence_audit import InvalidTask, audit, check_witness, normalize
from fab_evidence_audit.api import (
    MAX_CONFIGS,
    MAX_FORMULA_DEPTH,
    MAX_FORMULA_NODES,
    MAX_IDENTIFIER_LENGTH,
    MAX_RECORDS,
    MAX_REQUIREMENTS,
)


def record(rid, requirement, result="PASS", config="a", context="target"):
    return {
        "record_id": rid,
        "config_id": config,
        "context": context,
        "requirement": requirement,
        "result": result,
    }


def task(records=(), formula=None, configs=("a",), requirements=2):
    return {
        "formula": ["AND", 0, 1] if formula is None else formula,
        "requirements": requirements,
        "configs": list(configs),
        "target_context": "target",
        "records": list(records),
    }


class DecisionTests(unittest.TestCase):
    def assert_state(self, given, state):
        result = audit(given)
        self.assertEqual(set(result), {"state", "witness"})
        self.assertEqual(result["state"], state)
        self.assertTrue(check_witness(given, result))
        if state != "PASS":
            self.assertIsNone(result["witness"])
        return result

    def test_all_four_states_are_distinct(self):
        cases = {
            "PASS": [record("p0", 0), record("p1", 1)],
            "FAIL": [record("f0", 0, "FAIL")],
            "UNKNOWN": [record("p0", 0)],
            "ERROR": [record("p0", 0), record("f0", 0, "FAIL")],
        }
        for expected, records in cases.items():
            with self.subTest(state=expected):
                self.assert_state(task(records), expected)

    def test_missing_records_are_unknown_not_failure(self):
        self.assert_state(task(), "UNKNOWN")

    def test_configurations_cannot_pool_partial_successes(self):
        self.assert_state(
            task([record("a0", 0), record("b1", 1, config="b")], configs=("a", "b")),
            "UNKNOWN",
        )

    def test_contexts_cannot_pool_partial_successes(self):
        self.assert_state(
            task([record("t0", 0), record("o1", 1, context="other")]),
            "UNKNOWN",
        )

    def test_one_complete_configuration_can_succeed(self):
        result = self.assert_state(
            task([record("a0", 0, "FAIL"), record("b0", 0, config="b"),
                  record("b1", 1, config="b")], configs=("a", "b")),
            "PASS",
        )
        self.assertEqual(result["witness"]["config_id"], "b")
        self.assertEqual(set(result["witness"]["record_ids"]), {"b0", "b1"})

    def test_one_failed_and_one_unknown_configuration_is_unknown(self):
        self.assert_state(task([record("a0", 0, "FAIL")], configs=("a", "b")), "UNKNOWN")

    def test_all_alternative_configurations_must_fail_for_failure(self):
        self.assert_state(
            task([record("a0", 0, "FAIL"), record("b1", 1, "FAIL", config="b")],
                 configs=("a", "b")),
            "FAIL",
        )

    def test_error_in_other_configuration_overrides_complete_success(self):
        for extra in ([record("be", 0, "ERROR", config="b")],
                      [record("bp", 0, config="b"), record("bf", 0, "FAIL", config="b")]):
            with self.subTest(extra=extra):
                self.assert_state(
                    task([record("a0", 0), record("a1", 1)] + extra, configs=("a", "b")),
                    "ERROR",
                )

    def test_error_in_alternative_or_branch_overrides_success(self):
        self.assert_state(
            task([record("p", 0), record("e", 1, "ERROR")], formula=["OR", 0, 1]),
            "ERROR",
        )

    def test_off_context_errors_and_contradictions_are_ignored(self):
        rows = [record("p0", 0), record("p1", 1), record("oe", 0, "ERROR", context="other"),
                record("op", 1, context="other"), record("of", 1, "FAIL", context="other")]
        self.assert_state(task(rows), "PASS")

    def test_inactive_requirement_error_is_ignored(self):
        self.assert_state(task([record("p", 0), record("e", 1, "ERROR")], formula=0), "PASS")

    def test_or_can_succeed_while_other_leaf_is_unknown(self):
        result = self.assert_state(task([record("p", 0)], formula=["OR", 0, 1]), "PASS")
        self.assertEqual(result["witness"]["record_ids"], ["p"])

    def test_or_failure_and_missing_is_unknown(self):
        self.assert_state(task([record("f", 0, "FAIL")], formula=["OR", 0, 1]), "UNKNOWN")

    def test_repeated_leaf_reuses_one_record(self):
        result = self.assert_state(
            task([record("once", 0)], formula=["AND", 0, ["OR", 0, 1]]), "PASS"
        )
        self.assertEqual(result["witness"]["record_ids"], ["once"])

    def test_different_consistent_records_for_same_cell_are_allowed(self):
        self.assert_state(task([record("p", 0), record("another", 0)], formula=0), "PASS")

    def test_input_is_unchanged_and_normalization_is_detached(self):
        given = task([record("p0", 0), record("p1", 1)])
        given["metadata"] = {"nested": ["untouched"]}
        given["records"][0]["metadata"] = ["ignored"]
        original = copy.deepcopy(given)
        result = audit(given)
        self.assertTrue(check_witness(given, result))
        detached = normalize(given)
        detached["records"][0]["result"] = "FAIL"
        detached["configs"].append("new")
        self.assertEqual(given, original)

    def test_python_tuple_inputs_are_accepted(self):
        given = task([record("p0", 0), record("p1", 1)], formula=("AND", 0, 1))
        given["configs"] = ("a",)
        given["records"] = tuple(given["records"])
        self.assert_state(given, "PASS")


class WitnessTests(unittest.TestCase):
    def setUp(self):
        self.given = task([
            record("a0", 0), record("a1", 1),
            record("b0", 0, config="b"), record("off1", 1, context="other"),
            record("inactive_fail", 2, "FAIL"),
        ], configs=("a", "b"), requirements=3)

    def test_valid_generated_witness(self):
        result = audit(self.given)
        self.assertEqual(result["state"], "PASS")
        self.assertTrue(check_witness(self.given, result))

    def test_forged_witnesses_are_rejected(self):
        good = audit(self.given)
        variants = []
        for patch in ({"config_id": "b"}, {"context": "other"},
                      {"record_ids": []}, {"record_ids": ["missing"]},
                      {"record_ids": ["a0"]}, {"record_ids": ["a0", "b0"]},
                      {"record_ids": ["a0", "off1"]},
                      {"record_ids": ["a0", "a1", "inactive_fail"]},
                      {"record_ids": ["a0", "a1", "a1"]},
                      {"record_ids": ["a0", 1]}, {"record_ids": ("a0", "a1")}):
            forged = copy.deepcopy(good)
            forged["witness"].update(patch)
            variants.append(forged)
        variants += [None, {}, {"state": "PASS", "witness": None},
                     {"state": "UNKNOWN", "witness": None},
                     {"state": "PASS", "witness": good["witness"], "extra": True}]
        missing_key = copy.deepcopy(good)
        del missing_key["witness"]["context"]
        variants.append(missing_key)
        for forged in variants:
            with self.subTest(result=forged):
                self.assertFalse(check_witness(self.given, forged))

    def test_nonpass_result_requires_null_witness(self):
        for rows in ([], [record("f", 0, "FAIL")], [record("e", 0, "ERROR")]):
            given = task(rows)
            result = audit(given)
            self.assertTrue(check_witness(given, result))
            result["witness"] = {"config_id": "a", "context": "target", "record_ids": ["x"]}
            self.assertFalse(check_witness(given, result))

    def test_success_witness_does_not_override_global_error(self):
        result = audit(self.given)
        self.given["records"].append(record("be", 1, "ERROR", config="b"))
        self.assertFalse(check_witness(self.given, result))


class ValidationTests(unittest.TestCase):
    def assert_invalid(self, given):
        with self.assertRaises(InvalidTask):
            audit(given)

    def test_duplicate_record_id_is_invalid_even_across_contexts(self):
        self.assert_invalid(task([record("dup", 0), record("dup", 1, context="other")]))

    def test_all_mandatory_task_and_record_keys_are_required(self):
        base = task([record("r", 0)])
        for key in base:
            given = copy.deepcopy(base)
            del given[key]
            with self.subTest(missing_task_key=key):
                self.assert_invalid(given)
        for key in base["records"][0]:
            given = copy.deepcopy(base)
            del given["records"][0][key]
            with self.subTest(missing_record_key=key):
                self.assert_invalid(given)

    def test_task_types_are_not_coerced(self):
        for given in (None, [], "{}", 0, True):
            with self.subTest(task=given):
                self.assert_invalid(given)
        for key, values in {
            "requirements": [True, False, 0, -1, MAX_REQUIREMENTS + 1, "2", 2.0, None],
            "configs": ["a", {}, [], ["a", "a"], [True], [""], [None]],
            "target_context": [None, True, 0, [], ""],
            "records": [None, {}, "rows", [None]],
        }.items():
            for value in values:
                given = task()
                given[key] = value
                with self.subTest(key=key, value=value):
                    self.assert_invalid(given)

    def test_bad_record_types_and_cross_references_are_invalid(self):
        for key, values in {
            "requirement": [True, False, -1, 2, 0.0, "0", None],
            "config_id": ["not-declared", True, 0, "", None],
            "context": ["", 0, True, None],
            "record_id": ["", True, 1, None],
            "result": ["pass", "UNKNOWN", True, 0, [], None],
        }.items():
            for value in values:
                given = task([record("r", 0)])
                given["records"][0][key] = value
                with self.subTest(key=key, value=value):
                    self.assert_invalid(given)

    def test_boolean_leaves_and_malformed_formulas_are_rejected(self):
        for formula in (True, False, "0", 0.0, -1, 2, None, {}, [], ["NOT", 0, 1],
                        ["AND", 0], ["AND"], ["OR", 0, True], ["AND", 0, "1"]):
            given = task()
            given["formula"] = formula
            with self.subTest(formula=formula):
                self.assert_invalid(given)

    def test_identifier_maximum_is_inclusive(self):
        identifier = "x" * MAX_IDENTIFIER_LENGTH
        given = task([record(identifier, 0, config=identifier, context=identifier)],
                     formula=0, configs=(identifier,))
        given["target_context"] = identifier
        self.assertEqual(audit(given)["state"], "PASS")
        for field in ("config", "context", "record"):
            invalid = copy.deepcopy(given)
            if field == "config":
                invalid["configs"] = [identifier + "x"]
            elif field == "context":
                invalid["target_context"] = identifier + "x"
            else:
                invalid["records"][0]["record_id"] = identifier + "x"
            with self.subTest(field=field):
                self.assert_invalid(invalid)

    def test_requirement_and_configuration_limits(self):
        given = task(formula=MAX_REQUIREMENTS - 1,
                     configs=tuple(str(i) for i in range(MAX_CONFIGS)),
                     requirements=MAX_REQUIREMENTS)
        self.assertEqual(audit(given)["state"], "UNKNOWN")
        given["configs"].append("overflow")
        self.assert_invalid(given)

    def test_formula_node_limit_is_inclusive(self):
        given = task(formula=["OR"] + [0] * (MAX_FORMULA_NODES - 1))
        self.assertEqual(audit(given)["state"], "UNKNOWN")
        given["formula"].append(0)
        self.assert_invalid(given)

    def test_formula_depth_limit_is_inclusive(self):
        formula = 0
        for _ in range(MAX_FORMULA_DEPTH):
            formula = ["AND", formula, 0]
        given = task(formula=formula)
        self.assertEqual(audit(given)["state"], "UNKNOWN")
        given["formula"] = ["AND", formula, 0]
        self.assert_invalid(given)

    def test_record_count_overflow_is_rejected_before_interpreting_rows(self):
        given = task()
        given["records"] = [None] * (MAX_RECORDS + 1)
        self.assert_invalid(given)


if __name__ == "__main__":
    unittest.main()
