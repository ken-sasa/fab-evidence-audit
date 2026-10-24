"""Reconstruct one evidence task from archived observations; run installed CLI.

This walkthrough executes no SQLite probe and changes no archived observation.
It accepts only an installed 0.1.1 distribution in an explicit target directory.
"""
import argparse
import copy
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import sys


STUDY = Path(__file__).resolve().parents[1]
VERSION = "0.1.1"
CONFIG = "strict"
CONTEXT = "before_begin"
FORMULA_ID = "both_with_unique"
TASK_ID = "complete-before_begin-strict-both_with_unique"
CONSTRAINTS = {
    0: ("SQLITE_CONSTRAINT_FOREIGNKEY", 787),
    1: ("SQLITE_CONSTRAINT_CHECK", 275),
    2: ("SQLITE_CONSTRAINT_UNIQUE", 2067),
}
RECORD_KEYS = ("config_id", "context", "requirement", "result", "record_id")
BOOTSTRAP = """import pathlib, sys
root = pathlib.Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root))
import fab_evidence_audit as package
if not pathlib.Path(package.__file__).resolve().is_relative_to(root):
    raise RuntimeError('refusing source or global-package fallback')
if package.__version__ != '0.1.1':
    raise RuntimeError('this walkthrough requires installed version 0.1.1')
from fab_evidence_audit.cli import main
raise SystemExit(main(['-']))
"""


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def require(condition, message):
    if not condition:
        raise ValueError(message)


def derive_result(row):
    """Use saved control/count/exception facts, not the saved result label."""
    req = row["requirement"]
    require(type(req) is int and req in CONSTRAINTS, "unexpected requirement")
    effective = row.get("effective", {})
    readback_ok = set(effective) == {"foreign_keys", "ignore_check_constraints"} and all(
        isinstance(value, list) and len(value) == 1
        and type(value[0]) is int and value[0] in (0, 1)
        for value in effective.values()
    )
    controls_ok = (
        not row.get("unexpected_exception") and not row.get("cleanup_exception")
        and readback_ok and row.get("in_transaction") is True
        and row.get("initial_count") == 0 and row.get("control_count") == 1
        and row.get("control_accepted") is True
        and row.get("control_row") == [1, 1, 1, "base"]
        and row.get("cleanup_count") == 0 and row.get("cleanup_transaction_active") is True
    )
    if not controls_ok:
        return "ERROR"
    if row.get("violating_write") == "accepted" and row.get("final_count") == 2:
        return "FAIL"
    name, code = CONSTRAINTS[req]
    if (row.get("violating_write") == "rejected" and row.get("final_count") == 1
            and row.get("exception_name") == name and row.get("exception_code") == code):
        return "PASS"
    return "ERROR"


def formula_true(node, supported):
    if type(node) is int:
        return node in supported
    values = [formula_true(child, supported) for child in node[1:]]
    if node[0] == "AND":
        return all(values)
    if node[0] == "OR":
        return any(values)
    raise ValueError("unsupported formula")


def active_leaves(node):
    if type(node) is int:
        return {node}
    return set().union(*(active_leaves(child) for child in node[1:]))


