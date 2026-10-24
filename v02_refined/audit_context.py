"""Post-run audit of archived v0.2-refined tasks, predictions and witnesses.

The production evaluator and generator are never imported or rerun. Gold labels
are independently recomputed with the exhaustive-completion reference oracle.
Saved proof rows are checked directly against archived records and the formula.
Metamorphic variants were not archived and are therefore only reported, not
independently re-executed or certified by this audit.
"""

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True
from oracle_context import oracle, best_witness_exists


STATES = {"PASS", "FAIL", "UNKNOWN", "ERROR"}
METAMORPHIC = (
    "row_config_order", "offcontext_invariance", "duplicate_invariance",
    "record_id_renaming",
)


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def expression_tuple(value):
    if type(value) is int:
        return value
    if not isinstance(value, (list, tuple)) or len(value) < 3:
        raise ValueError("Invalid serialized formula")
    return (value[0], *(expression_tuple(child) for child in value[1:]))


def active_requirements(formula):
    if type(formula) is int:
        return {formula}
    result = set()
    for child in formula[1:]:
        result.update(active_requirements(child))
    return result


def boolean_formula(formula, supported):
    if type(formula) is int:
        return formula in supported
    child = [boolean_formula(value, supported) for value in formula[1:]]
    if formula[0] == "AND":
        return all(child)
    if formula[0] == "OR":
        return any(child)
    raise ValueError("Invalid formula operator")


def check_witness(task, witness):
    if witness is None:
        return False
    if witness.get("config_id") not in task["configs"]:
        return False
    if witness.get("context") != task["target_context"]:
        return False
    identifiers = witness.get("record_ids")
    if not isinstance(identifiers, list) or not identifiers:
        return False
    if any(not isinstance(item, str) for item in identifiers) or len(set(identifiers)) != len(identifiers):
        return False
    by_id = {row["record_id"]: row for row in task["records"]}
    active = active_requirements(task["formula"])
    supported = set()
    for identifier in identifiers:
        row = by_id.get(identifier)
        if row is None:
            return False
        if row["config_id"] != witness["config_id"] or row["context"] != task["target_context"]:
            return False
        if row["result"] != "PASS" or row["requirement"] not in active:
            return False
        # A nominal PASS cannot witness a cell with conflicting/error evidence.
        cell_results = {
            other["result"] for other in task["records"]
            if other["config_id"] == row["config_id"]
            and other["context"] == row["context"]
            and other["requirement"] == row["requirement"]
        }
        if cell_results != {"PASS"}:
            return False
        supported.add(row["requirement"])
    return boolean_formula(task["formula"], supported)


