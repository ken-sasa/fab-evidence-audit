"""Audit frozen decimal telemetry without running decimal or evaluator code.

Only the independently authored completion oracle is imported. Raw values are
checked with exact integer/Fraction arithmetic. Run after both probe programs.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import importlib.util
from itertools import combinations
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
STUDY = HERE.parent
PROFILES = {"high_trap": (6, True), "high_open": (6, False), "low_trap": (3, True), "low_open": (3, False)}
CONTEXTS = ("ROUND_HALF_EVEN", "ROUND_HALF_UP")
SIGNALS = {"Clamped", "DivisionByZero", "Inexact", "InvalidOperation", "Overflow", "Rounded", "Subnormal", "Underflow", "FloatOperation"}
METHODS = {"witness_evaluator", "relational_reference", "sql_relational", "config_only_conservative", "context_only_conservative", "optimistic_pool", "missing_as_fail"}
METRICS = ("correct", "false_pass", "excess_error", "unknown_to_pass", "unknown_to_fail", "hidden_error")
CONTROL_VALUES = {0: "1.23", 1: "2", 2: "0.25"}
SCALARS = ("prec", "rounding", "Emin", "Emax", "capitals", "clamp")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def tuples(value):
    return tuple(tuples(x) for x in value) if isinstance(value, list) else value


def flag_map(telemetry):
    flags = telemetry.get("flags", {})
    return flags if set(flags) == SIGNALS and all(type(v) is bool for v in flags.values()) else None


def clear_flags(telemetry):
    flags = flag_map(telemetry)
    return flags is not None and not any(flags.values())


def only_inexact_related(telemetry):
    flags = flag_map(telemetry)
    return flags is not None and flags["Inexact"] and not any(v for k, v in flags.items() if k not in {"Inexact", "Rounded"})


def exception_name(telemetry):
    exc = telemetry.get("exception")
    return exc.get("type") if isinstance(exc, dict) else exc


def exact_inexact(telemetry):
    exc = telemetry.get("exception")
    return exception_name(telemetry) == "Inexact" and (not isinstance(exc, dict) or exc.get("is_exact_Inexact") is True)


def numeric_value(telemetry):
    """Return a Fraction only for a self-consistent finite return value."""
    if telemetry.get("outcome") != "return" or telemetry.get("exception") is not None:
        return None
    if telemetry.get("is_finite", telemetry.get("finite")) is not True:
        return None
    value = telemetry.get("value")
    rep = telemetry.get("tuple", telemetry.get("as_tuple"))
    if not isinstance(value, str) or not isinstance(rep, dict):
        return None
    sign, digits, exponent = rep.get("sign"), rep.get("digits"), rep.get("exponent")
    if type(sign) is not int or sign not in (0, 1) or type(exponent) is not int or abs(exponent) > 1000:
        return None
    if not isinstance(digits, list) or not digits or any(type(d) is not int or not 0 <= d <= 9 for d in digits):
        return None
    try:
        from_text = Fraction(value)
        coefficient = int("".join(str(d) for d in digits)) * (-1 if sign else 1)
        from_tuple = Fraction(coefficient * 10 ** exponent) if exponent >= 0 else Fraction(coefficient, 10 ** (-exponent))
    except (ValueError, ZeroDivisionError):
        return None
    return from_text if from_text == from_tuple else None


def context_valid(row):
    effective = row.get("effective", {})
    precision, trapped = PROFILES[row["config_id"]]
    expected = {"prec": precision, "rounding": row["context"], "Emin": -999, "Emax": 999, "capitals": 1, "clamp": 0}
    traps = effective.get("traps", {})
    return (all(effective.get(k) == v for k, v in expected.items())
            and set(traps) == SIGNALS and all(type(v) is bool for v in traps.values())
            and all(v == (trapped and k == "Inexact") for k, v in traps.items())
            and ("flags" not in effective or clear_flags(effective)))


def control_valid(row):
    control = row.get("control", {})
    return numeric_value(control) == Fraction(CONTROL_VALUES[row["requirement"]]) and clear_flags(control)


def classify(row):
    """Reconstruct the three falsifiable requirements from telemetry only."""
    if not context_valid(row) or not control_valid(row):
        return "ERROR"
    requirement, probe = row["requirement"], row.get("probe", {})
    value = numeric_value(probe)
    if requirement == 0:
        if value == Fraction("1.2346") and clear_flags(probe):
            return "PASS"
        if probe.get("outcome") == "exception" and exact_inexact(probe) and only_inexact_related(probe):
            return "FAIL"
        if value is not None:
            return "FAIL"
    elif requirement == 1:
        if clear_flags(probe) and value in (Fraction(2), Fraction(3)):
            return "PASS" if value == 3 else "FAIL"
    elif requirement == 2:
        if probe.get("outcome") == "exception" and exact_inexact(probe) and only_inexact_related(probe):
            return "PASS"
        if value is not None and only_inexact_related(probe) and probe["flags"]["Rounded"]:
            return "FAIL"
    return "ERROR"


def truth(formula, positive):
    if type(formula) is int:
        return formula in positive
    values = [truth(child, positive) for child in formula[1:]]
    return all(values) if formula[0] == "AND" else any(values)


def leaves(formula):
    return {formula} if type(formula) is int else set().union(*(leaves(c) for c in formula[1:]))


def valid_witness(task, witness):
    if not isinstance(witness, dict) or witness.get("config_id") not in task["configs"] or witness.get("context") != task["target_context"]:
        return False
    ids = witness.get("record_ids")
    records = {r["record_id"]: r for r in task["records"]}
    if not isinstance(ids, list) or not ids or len(ids) != len(set(ids)) or any(i not in records for i in ids):
        return False
    selected = [records[i] for i in ids]
    if any(r["config_id"] != witness["config_id"] or r["context"] != task["target_context"] or r["result"] != "PASS" or r["requirement"] not in leaves(task["formula"]) for r in selected):
        return False
    return truth(task["formula"], {r["requirement"] for r in selected})


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results", type=Path, required=True)
    args = ap.parse_args()
    out = args.results.resolve()
    destination = out / "audit_report.json"
    if destination.exists():
        raise FileExistsError("Refusing to overwrite a completed audit")
    started = datetime.now(timezone.utc).isoformat()
    checks = []
    def check(name, actual, expected=True):
        checks.append({"name": name, "passed": actual == expected, "actual": actual, "expected": expected})
    protocol, summary = read(out / "protocol_frozen.json"), read(out / "summary.json")
    rows, predictions = read(out / "observations.json"), read(out / "predictions.json")
    tasks = [json.loads(line) for line in (out / "tasks.jsonl").read_text().splitlines() if line.strip()]
    reference = read(out / "decimal_reference.json")
    check("protocol_hash", digest(out / "protocol_frozen.json"), summary["protocol_sha256"])
    for name, expected in protocol["source_hashes"].items():
        check("frozen_source:" + name, digest(STUDY / name), expected)
    for name, expected in summary["files_sha256"].items():
        check("output_hash:" + name, digest(out / name), expected)
    ref_source = HERE / "decimal_reference.py"
    check("independent_reference_source_hash", reference["source_sha256"], digest(ref_source))
    if "sources" in protocol:
        check("frozen_documentation_descriptions", read(HERE / "sources.json"), protocol["sources"])
    expected_keys = {(c, x, q) for c in PROFILES for x in CONTEXTS for q in range(3)}
    by_key = {(r["config_id"], r["context"], r["requirement"]): r for r in rows}
    check("primary_24_cells", len(rows), 24)
    check("primary_cell_keys", sorted(by_key), sorted(expected_keys))
    check("primary_24_record_ids", len({r["record_id"] for r in rows}), 24)
    check("measured_count_summary", summary["measured_probe_cells"], 24)
    check("observed_states_summary", dict(Counter(r["result"] for r in rows)), summary["observed_states"])
    reference_rows = reference["observations"]
    ref_by_key = {(r["config_id"], r["context"], r["requirement"]): r for r in reference_rows}
    check("reference_24_cells", len(reference_rows), 24)
    check("reference_cell_keys", sorted(ref_by_key), sorted(expected_keys))
    differences = []
    for key, row in by_key.items():
        tag = "/".join(map(str, key))
        check("primary_context:" + tag, context_valid(row))
        check("primary_control:" + tag, control_valid(row))
        check("primary_control_expected:" + tag, row.get("control_expected", CONTROL_VALUES[key[2]]), CONTROL_VALUES[key[2]])
        check("primary_state_reconstructed:" + tag, row["result"], classify(row))
        after = row.get("effective_after", {})
        check("primary_context_unchanged:" + tag, {k: after.get(k) for k in (*SCALARS, "traps")}, {k: row["effective"].get(k) for k in (*SCALARS, "traps")})
        if "flags" in after:
            check("primary_flags_after:" + tag, after["flags"], row["probe"].get("flags"))
        for section in ("control", "probe"):
            telemetry = row[section]
            if "flags_before" in telemetry:
                check("primary_cleared_before:" + tag + "/" + section, clear_flags({"flags": telemetry["flags_before"]}))
        if "passed" in row["control"]:
            check("primary_control_pass_flag:" + tag, row["control"]["passed"], control_valid(row))
        ref = ref_by_key[key]
        check("reference_context:" + tag, context_valid(ref))
        check("reference_control:" + tag, control_valid(ref))
        check("reference_state_reconstructed:" + tag, ref["result"], classify(ref))
        check("independent_runtime_state:" + tag, row["result"], ref["result"])
        for phase in ("control", "probe"):
            a, b = row[phase], ref[phase]
            check("reference_outcome:" + tag + "/" + phase, a.get("outcome"), b.get("outcome"))
            check("reference_return_value:" + tag + "/" + phase, str(numeric_value(a)), str(numeric_value(b)))
            check("reference_exception_type:" + tag + "/" + phase, exception_name(a), exception_name(b))
            check("reference_Inexact_flag:" + tag + "/" + phase, a["flags"]["Inexact"], b["flags"]["Inexact"])
            if a.get("flags") != b.get("flags"):
                differences.append({"cell": list(key), "phase": phase, "primary": a.get("flags"), "reference": b.get("flags"), "interpretation": "Logged secondary flag difference; not automatically an error or state disagreement."})
    ref_normalized = reference.get("normalized_records")
    if ref_normalized is not None:
        check("reference_normalized_count", len(ref_normalized), 24)
        check("reference_normalized_states", sorted((r["config_id"], r["context"], r["requirement"], r["result"]) for r in ref_normalized), sorted((*k, r["result"]) for k, r in ref_by_key.items()))

    formulas = protocol["formulas"]
    profile_order = list(protocol.get("profiles", PROFILES))
    contexts = list(protocol.get("contexts", CONTEXTS))
    check("protocol_profile_domain", sorted(profile_order), sorted(PROFILES))
    check("protocol_context_domain", sorted(contexts), sorted(CONTEXTS))
    expected_tasks = {}
    for context in contexts:
        for size in range(1, 5):
            for configs in combinations(profile_order, size):
                for label, formula in formulas.items():
                    expected_tasks["complete-" + context + "-" + "-".join(configs) + "-" + label] = ("complete", context, list(configs), label, formula)
        for label, formula in formulas.items():
            expected_tasks["withheld-" + context + "-" + label] = ("withheld", context, ["high_trap"], label, formula)
    check("query_count", len(tasks), 128)
    check("query_ids_unique", len({t["task_id"] for t in tasks}), 128)
    check("query_design_ids", sorted(t["task_id"] for t in tasks), sorted(expected_tasks))
    check("query_families", dict(Counter(t["family"] for t in tasks)), {"complete": 120, "withheld": 8})
    records = [{k: r[k] for k in ("config_id", "context", "requirement", "result", "record_id")} for r in rows]
    omitted = []
    for task in tasks:
        tid = task["task_id"]
        family, context, configs, label, formula = expected_tasks[tid]
        check("query_metadata:" + tid, [task["family"], task["target_context"], task["configs"], task["formula_id"], task["formula"], task["requirements"]], [family, context, configs, label, formula, 3])
        selected = [r for r in records if r["config_id"] in configs]
        if family == "withheld":
            removed = [r for r in selected if r["context"] == context and r["requirement"] == 2]
            check("one_declared_omission:" + tid, len(removed), 1)
            omitted.extend(r["record_id"] for r in removed)
            selected = [r for r in selected if not (r["context"] == context and r["requirement"] == 2)]
        check("faithful_record_subset:" + tid, sorted(task["records"], key=lambda r: r["record_id"]), sorted(selected, key=lambda r: r["record_id"]))
        check("query_record_ids_unique:" + tid, len({r["record_id"] for r in task["records"]}), len(task["records"]))
    check("omission_events", len(omitted), 8)
    check("distinct_hidden_measurement_cells", len(set(omitted)), 2)
    spec = importlib.util.spec_from_file_location("decimal_audit_completion_oracle", STUDY / "v02_refined/oracle_context.py")
    oracle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oracle)
    task_map = {t["task_id"]: t for t in tasks}
    check("predictions_count", len(predictions), 128)
    check("prediction_ids_unique", len({p["task_id"] for p in predictions}), 128)
    check("prediction_ids", sorted(p["task_id"] for p in predictions), sorted(task_map))
    metrics = {m: Counter(dict.fromkeys(METRICS, 0)) for m in METHODS}
    gold_counts, witness_count = Counter(), 0
    for prediction in predictions:
        tid = prediction["task_id"]
        task = dict(task_map[tid]); task["formula"] = tuples(task["formula"])
        gold = oracle.oracle(task)
        check("oracle_recomputed:" + tid, prediction["gold"], gold)
        check("prediction_family:" + tid, prediction["family"], task["family"])
        check("seven_methods:" + tid, sorted(prediction["methods"]), sorted(METHODS))
        gold_counts[gold] += 1
        for method, state in prediction["methods"].items():
            check("state_domain:" + tid + "/" + method, state in {"PASS", "FAIL", "UNKNOWN", "ERROR"})
            m = metrics[method]
            for name, condition in (("correct", state == gold), ("false_pass", gold == "FAIL" and state == "PASS"), ("excess_error", gold != "ERROR" and state == "ERROR"), ("unknown_to_pass", gold == "UNKNOWN" and state == "PASS"), ("unknown_to_fail", gold == "UNKNOWN" and state == "FAIL"), ("hidden_error", gold == "ERROR" and state != "ERROR")):
                m[name] += condition
        if prediction["methods"]["witness_evaluator"] == "PASS":
            witness_count += 1
            check("successful_witness_valid:" + tid, valid_witness(task, prediction["witness"]))
            check("independent_positive_route:" + tid, oracle.best_witness_exists(task))
        else:
            check("nonpass_no_witness:" + tid, prediction["witness"], None)
    check("gold_counts_summary", dict(gold_counts), summary["gold_counts"])
    check("query_count_summary", summary["queries"], 128)
    for method, counter in metrics.items():
        check("metrics_recount:" + method, dict(counter), summary["metrics"][method])
    report = {"audit_kind": "raw_decimal_telemetry_reference_provenance_oracle_count_audit", "started_at_utc": started,
              "completed_at_utc": datetime.now(timezone.utc).isoformat(), "audit_source_sha256": digest(Path(__file__)),
              "checks": checks, "checks_passed": sum(c["passed"] for c in checks), "checks_total": len(checks),
              "all_checks_passed": all(c["passed"] for c in checks), "pass_witnesses_checked": witness_count,
              "secondary_flag_differences": differences, "recomputed_gold_counts": dict(gold_counts),
              "recomputed_metrics": {k: dict(v) for k, v in metrics.items()},
              "reference_output_sha256": digest(out / "decimal_reference.json"),
              "scope": "No candidate decimal operation, primary runner, evaluator or reference probe program is executed by this audit. Only stored telemetry and exact Fraction arithmetic are used for runtime checks; the independent completion oracle is invoked for query states. Secondary flag differences do not automatically imply state disagreement. Counts are dependent engineering assertions, not independent empirical observations."}
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("checks_passed", "checks_total", "all_checks_passed", "pass_witnesses_checked")}))
    if not report["all_checks_passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
