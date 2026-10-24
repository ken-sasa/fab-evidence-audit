"""Read-only audit of the v0.3 SQLite case outputs.

No SQL is executed and neither the primary runner nor its evaluator is imported.
The only study-code import is the independently implemented completion oracle.
Run only after the frozen measurement and independent reference have completed.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
from itertools import combinations
import json
from pathlib import Path
import re
import sys


HERE = Path(__file__).resolve().parent
STUDY = HERE.parent
STATES = {"PASS", "FAIL", "UNKNOWN", "ERROR"}
METHODS = {
    "witness_evaluator", "relational_reference", "sql_relational",
    "config_only_conservative", "context_only_conservative",
    "optimistic_pool", "missing_as_fail",
}
CONSTRAINTS = {
    0: ("SQLITE_CONSTRAINT_FOREIGNKEY", 787),
    1: ("SQLITE_CONSTRAINT_CHECK", 275),
    2: ("SQLITE_CONSTRAINT_UNIQUE", 2067),
}
REFERENCE_REQUIREMENTS = {
    "reject_orphan_foreign_key": 0,
    "reject_negative_check_value": 1,
    "reject_duplicate_unique_token": 2,
}
INVALID_INSERTS = {
    0: "INSERT INTO child VALUES(2,999,1,'new')",
    1: "INSERT INTO child VALUES(2,1,-1,'new')",
    2: "INSERT INTO child VALUES(2,1,1,'base')",
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def tuple_formula(value):
    return tuple(tuple_formula(x) for x in value) if isinstance(value, list) else value


def active_leaves(formula):
    if type(formula) is int:
        return {formula}
    if not isinstance(formula, (tuple, list)) or len(formula) < 3 or formula[0] not in {"AND", "OR"}:
        raise ValueError("Invalid task formula")
    return set().union(*(active_leaves(child) for child in formula[1:]))


def boolean_formula(formula, positive):
    if type(formula) is int:
        return formula in positive
    results = [boolean_formula(child, positive) for child in formula[1:]]
    return all(results) if formula[0] == "AND" else any(results)


def compact_sql(sql):
    return re.sub(r"\s+", "", sql).rstrip(";").lower()


def contiguous(needle, haystack):
    return bool(needle) and any(haystack[i:i + len(needle)] == needle for i in range(len(haystack) - len(needle) + 1))


def effective_values(row):
    values = row.get("effective", {})
    if set(values) != {"foreign_keys", "ignore_check_constraints"}:
        return None
    if not all(isinstance(v, list) and len(v) == 1 and type(v[0]) is int and v[0] in (0, 1) for v in values.values()):
        return None
    return {key: value[0] for key, value in values.items()}


def derived_primary_state(row):
    """Reconstruct the measurement rule from recorded observations only."""
    if row.get("unexpected_exception") or row.get("cleanup_exception"):
        return "ERROR"
    if effective_values(row) is None or row.get("in_transaction") is not True:
        return "ERROR"
    if row.get("initial_count") != 0 or row.get("control_count") != 1:
        return "ERROR"
    if row.get("control_accepted") is not True or row.get("control_row") != [1, 1, 1, "base"]:
        return "ERROR"
    if row.get("cleanup_count") != 0 or row.get("cleanup_transaction_active") is not True:
        return "ERROR"
    if row.get("violating_write") == "accepted" and row.get("final_count") == 2:
        return "FAIL"
    name, code = CONSTRAINTS[row["requirement"]]
    if (row.get("violating_write") == "rejected" and row.get("final_count") == 1
            and row.get("exception_name") == name and row.get("exception_code") == code):
        return "PASS"
    return "ERROR"


def derived_reference_state(probe):
    """Use the independent execution's own controls and observed exception."""
    control = probe.get("valid_control") or {}
    cleanup = probe.get("cleanup") or {}
    if (probe.get("error_reason") or probe.get("probe_exception")
            or probe.get("transaction_active_before") is not True
            or probe.get("transaction_active_after") is not True
            or control.get("accepted") is not True
            or cleanup.get("restored_empty_samples") is not True
            or cleanup.get("outer_transaction_active") is not True):
        return "ERROR"
    req = REFERENCE_REQUIREMENTS[probe["requirement"]]
    name, code = CONSTRAINTS[req]
    if probe.get("violating_write_accepted") is True:
        return "FAIL" if probe.get("accepted_violating_row") == probe.get("violation_parameters") else "ERROR"
    exception = probe.get("observed_exception") or {}
    if (probe.get("violating_write_accepted") is False
            and exception.get("python_type") == "IntegrityError"
            and exception.get("sqlite_errorname") == name
            and exception.get("sqlite_errorcode") == code):
        return "PASS"
    return "ERROR"