def valid_saved_witness(task, result, original_rows):
    """Inspect support against the source observations, without the evaluator."""
    if not isinstance(result, dict) or set(result) != {"state", "witness"} or result["state"] != "PASS":
        return False
    witness = result["witness"]
    if not isinstance(witness, dict) or set(witness) != {"config_id", "context", "record_ids"}:
        return False
    if witness["config_id"] not in task["configs"] or witness["context"] != task["target_context"]:
        return False
    ids = witness["record_ids"]
    if not isinstance(ids, list) or not ids or any(not isinstance(rid, str) for rid in ids) or len(ids) != len(set(ids)):
        return False
    original = {row["record_id"]: row for row in original_rows}
    records = {row["record_id"]: row for row in task["records"]}
    if len(original) != len(original_rows) or len(records) != len(task["records"]):
        return False
    active = active_leaves(task["formula"])
    cells = {}
    for row in task["records"]:
        source = original.get(row["record_id"])
        if source is None or any(row[key] != source[key] for key in RECORD_KEYS if key != "result"):
            return False
        if row["result"] != derive_result(source):
            return False
        if row["context"] == task["target_context"] and row["requirement"] in active:
            cells.setdefault((row["config_id"], row["requirement"]), set()).add(row["result"])
    if any("ERROR" in values or {"PASS", "FAIL"} <= values for values in cells.values()):
        return False
    supported = set()
    for rid in ids:
        if rid not in records or rid not in original:
            return False
        row = records[rid]
        if (row["config_id"] != witness["config_id"] or row["context"] != witness["context"]
                or row["result"] != "PASS" or row["requirement"] not in active):
            return False
        supported.add(row["requirement"])
    return formula_true(task["formula"], supported)


def installed_distribution(root):
    require(root.is_dir(), "installed-root is not a directory")
    matches = [dist for dist in importlib.metadata.distributions(path=[str(root)])
               if dist.metadata.get("Name", "").replace("_", "-").lower() == "fab-evidence-audit"]
    require(len(matches) == 1, "installed-root must contain exactly one installed fab-evidence-audit distribution")
    distribution = matches[0]
    require(distribution.version == VERSION, "installed distribution must be version " + VERSION)
    metadata_files = [distribution.locate_file(name).resolve() for name in (distribution.files or [])
                      if Path(name).name == "METADATA" and Path(name).parent.suffix == ".dist-info"]
    require(len(metadata_files) == 1 and metadata_files[0].is_relative_to(root),
            "pip-installed .dist-info metadata is required; source .egg-info is not an installation")
    for name in ("__init__.py", "api.py", "cli.py", "_core.py"):
        path = (root / "fab_evidence_audit" / name).resolve()
        require(path.is_file() and path.is_relative_to(root), "installed package escapes target or is incomplete")
    return distribution


