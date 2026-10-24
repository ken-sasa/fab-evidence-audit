"""The public checker accepts sufficient supports without imposing minimality."""
import unittest

from fab_evidence_audit import audit, check_witness


class WitnessContractTests(unittest.TestCase):
    def test_redundant_positive_support_is_valid_but_not_canonical(self):
        task = {
            "formula": ["OR", 0, 1], "requirements": 2, "configs": ["a"],
            "target_context": "target",
            "records": [
                {"record_id": "r0", "config_id": "a", "context": "target",
                 "requirement": 0, "result": "PASS"},
                {"record_id": "r1", "config_id": "a", "context": "target",
                 "requirement": 1, "result": "PASS"},
            ],
        }
        selected = audit(task)
        self.assertEqual(len(selected["witness"]["record_ids"]), 1)
        sufficient = {"state": "PASS", "witness": {
            "config_id": "a", "context": "target", "record_ids": ["r0", "r1"]}}
        self.assertTrue(check_witness(task, sufficient))


if __name__ == "__main__":
    unittest.main()