def witness_valid(task, witness):
    if not isinstance(witness, dict) or witness.get("config_id") not in task["configs"]:
        return False
    if witness.get("context") != task["target_context"]:
        return False
    ids = witness.get("record_ids")
    if not isinstance(ids, list) or not ids or len(set(ids)) != len(ids):
        return False
    records = {row["record_id"]: row for row in task["records"]}
    if any(rid not in records for rid in ids):
        return False
    selected = [records[rid] for rid in ids]
    if any(row["config_id"] != witness["config_id"] or row["context"] != task["target_context"] or row["result"] != "PASS" for row in selected):
        return False
    active = active_leaves(task["formula"])
    if any(row["requirement"] not in active for row in selected):
        return False
    return boolean_formula(task["formula"], {row["requirement"] for row in selected})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--reference", type=Path)
    args = parser.parse_args()
    out = args.output.resolve()
    ref_path = args.reference.resolve() if args.reference else out / "sqlite_reference.json"
    report_path = out / "audit_report.json"
    if report_path.exists():
        raise FileExistsError("Refusing to overwrite an existing audit report")
    checks = []
    started = datetime.now(timezone.utc).isoformat()

    def equal(name, actual, expected):
        passed = actual == expected
        checks.append({"name": name, "passed": passed, "actual": actual, "expected": expected})
        return passed

    def yes(name, condition):
        return equal(name, bool(condition), True)

    protocol = read_json(out / "protocol_frozen.json")
    summary = read_json(out / "summary.json")
    observations = read_json(out / "observations.json")
    conditions = read_json(out / "conditions.json")
    runtime = read_json(out / "runtime.json")
    predictions = read_json(out / "predictions.json")
    tasks = [json.loads(line) for line in (out / "tasks.jsonl").read_text().splitlines() if line.strip()]
    reference = read_json(ref_path)

    equal("protocol_digest", digest(out / "protocol_frozen.json"), summary["protocol_sha256"])
    for relative, expected in protocol["source_hashes"].items():
        equal("frozen_source:" + relative, digest(STUDY / relative), expected)
    for name, expected in summary["files_sha256"].items():
        equal("saved_file:" + name, digest(out / name), expected)
    equal("frozen_source_descriptions", read_json(HERE / "sources.json"), protocol["sources"])
    equal("runtime_summary_copy", runtime, summary["runtime"])
    equal("reference_source_digest", reference["source_sha256"], digest(HERE / "sqlite_reference.py"))
    equal("same_sqlite_runtime_version", reference["metadata"]["sqlite_runtime_version"], runtime["sqlite_version"])

    profiles = protocol["profiles"]
    phases = protocol["phases"]
    formulas = protocol["formulas"]
    condition_keys = {(phase, config) for phase in phases for config in profiles}
    observed_keys = {(row["context"], row["config_id"], row["requirement"]) for row in observations}
    expected_observed_keys = {(*key, requirement) for key in condition_keys for requirement in (0, 1, 2)}
    equal("conditions_count", len(conditions), 8)
    equal("condition_keys", sorted((c["context"], c["config_id"]) for c in conditions), sorted(condition_keys))
    equal("condition_ids_unique", len({c["condition_id"] for c in conditions}), 8)
    equal("measured_cell_count", len(observations), 24)
    equal("measured_cell_keys", sorted(observed_keys), sorted(expected_observed_keys))
    equal("observation_ids_unique", len({r["record_id"] for r in observations}), 24)
    equal("measured_cells_summary", summary["measured_probe_cells"], len(observations))
    equal("condition_count_summary", summary["connection_conditions"], len(conditions))
    equal("observed_states_summary", summary["observed_states"], dict(Counter(r["result"] for r in observations)))

    condition_map = {(c["context"], c["config_id"]): c for c in conditions}
    for (phase, config), condition in condition_map.items():
        tag = phase + "/" + config
        fk, ignore = profiles[config]
        equal("requested_recipe:" + tag, condition["requested"], {"foreign_keys": fk, "ignore_check_constraints": ignore})
        schema = condition["schema"]
        equal("schema_digest:" + tag, hashlib.sha256(json.dumps(schema, separators=(",", ":")).encode()).hexdigest(), condition["schema_sha256"])
        equal("schema_names:" + tag, sorted(row[0] for row in schema), ["child", "parent"])
        schema_text = " ".join(row[1] for row in schema).upper()
        yes("schema_constraint_types:" + tag, all(token in schema_text for token in ("REFERENCES", "CHECK", "UNIQUE")))
        trace = [compact_sql(sql) for sql in condition["sql_trace"]]
        begins = [i for i, sql in enumerate(trace) if sql == "begin"]
        yes("outer_begin_present:" + tag, len(begins) == 1)
        requested_fk = compact_sql("PRAGMA foreign_keys=" + str(fk))
        requested_check = compact_sql("PRAGMA ignore_check_constraints=" + str(ignore))
        fk_indexes = [i for i, sql in enumerate(trace) if sql == requested_fk]
        check_indexes = [i for i, sql in enumerate(trace) if sql == requested_check]
        yes("requested_pragmas_traced:" + tag, bool(fk_indexes and check_indexes))
        if begins and fk_indexes and check_indexes:
            applied = [fk_indexes[-1], check_indexes[-1]]
            yes("application_phase_trace:" + tag, max(applied) < begins[0] if phase == "before_begin" else begins[0] < min(applied))
        yes("baseline_setting_trace:" + tag, "pragmaforeign_keys=off" in trace and "pragmaignore_check_constraints=off" in trace)
        yes("final_rollback_trace:" + tag, bool(trace) and trace[-1] == "rollback")
    equal("one_primary_schema_fingerprint", len({c["schema_sha256"] for c in conditions}), 1)

    reference_cells = {}
    equal("reference_condition_count", len(reference["conditions"]), 8)
    for condition in reference["conditions"]:
        phase, config = condition["application_phase"], condition["recipe"]
        tag = phase + "/" + config
        equal("reference_baseline:" + tag, condition.get("baseline_settings_before_schema"), {"foreign_keys": 0, "ignore_check_constraints": 0})
        equal("reference_request:" + tag, condition["requested_settings"], {"foreign_keys": profiles[config][0], "ignore_check_constraints": profiles[config][1]})
        yes("reference_measurement_transaction:" + tag, condition.get("transaction_active_at_measurement") is True)
        equal("reference_probe_count:" + tag, len(condition["probes"]), 3)
        equal("reference_settings_stable:" + tag, condition.get("settings_inside_transaction_after_application"), condition.get("settings_inside_transaction_after_probes"))
        for probe in condition["probes"]:
            requirement = REFERENCE_REQUIREMENTS[probe["requirement"]]
            key = (phase, config, requirement)
            yes("reference_cell_unique:" + str(key), key not in reference_cells)
            name, code = CONSTRAINTS[requirement]
            equal("reference_required_exception_name:" + str(key), probe["required_exception"]["sqlite_errorname"], name)
            equal("reference_required_exception_code:" + str(key), probe["required_exception"]["sqlite_errorcode"], code)
            derived = derived_reference_state(probe)
            equal("reference_state_reconstructed:" + str(key), probe["state"], derived)
            reference_cells[key] = (condition, probe)
    equal("reference_cell_keys", sorted(reference_cells), sorted(expected_observed_keys))

    for row in observations:
        key = (row["context"], row["config_id"], row["requirement"])
        tag = row["record_id"]
        name, code = CONSTRAINTS[row["requirement"]]
        equal("expected_constraint_mapping:" + tag, row["expected_constraint"], name)
        derived = derived_primary_state(row)
        equal("primary_state_reconstructed:" + tag, row["result"], derived)
        if row["result"] in {"PASS", "FAIL"}:
            equal("initial_empty:" + tag, row.get("initial_count"), 0)
            equal("valid_control_count:" + tag, row.get("control_count"), 1)
            equal("valid_control_row:" + tag, row.get("control_row"), [1, 1, 1, "base"])
            yes("control_accepted:" + tag, row.get("control_accepted") is True)
            equal("cleanup_empty:" + tag, row.get("cleanup_count"), 0)
            yes("cleanup_outer_transaction:" + tag, row.get("cleanup_transaction_active") is True)
            equal("final_row_count:" + tag, row.get("final_count"), 1 if row["result"] == "PASS" else 2)
        if row["result"] == "PASS":
            equal("observed_constraint_name:" + tag, row.get("exception_name"), name)
            equal("observed_constraint_code:" + tag, row.get("exception_code"), code)
        eff = effective_values(row)
        fk, ignore = profiles[row["config_id"]]
        expected_eff = {"foreign_keys": fk if row["context"] == "before_begin" else 0, "ignore_check_constraints": ignore}
        equal("documented_effective_readback:" + tag, eff, expected_eff)
        yes("measurement_outer_transaction:" + tag, row.get("in_transaction") is True)
        condition = condition_map[key[:2]]
        yes("probe_trace_embedded:" + tag, contiguous(row["sql_trace"], condition["sql_trace"]))
        trace = {compact_sql(sql) for sql in row["sql_trace"]}
        expected_trace = {compact_sql(sql) for sql in ("PRAGMA foreign_keys", "PRAGMA ignore_check_constraints", "SAVEPOINT probe", "INSERT INTO child VALUES(1,1,1,'base')", INVALID_INSERTS[row["requirement"]], "SELECT COUNT(*) FROM child", "ROLLBACK TO probe", "RELEASE probe")}
        yes("required_probe_trace:" + tag, expected_trace <= trace)
        ref_condition, ref_probe = reference_cells[key]
        equal("independent_runtime_state:" + tag, row["result"], ref_probe["state"])
        equal("independent_runtime_readback:" + tag, eff, ref_condition["settings_inside_transaction_after_application"])

    expected_tasks = {}
    for phase in phases:
        for size in range(1, 5):
            for configs in combinations(profiles, size):
                for label, formula in formulas.items():
                    tid = "complete-" + phase + "-" + "-".join(configs) + "-" + label
                    expected_tasks[tid] = ("complete", phase, list(configs), label, formula)
        for label, formula in formulas.items():
            expected_tasks["withheld-" + phase + "-" + label] = ("withheld", phase, ["strict"], label, formula)
    equal("tasks_count", len(tasks), 128)
    equal("task_ids_unique", len({t["task_id"] for t in tasks}), 128)
    equal("query_design_ids", sorted(t["task_id"] for t in tasks), sorted(expected_tasks))
    equal("task_families", dict(Counter(t["family"] for t in tasks)), {"complete": 120, "withheld": 8})
    equal("query_count_summary", summary["queries"], len(tasks))
    equal("query_families_summary", summary["query_families"], dict(Counter(t["family"] for t in tasks)))
    projected = [{key: row[key] for key in ("config_id", "context", "requirement", "result", "record_id")} for row in observations]
    observation_by_id = {r["record_id"]: r for r in projected}
    task_by_id = {t["task_id"]: t for t in tasks}
    omissions = []
    for task in tasks:
        tid = task["task_id"]
        family, phase, configs, label, formula = expected_tasks[tid]
        equal("task_metadata:" + tid, [task["family"], task["target_context"], task["configs"], task["formula_id"], task["formula"], task["requirements"]], [family, phase, configs, label, formula, 3])
        allowed = [r for r in projected if r["config_id"] in configs]
        if family == "withheld":
            removed = [r for r in allowed if r["context"] == phase and r["requirement"] == 0]
            equal("one_declared_omission:" + tid, len(removed), 1)
            omissions.extend(r["record_id"] for r in removed)
            allowed = [r for r in allowed if not (r["context"] == phase and r["requirement"] == 0)]
        equal("faithful_record_subset:" + tid, sorted(task["records"], key=lambda r: r["record_id"]), sorted(allowed, key=lambda r: r["record_id"]))
        yes("unique_task_record_ids:" + tid, len(task["records"]) == len({r["record_id"] for r in task["records"]}))
        yes("all_task_records_trace_back:" + tid, all(observation_by_id.get(r["record_id"]) == r for r in task["records"]))
    equal("declared_omission_events", len(omissions), 8)
    equal("omitted_unique_measured_cells", len(set(omissions)), 2)

    spec = importlib.util.spec_from_file_location("independent_context_oracle_for_sqlite_audit", STUDY / "v02_refined/oracle_context.py")
    oracle_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oracle_module)
    equal("predictions_count", len(predictions), 128)
    equal("prediction_ids_unique", len({p["task_id"] for p in predictions}), 128)
    equal("prediction_task_keys", sorted(p["task_id"] for p in predictions), sorted(task_by_id))
    recount = {method: Counter({key: 0 for key in ("correct", "false_pass", "excess_error", "unknown_to_pass", "unknown_to_fail", "hidden_error")}) for method in METHODS}
    gold_counts = Counter()
    pass_witnesses = 0
    for prediction in predictions:
        tid = prediction["task_id"]
        task = dict(task_by_id[tid])
        task["formula"] = tuple_formula(task["formula"])
        gold = oracle_module.oracle(task)
        equal("oracle_recomputed:" + tid, prediction["gold"], gold)
        equal("prediction_family:" + tid, prediction["family"], task["family"])
        equal("seven_method_keys:" + tid, sorted(prediction["methods"]), sorted(METHODS))
        gold_counts[gold] += 1
        for method, state in prediction["methods"].items():
            yes("method_state_domain:" + tid + "/" + method, state in STATES)
            metrics = recount[method]
            metrics["correct"] += state == gold
            metrics["false_pass"] += gold == "FAIL" and state == "PASS"
            metrics["excess_error"] += gold != "ERROR" and state == "ERROR"
            metrics["unknown_to_pass"] += gold == "UNKNOWN" and state == "PASS"
            metrics["unknown_to_fail"] += gold == "UNKNOWN" and state == "FAIL"
            metrics["hidden_error"] += gold == "ERROR" and state != "ERROR"
        if prediction["methods"]["witness_evaluator"] == "PASS":
            pass_witnesses += 1
            yes("successful_witness_valid:" + tid, witness_valid(task, prediction["witness"]))
            yes("independent_positive_route_exists:" + tid, oracle_module.best_witness_exists(task))
        else:
            equal("nonpass_has_no_witness:" + tid, prediction["witness"], None)
    equal("gold_counts_summary", dict(gold_counts), summary["gold_counts"])
    for method, metrics in recount.items():
        equal("method_metrics:" + method, dict(metrics), summary["metrics"][method])
    report = {
        "audit_kind": "post_execution_raw_observation_trace_subset_oracle_and_count_audit",
        "audited_at_utc": started,
        "audit_completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "audit_source_sha256": digest(Path(__file__)),
        "reference_output_sha256": digest(ref_path),
        "checks": checks,
        "checks_passed": sum(c["passed"] for c in checks),
        "checks_total": len(checks),
        "all_checks_passed": all(c["passed"] for c in checks),
        "recomputed_metrics": {k: dict(v) for k, v in recount.items()},
        "recomputed_gold_counts": dict(gold_counts),
        "pass_witnesses_checked": pass_witnesses,
        "observed_error_cells": [r["record_id"] for r in observations if r["result"] == "ERROR"],
        "units": {"runtime_implementations": 1, "independently_authored_probe_programs": 2, "connection_conditions_per_program": 8, "measured_cells_per_program": 24, "dependent_queries": 128, "declared_omission_events": 8, "unique_measured_cells_hidden_in_derived_controls": 2},
        "scope": "No SQL or primary/evaluator code was executed by this audit. It reconstructs result labels from stored probe telemetry, checks documented readback behavior, matches separate same-runtime reference measurements, verifies task provenance/witnesses, and invokes the independent completion oracle. SQL traces are stored observations, not independently authenticated executions. Checks are dependent engineering assertions, not independent experiments. No population inference, second SQLite implementation or independent research-group reproduction is claimed.",
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("checks_passed", "checks_total", "all_checks_passed", "pass_witnesses_checked")}))
    if not report["all_checks_passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