def run(installed_root, archive, output):
    installed_root, archive, output = installed_root.resolve(), archive.resolve(), output.resolve()
    require(not output.exists(), "output directory already exists; choose a new path")
    distribution = installed_distribution(installed_root)
    source_names = ("observations.json", "conditions.json", "protocol_frozen.json", "tasks.jsonl", "predictions.json")
    source_hashes = {name: digest(archive / name) for name in source_names}
    observations = read_json(archive / "observations.json")
    conditions = read_json(archive / "conditions.json")
    protocol = read_json(archive / "protocol_frozen.json")
    formula = protocol["formulas"][FORMULA_ID]
    require(formula == ["AND", 0, 1, 2], "archived formula no longer matches this walkthrough")
    selected = [(index, row) for index, row in enumerate(observations) if row["config_id"] == CONFIG]
    require(len(selected) == 6, "expected both archived contexts, each with three strict-profile observations")
    records = []
    for _, row in selected:
        derived = derive_result(row)
        require(derived == row["result"], "raw facts disagree with stored result for " + row["record_id"])
        record = {key: row[key] for key in RECORD_KEYS}
        record["result"] = derived
        records.append(record)
    task = {"task_id": TASK_ID, "family": "complete", "formula_id": FORMULA_ID,
            "formula": formula, "requirements": 3, "configs": [CONFIG],
            "target_context": CONTEXT, "records": records}
    archived_tasks = [json.loads(line) for line in (archive / "tasks.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    matched = [item for item in archived_tasks if item["task_id"] == TASK_ID]
    require(len(matched) == 1 and task == matched[0], "raw reconstruction does not equal the archived query")
    saved_predictions = [item for item in read_json(archive / "predictions.json") if item["task_id"] == TASK_ID]
    require(len(saved_predictions) == 1 and saved_predictions[0]["gold"] == "PASS", "saved query is not the expected PASS control")
    command = [sys.executable, "-I", "-c", BOOTSTRAP, str(installed_root)]
    completed = subprocess.run(command, input=json.dumps(task), capture_output=True, text=True,
                               encoding="utf-8", check=False, timeout=30, cwd=installed_root)
    require(completed.returncode == 0 and completed.stderr == "", "installed CLI failed: " + completed.stderr)
    decision = json.loads(completed.stdout)
    require(valid_saved_witness(task, decision, observations), "CLI witness failed original-observation audit")
    require(len(decision["witness"]["record_ids"]) == 3, "expected a three-requirement support witness")
    require(decision["witness"] == saved_predictions[0]["witness"], "CLI witness differs from the saved control")
    forged = copy.deepcopy(decision)
    forged["witness"]["record_ids"][0] = "__walkthrough_forged_record__"
    require(not valid_saved_witness(task, forged, observations), "forged witness was not rejected")
    witness_ids = set(decision["witness"]["record_ids"])
    mapping = []
    for task_index, (source_index, row) in enumerate(selected):
        condition_matches = [(index, condition) for index, condition in enumerate(conditions)
                             if condition["config_id"] == row["config_id"] and condition["context"] == row["context"]]
        require(len(condition_matches) == 1, "condition mapping is not unique")
        condition_index, condition = condition_matches[0]
        mapping.append({"record_id": row["record_id"], "requirement": row["requirement"],
                        "requirement_text": protocol["requirements"][str(row["requirement"])],
                        "task_pointer": "/records/" + str(task_index),
                        "observation_file": "observations.json", "observation_pointer": "/" + str(source_index),
                        "condition_file": "conditions.json", "condition_pointer": "/" + str(condition_index),
                        "condition_id": condition["condition_id"], "requested_settings": condition["requested"],
                        "schema_sha256": condition["schema_sha256"], "derived_result": derive_result(row),
                        "in_target_context": row["context"] == CONTEXT, "in_witness": row["record_id"] in witness_ids,
                        "raw_observation": row})
    report = {"scope": "Reuse walkthrough over saved observations only; no new SQL measurement or independent sample.",
              "installed_version": distribution.version, "installed_root": str(installed_root),
              "isolated_cli_no_source_fallback": True, "cli_exit_code": completed.returncode,
              "source_archive": str(archive), "source_sha256": source_hashes,
              "walkthrough_source_sha256": digest(Path(__file__)),
              "installed_source_sha256": {name: digest(installed_root / "fab_evidence_audit" / name)
                                          for name in ("__init__.py", "api.py", "cli.py", "_core.py")},
              "task_id": TASK_ID, "raw_derived_records": len(records), "archived_task_exact_match": True,
              "decision": decision["state"], "witness_record_ids": decision["witness"]["record_ids"],
              "witness_matches_original_observations": True, "witness_matches_saved_control": True,
              "forged_witness_rejected": True,
              "limits": "Checks a source-to-record mapping and logical support, not source authenticity or a new SQLite run."}
    require(source_hashes == {name: digest(archive / name) for name in source_names}, "source archive changed during walkthrough")
    output.mkdir(parents=True, exist_ok=False)
    for name, value in (("task.json", task), ("decision.json", decision),
                        ("record_source_map.json", mapping), ("report.json", report)):
        (output / name).write_text(json.dumps(value, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return {"output": str(output), "installed_version": VERSION, "state": decision["state"],
            "witness_record_ids": decision["witness"]["record_ids"], "forged_witness_rejected": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installed-root", type=Path, required=True,
                        help="explicit pip --target installation of version 0.1.1; source fallback is forbidden")
    parser.add_argument("--archive", type=Path, default=STUDY / "results/sqlite_v0_3")
    parser.add_argument("--output", type=Path, required=True, help="new output directory")
    args = parser.parse_args()
    try:
        result = run(args.installed_root, args.archive, args.output)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"error": "WALKTHROUGH_FAILED", "message": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
