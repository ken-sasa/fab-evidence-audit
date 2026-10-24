"""Subprocess tests of the portable JSON interface and exit-code contract."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


def task(state="UNKNOWN"):
    records = []
    outcomes = {"UNKNOWN": [], "PASS": ["PASS"], "FAIL": ["FAIL"], "ERROR": ["ERROR"]}
    for index, outcome in enumerate(outcomes[state]):
        records.append({"record_id": f"r{index}", "config_id": "a", "context": "target",
                        "requirement": 0, "result": outcome})
    return {"formula": 0, "requirements": 1, "configs": ["a"],
            "target_context": "target", "records": records}


class CliTests(unittest.TestCase):
    def run_cli(self, raw="", *args):
        env = os.environ.copy()
        src = str(Path(__file__).resolve().parents[1] / "src")
        env["PYTHONPATH"] = src + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        return subprocess.run([sys.executable, "-m", "fab_evidence_audit", *args],
                              input=raw, text=True, encoding="utf-8", capture_output=True,
                              env=env, timeout=15, check=False)

    def assert_invalid_json_diagnostic(self, result):
        self.assertEqual(result.returncode, 2, result)
        self.assertEqual(result.stdout, "")
        diagnostic = json.loads(result.stderr)
        self.assertEqual(set(diagnostic), {"error", "message"})
        self.assertEqual(diagnostic["error"], "INVALID_INPUT")
        self.assertIsInstance(diagnostic["message"], str)
        self.assertTrue(diagnostic["message"])
        self.assertNotIn("Traceback", result.stderr)

    def test_valid_decisions_all_exit_zero(self):
        for state in ("PASS", "FAIL", "UNKNOWN", "ERROR"):
            with self.subTest(state=state):
                completed = self.run_cli(json.dumps(task(state)))
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(completed.stderr, "")
                result = json.loads(completed.stdout)
                self.assertEqual(result["state"], state)
                self.assertEqual(set(result), {"state", "witness"})
                if state != "PASS":
                    self.assertIsNone(result["witness"])

    def test_explicit_stdin_marker(self):
        completed = self.run_cli(json.dumps(task("PASS")), "-")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["state"], "PASS")

    def test_json_file_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "task with spaces.json"
            path.write_text(json.dumps(task("PASS")), encoding="utf-8")
            completed = self.run_cli("not read when a file is given", str(path))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["state"], "PASS")

    def test_invalid_json(self):
        for raw in ("", "{", '{"formula": 0,}', "{}{}", "[]", "null", "{}"):
            with self.subTest(raw=raw):
                self.assert_invalid_json_diagnostic(self.run_cli(raw))

    def test_duplicate_json_keys_at_top_and_nested_levels(self):
        raw = json.dumps(task("PASS"))
        cases = [raw[:-1] + ', "requirements": 1}',
                 raw.replace('"result": "PASS"', '"result": "PASS", "result": "FAIL"'),
                 raw[:-1] + ', "metadata": {"tag": 1, "tag": 2}}']
        for duplicate in cases:
            with self.subTest(raw=duplicate):
                result = self.run_cli(duplicate)
                self.assert_invalid_json_diagnostic(result)
                self.assertIn("duplicate JSON object key", json.loads(result.stderr)["message"])

    def test_nonstandard_constants_are_rejected_even_in_ignored_metadata(self):
        raw = json.dumps(task())
        for constant in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(constant=constant):
                result = self.run_cli(raw[:-1] + ', "metadata": ' + constant + '}')
                self.assert_invalid_json_diagnostic(result)
                self.assertIn("nonstandard JSON numeric constant", json.loads(result.stderr)["message"])

    def test_missing_file_has_json_stderr(self):
        with tempfile.TemporaryDirectory() as directory:
            result = self.run_cli("", str(Path(directory) / "missing.json"))
        self.assert_invalid_json_diagnostic(result)

    def test_invalid_utf8_file_has_json_stderr(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_bytes(b"\xff")
            result = self.run_cli("", str(path))
        self.assert_invalid_json_diagnostic(result)

    def test_invalid_flag_uses_conventional_argument_error(self):
        result = self.run_cli("", "--not-a-real-flag")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage:", result.stderr)


if __name__ == "__main__":
    unittest.main()
