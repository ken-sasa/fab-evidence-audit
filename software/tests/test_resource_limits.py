"""Small regression checks for operational limits and portable JSON output.

The explosive expression is only normalized and inspected by the preflight.
Tests mock the preserved evaluator to prohibit materializing its support sets.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from fab_evidence_audit import EvaluationLimit, InvalidTask, audit, check_witness, normalize


def row(index, result="PASS"):
    return {"config_id": "a", "context": "target", "requirement": index,
            "result": result, "record_id": "r" + str(index)}


def task(formula=0, records=None, requirements=1):
    return {"formula": formula, "requirements": requirements, "configs": ["a"],
            "target_context": "target", "records": [row(0)] if records is None else records}


def explosive_task():
    group = ["AND"] + [["OR", i, i + 1] for i in range(0, 16, 2)]
    return task(["AND", group, group, group, group], [row(i) for i in range(16)], 16)


class ResourceLimitTests(unittest.TestCase):
    def test_explosive_valid_task_stops_before_core(self):
        given = explosive_task()
        self.assertIsInstance(normalize(given), dict)
        with patch("fab_evidence_audit.api._core.evaluate", side_effect=AssertionError("core must not run")) as core:
            with self.assertRaises(EvaluationLimit):
                audit(given)
            core.assert_not_called()

    def test_check_witness_also_stops_before_core(self):
        with patch("fab_evidence_audit.api._core.evaluate", side_effect=AssertionError("core must not run")) as core:
            with self.assertRaises(EvaluationLimit):
                check_witness(explosive_task(), {"state": "PASS", "witness": None})
            core.assert_not_called()

    def test_budget_boundary_is_inclusive(self):
        # OR(0,1): two leaf costs + two raw candidates + four comparisons = 8.
        given = task(["OR", 0, 1], [row(0)], 2)
        with patch("fab_evidence_audit.api.MAX_SUPPORT_WORK", 8):
            self.assertEqual(audit(given)["state"], "PASS")
        with patch("fab_evidence_audit.api.MAX_SUPPORT_WORK", 7):
            with patch("fab_evidence_audit.api._core.evaluate") as core:
                with self.assertRaises(EvaluationLimit):
                    audit(given)
                core.assert_not_called()

    def test_budget_accounts_for_all_configurations(self):
        given = task(["OR", 0, 1], [row(0)], 2)
        given["configs"] = ["a", "b"]
        with patch("fab_evidence_audit.api.MAX_SUPPORT_WORK", 15):
            with self.assertRaises(EvaluationLimit):
                audit(given)
        with patch("fab_evidence_audit.api.MAX_SUPPORT_WORK", 16):
            self.assertEqual(audit(given)["state"], "PASS")

    def test_preflight_is_syntactic_even_when_evidence_is_missing(self):
        given = explosive_task()
        given["records"] = []
        with patch("fab_evidence_audit.api._core.evaluate") as core:
            with self.assertRaises(EvaluationLimit):
                audit(given)
            core.assert_not_called()

    def test_malformed_task_is_invalid_before_resource_estimation(self):
        given = explosive_task()
        given["requirements"] = True
        with self.assertRaises(InvalidTask):
            audit(given)

    def test_small_repeated_subformula_remains_supported(self):
        group = ["AND", ["OR", 0, 1], ["OR", 2, 3]]
        given = task(["AND", group, group], [row(0), row(2)], 4)
        result = audit(given)
        self.assertEqual(result["state"], "PASS")
        self.assertTrue(check_witness(given, result))
        self.assertEqual(set(result["witness"]["record_ids"]), {"r0", "r2"})

    def test_small_fixture_decisions_are_unchanged(self):
        for expected, rows in (("PASS", [row(0)]), ("FAIL", [row(0, "FAIL")]),
                               ("UNKNOWN", []), ("ERROR", [row(0, "ERROR")])):
            with self.subTest(state=expected):
                given = task(records=rows)
                result = audit(given)
                self.assertEqual(result["state"], expected)
                self.assertTrue(check_witness(given, result))


class PortableCliTests(unittest.TestCase):
    def run_cli(self, raw):
        env = os.environ.copy()
        src = str(Path(__file__).resolve().parents[1] / "src")
        env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env["PYTHONIOENCODING"] = "utf-8:strict"
        return subprocess.run([sys.executable, "-m", "fab_evidence_audit"], input=raw,
                              encoding="utf-8", text=True, capture_output=True, env=env,
                              check=False, timeout=10)

    def test_resource_limit_has_distinct_exit_and_no_decision(self):
        result = self.run_cli(json.dumps(explosive_task()))
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertEqual(result.stdout, "")
        diagnostic = json.loads(result.stderr)
        self.assertEqual(diagnostic["error"], "EVALUATION_LIMIT")
        self.assertTrue(diagnostic["message"])
        self.assertNotIn("Traceback", result.stderr)

    def test_nonascii_identifiers_round_trip(self):
        given = task()
        given["configs"] = ["構成α"]
        given["target_context"] = "条件β"
        given["records"][0].update(config_id="構成α", context="条件β", record_id="証拠γ")
        result = self.run_cli(json.dumps(given))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        witness = json.loads(result.stdout)["witness"]
        self.assertEqual(witness, {"config_id": "構成α", "context": "条件β", "record_ids": ["証拠γ"]})

    def test_escaped_lone_surrogate_identifier_round_trips(self):
        given = task()
        given["records"][0]["record_id"] = "\ud800"
        result = self.run_cli(json.dumps(given))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout)["witness"]["record_ids"], ["\ud800"])

    def test_surrogate_in_duplicate_key_error_is_json(self):
        result = self.run_cli('{"\\ud800": 1, "\\ud800": 2}')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, "")
        diagnostic = json.loads(result.stderr)
        self.assertEqual(diagnostic["error"], "INVALID_INPUT")
        self.assertIn("\ud800", diagnostic["message"])


if __name__ == "__main__":
    unittest.main()