def metrics_increment(counter, gold, answer):
    counter["total"] += 1
    counter["correct"] += answer == gold
    counter["false_pass"] += gold == "FAIL" and answer == "PASS"
    counter["unsupported_pass"] += gold == "UNKNOWN" and answer == "PASS"
    counter["unknown_forced_fail"] += gold == "UNKNOWN" and answer == "FAIL"
    counter["masked_error"] += gold == "ERROR" and answer != "ERROR"
    counter["excess_error"] += gold != "ERROR" and answer == "ERROR"
    counter["missed_pass"] += gold == "PASS" and answer != "PASS"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    folder = args.results.resolve()
    output = args.output.resolve() if args.output else folder / "audit_report.json"
    code_root = Path(__file__).resolve().parent
    study_root = code_root.parent
    source_names = (
        "protocol_frozen.json", "summary.json", "tasks.jsonl.gz",
        "predictions.csv.gz", "witnesses.jsonl", "confusion.csv",
    )
    before = {name: sha(folder / name) for name in source_names}
    report = {
        "audit_kind": "post_run_independent_oracle_and_archived_witness_audit",
        "audited_at_utc": datetime.now(timezone.utc).isoformat(),
        "audit_code_sha256": sha(Path(__file__).resolve()),
        "scope": [
            "Count archived tasks and prediction columns without importing the production evaluator.",
            "Recompute each gold label and positive-witness existence using the independent completion oracle.",
            "Check each saved witness against actual record IDs, one configuration, the target context, consistent PASS evidence and Boolean satisfaction.",
            "Verify source hashes, protocol history links, confusion counts and summary counts.",
        ],
        "limitations": [
            "These constructed development tasks are not independent real-world observations or an outcome-blind confirmatory holdout.",
            "Archived predictions for comparator methods are recounted, not regenerated by their implementations.",
            "Unarchived metamorphic variants are not independently rerun; their summary values are reported only.",
            "This is a post-run audit, not a new experiment or registered FAB production certification.",
        ],
        "checks": [],
    }

    def check(name, actual, expected):
        report["checks"].append({"name": name, "passed": actual == expected, "actual": actual, "expected": expected})

    def counters_equal(name, actual, expected):
        differences = {
            key: {"actual": actual.get(key, 0), "expected": expected.get(key, 0)}
            for key in sorted(set(actual) | set(expected))
            if actual.get(key, 0) != expected.get(key, 0)
        }
        check(name, differences, {})

    try:
        protocol = json.loads((folder / "protocol_frozen.json").read_text())
        summary = json.loads((folder / "summary.json").read_text())
        methods = tuple(protocol["comparison_methods"])
        check("method_names_match_summary", sorted(methods), sorted(summary["metrics"]))
        check("protocol_hash_matches_summary", before["protocol_frozen.json"], summary["protocol_sha256"])
        check("source_hash_set", sorted(protocol["code_sha256"]), ["engine_context.py", "oracle_context.py", "run_context.py"])
        for name, expected in protocol["code_sha256"].items():
            if Path(name).name != name:
                raise ValueError("Unsafe protocol source filename")
            check("source_hash:" + name, sha(code_root / name), expected)
        check("prior_v01_protocol_hash", sha(study_root / "results/pilot_v0_1/protocol_frozen.json"), protocol["old_pilot_protocol_sha256"])
        history = protocol["development_history"]
        check("refinement_explicitly_post_result", history["initial_v02_results_accessed"], True)
        check("prior_v02_protocol_hash", sha(study_root / "results/context_v0_2/protocol_frozen.json"), history["initial_protocol_sha256"])
        for name in ("engine_context.py", "oracle_context.py"):
            check("unchanged_from_initial_v02:" + name, sha(code_root / name), sha(study_root / "v02" / name))

        tasks = {}
        serialized_formulas = {}
        with gzip.open(folder / "tasks.jsonl.gz", "rt", encoding="utf-8") as stream:
            for line in stream:
                task = json.loads(line)
                identifier = task["task_id"]
                if identifier in tasks:
                    raise ValueError("Duplicate archived task ID")
                serialized_formulas[identifier] = task["formula"]
                task["formula"] = expression_tuple(task["formula"])
                tasks[identifier] = task
        predictions = {}
        with gzip.open(folder / "predictions.csv.gz", "rt", encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                if row["task_id"] in predictions:
                    raise ValueError("Duplicate prediction task ID")
                predictions[row["task_id"]] = row
        witnesses = {}
        with (folder / "witnesses.jsonl").open(encoding="utf-8") as stream:
            for line in stream:
                witness = json.loads(line)
                if witness["task_id"] in witnesses:
                    raise ValueError("Duplicate witness task ID")
                witnesses[witness["task_id"]] = witness
        check("task_count_expected", len(tasks), 5120)
        check("task_count_matches_summary", len(tasks), summary["task_count"])
        check("prediction_task_keys", set(tasks) == set(predictions), True)
        check("witness_keys_are_known_tasks", set(witnesses).issubset(tasks), True)
        catalogue = {item["name"]: item for item in protocol["formula_catalogue"]}
        formula_counts, family_counts, formula_family_counts = Counter(), Counter(), Counter()
        gold_counts, metrics, family_metrics, confusion = Counter(), defaultdict(Counter), defaultdict(Counter), Counter()
        failures = defaultdict(list)
        oracle_agreement = witness_existence_agreement = valid_witness_count = 0
        witness_presence_count = prediction_witness_flag_agreement = 0
        for identifier, task in tasks.items():
            row = predictions[identifier]
            formula_name, family = task["formula_name"], task["family"]
            if row["formula"] != formula_name or row["family"] != family:
                failures["metadata"].append(identifier)
            declared = catalogue.get(formula_name)
            if declared is None or serialized_formulas[identifier] != declared["formula"] or task["requirements"] != declared["requirements"]:
                failures["formula_catalogue"].append(identifier)
            if len(task["configs"]) != summary["configs_per_task"]:
                failures["configuration_count"].append(identifier)
            independent_gold = oracle(task)
            independent_exists = best_witness_exists(task)
            oracle_agreement += independent_gold == row["gold"]
            if independent_gold != row["gold"]:
                failures["gold"].append(identifier)
            witness_existence_agreement += int(independent_exists) == int(row["oracle_witness_exists"])
            gold_counts[independent_gold] += 1
            formula_counts[formula_name] += 1
            family_counts[family] += 1
            formula_family_counts[formula_name, family] += 1
            witness = witnesses.get(identifier)
            if independent_gold == "PASS":
                witness_presence_count += witness is not None
                valid = check_witness(task, witness)
                valid_witness_count += valid
                if not valid:
                    failures["invalid_witness"].append(identifier)
                if not independent_exists:
                    failures["missing_positive_route"].append(identifier)
            else:
                valid = witness is None
                if witness is not None:
                    failures["unexpected_witness"].append(identifier)
            prediction_witness_flag_agreement += int(valid) == int(row["witness_valid"])
            for method in methods:
                answer = row[method]
                if answer not in STATES:
                    raise ValueError("Invalid archived method prediction")
                metrics_increment(metrics[method], independent_gold, answer)
                metrics_increment(family_metrics[method, family], independent_gold, answer)
                confusion[method, family, independent_gold, answer] += 1

        check("all_gold_labels_independently_recomputed", oracle_agreement, len(tasks))
        check("all_witness_existence_flags_independently_recomputed", witness_existence_agreement, len(tasks))
        check("metadata_and_formula_failures", {k: len(v) for k, v in failures.items() if v}, {})
        check("gold_counts_match_summary", dict(gold_counts), summary["gold_counts"])
        check("formula_count", len(formula_counts), summary["formula_count"])
        check("family_count", len(family_counts), summary["family_count"])
        check("family_names", sorted(family_counts), sorted(protocol["families"]))
        check("balanced_formula_family_cells", sorted(set(formula_family_counts.values())), [protocol["replicates_per_formula_family"]])
        check("formula_family_cell_count", len(formula_family_counts), len(catalogue) * len(protocol["families"]))
        check("saved_witness_count", len(witnesses), 2048)
        check("pass_tasks_have_saved_witness", witness_presence_count, gold_counts["PASS"])
        check("saved_witness_semantic_validity", valid_witness_count, gold_counts["PASS"])
        check("recorded_witness_valid_flags", prediction_witness_flag_agreement, len(tasks))
        for method in methods:
            counters_equal("metrics:" + method, metrics[method], summary["metrics"][method])
        expected_family = {(item["method"], item["family"]): {k: v for k, v in item.items() if k not in {"method", "family"}} for item in summary["family_metrics"]}
        check("family_metric_keys", sorted(family_metrics), sorted(expected_family))
        for key, value in family_metrics.items():
            counters_equal("family_metrics:" + "/".join(key), value, expected_family.get(key, {}))
        recorded_confusion = Counter()
        with (folder / "confusion.csv").open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                recorded_confusion[row["method"], row["family"], row["gold"], row["prediction"]] += int(row["count"])
        check("confusion_matches_recount", dict(recorded_confusion) == dict(confusion), True)
        for method in ("witness_evaluator", "relational_reference"):
            check("independent_oracle_agreement:" + method, metrics[method]["correct"], len(tasks))
        report["independently_recomputed"] = {
            "task_count": len(tasks), "oracle_gold_agreement": oracle_agreement,
            "gold_counts": dict(gold_counts), "valid_saved_positive_witnesses": valid_witness_count,
            "witness_existence_flag_agreement": witness_existence_agreement,
            "metrics": {key: dict(value) for key, value in metrics.items()},
            "family_counts": dict(family_counts), "formula_counts": dict(formula_counts),
        }
        report["reported_only_metamorphic_checks_not_reexecuted"] = {key: summary["property_checks"][key] for key in METAMORPHIC}
        report["reported_only_metamorphic_total"] = sum(summary["property_checks"][key]["total"] for key in METAMORPHIC)
        report["original_reported_property_total"] = sum(value["total"] for value in summary["property_checks"].values())
        report["failure_examples"] = {key: value[:20] for key, value in failures.items() if value}
        after = {name: sha(folder / name) for name in source_names}
        check("archived_inputs_unchanged_during_audit", after, before)
        report["source_sha256"] = before
        report["status"] = "PASS" if all(check["passed"] for check in report["checks"]) else "FAIL"
    except Exception as exc:
        report["status"] = "ERROR"
        report["exception"] = {"type": type(exc).__name__, "message": str(exc)}
    report["audit_completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "checks": len(report["checks"]), "audit_report": str(output)}, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
