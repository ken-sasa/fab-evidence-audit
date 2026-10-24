"""Exploratory decimal case: freeze before any candidate arithmetic execution."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import decimal as d
import hashlib
import importlib.util
from itertools import combinations
import json
from pathlib import Path
import platform
import sys

HERE = Path(__file__).resolve().parent
STUDY = HERE.parent
PROFILES = {"high_trap": (6, True), "high_open": (6, False), "low_trap": (3, True), "low_open": (3, False)}
CONTEXTS = ("ROUND_HALF_EVEN", "ROUND_HALF_UP")
FORMULAS = {"both": ("AND", 0, 2), "either": ("OR", 0, 2),
            "both_with_round": ("AND", 0, 2, 1), "either_with_round": ("AND", ("OR", 0, 2), 1)}
FROZEN = ["v04_decimal/run_decimal.py", "v04_decimal/decimal_reference.py", "v04_decimal/audit_decimal.py",
          "v04_decimal/sources.json", "v02_refined/engine_context.py", "v02_refined/oracle_context.py",
          "v03_sqlite/run_sqlite_case.py", "software/src/fab_evidence_audit/api.py", "software/src/fab_evidence_audit/_core.py"]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dump(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def freeze(out):
    out.mkdir(parents=True, exist_ok=False)
    protocol = {
        "version": "v0.4 decimal executable case",
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_outcomes_accessed_before_freeze": False,
        "local_commitment_only": True,
        "selection": "Purposive official-document contracts outside SQLite and main FAB corpus. Expected documented effects known; not a blinded holdout or external preregistration.",
        "profiles": PROFILES, "contexts": CONTEXTS,
        "context_attributes": {"Emin": -999, "Emax": 999, "capitals": 1, "clamp": 0, "flags": [], "traps": "only Inexact if declared"},
        "requirements": {
            "0": {"operation": "Context.add(Decimal('1.2345'),Decimal('0.0001'))", "control": "1.20+0.03=1.23", "pass": "exact returned value1.2346 and all flags false", "fail": "other finite return or exact Inexact exception; flags retained"},
            "1": {"operation": "Decimal('2.5').to_integral_value(context=ctx)", "control": "2.to_integral_value=2", "pass": "return3 and allflagsfalse", "fail": "return2 and allflagsfalse"},
            "2": {"operation": "Context.divide(Decimal('1'),Decimal('3'))", "control": "1/4=0.25", "pass": "exact Inexact exception and Inexact flag", "fail": "finite return with Inexact and Rounded flags"},
        },
        "measurement": "Fresh explicit Context per probe, string-created operands, clear flags before control and probe, record all attributes/traps/flags/return tuples/exceptions; control failure, unexpected exception or readback mismatch=ERROR. Expected exception means FAIL for requirement0, PASS for requirement2.",
        "static_source_refinement_before_outcomes": "Reading _pydecimal showed Inexact may raise before Rounded is flagged. A trapped Inexact does not require Rounded; record backend flag differences without suppressing them. No candidate arithmetic was executed to make this refinement.",
        "reference": "Separately authored _pydecimal probe code where available; backend identity recorded. Same workflow, not independent human/group replication. Compare states, values, exception type and Inexact flag; other flags descriptive.",
        "formulas": FORMULAS,
        "query_design": {"complete": "15 nonempty profile subsets x2 contexts x4 formulas=120", "withheld": "high_trap-only x2contexts x4formulas remove requirement2targetrow=8; two unique withheld measuredcells", "unit": "24measured cells from8conditions reused by128dependentqueries; no population inference"},
        "metrics": ["agreement with completion oracle", "false PASS", "unknown closures", "excess ERROR", "valid support witnesses", "24 paired backend probe states", "all backend flag differences retained"],
        "exclusions": "No outcome-based exclusions. Setup failure means incomplete run. Corrections require named amended run; originals retained.",
        "scope": "Public technical specifications and artificial decimal values, no human/personal/main-study records",
        "sources": json.loads((HERE / "sources.json").read_text()),
        "source_hashes": {name: sha(STUDY / name) for name in FROZEN},
    }
    dump(out / "protocol_frozen.json", protocol)
    print(json.dumps({"protocol": str(out / "protocol_frozen.json"), "sha256": sha(out / "protocol_frozen.json")}))


def settings(ctx):
    result = {key: getattr(ctx, key) for key in ("prec", "rounding", "Emin", "Emax", "capitals", "clamp")}
    result["traps"] = {signal.__name__: bool(value) for signal, value in ctx.traps.items()}
    return result


def capture(ctx, operation):
    ctx.clear_flags()
    result = {"outcome": "exception", "value": None, "tuple": None, "finite": None, "exception": None,
              "flags_before": {s.__name__: bool(v) for s, v in ctx.flags.items()}}
    try:
        value = operation()
        parts = value.as_tuple()
        result.update(outcome="return", value=str(value), tuple={"sign": parts.sign, "digits": list(parts.digits), "exponent": parts.exponent}, finite=value.is_finite())
    except Exception as exc:
        result["exception"] = {"type": type(exc).__name__, "module": type(exc).__module__, "message": str(exc), "is_exact_Inexact": type(exc) is d.Inexact}
    result["flags"] = {s.__name__: bool(v) for s, v in ctx.flags.items()}
    return result


def classify(req, probe):
    flags = probe["flags"]
    clean = not any(flags.values())
    if req == 0:
        if probe["outcome"] == "return" and probe["finite"]:
            return "PASS" if d.Decimal(probe["value"]) == d.Decimal("1.2346") and clean else "FAIL"
        if probe.get("exception") and probe["exception"]["is_exact_Inexact"]:
            return "FAIL"
    elif req == 1:
        if probe["outcome"] == "return" and clean:
            if d.Decimal(probe["value"]) == d.Decimal("3"):
                return "PASS"
            if d.Decimal(probe["value"]) == d.Decimal("2"):
                return "FAIL"
    elif req == 2:
        if probe.get("exception") and probe["exception"]["is_exact_Inexact"] and flags.get("Inexact"):
            return "PASS"
        if probe["outcome"] == "return" and probe["finite"] and flags.get("Inexact") and flags.get("Rounded"):
            return "FAIL"
    return "ERROR"


def observe():
    records = []
    for context in CONTEXTS:
        for config, (precision, trap) in PROFILES.items():
            for req in range(3):
                ctx = d.Context(prec=precision, rounding=getattr(d, context), Emin=-999, Emax=999, capitals=1, clamp=0, flags=[], traps=[d.Inexact] if trap else [])
                before = settings(ctx)
                controls = [lambda: ctx.add(d.Decimal("1.20"), d.Decimal("0.03")),
                            lambda: d.Decimal("2").to_integral_value(context=ctx),
                            lambda: ctx.divide(d.Decimal("1"), d.Decimal("4"))]
                operations = [lambda: ctx.add(d.Decimal("1.2345"), d.Decimal("0.0001")),
                              lambda: d.Decimal("2.5").to_integral_value(context=ctx),
                              lambda: ctx.divide(d.Decimal("1"), d.Decimal("3"))]
                control = capture(ctx, controls[req])
                expected = ("1.23", "2", "0.25")[req]
                control["passed"] = control["outcome"] == "return" and d.Decimal(control["value"]) == d.Decimal(expected) and not any(control["flags"].values())
                probe = capture(ctx, operations[req])
                after = settings(ctx)
                effective_ok = before == after and before["prec"] == precision and before["rounding"] == context and before["traps"].get("Inexact") == trap and sum(before["traps"].values()) == int(trap)
                state = classify(req, probe) if control["passed"] and effective_ok else "ERROR"
                records.append({"record_id": f"decimal-{context}-{config}-r{req}", "config_id": config, "context": context,
                                "requirement": req, "result": state, "effective": before, "effective_after": after,
                                "control_expected": expected, "control": control, "probe": probe})
    return records


def tasks_from(rows):
    keys = ("record_id", "config_id", "context", "requirement", "result")
    records = [{key: row[key] for key in keys} for row in rows]
    tasks = []
    for context in CONTEXTS:
        for size in range(1, 5):
            for configs in combinations(PROFILES, size):
                for label, formula in FORMULAS.items():
                    tasks.append({"task_id": f"complete-{context}-{'-'.join(configs)}-{label}", "family": "complete", "formula_id": label,
                                  "formula": formula, "requirements": 3, "configs": list(configs), "target_context": context,
                                  "records": [r for r in records if r["config_id"] in configs]})
        for label, formula in FORMULAS.items():
            tasks.append({"task_id": f"withheld-{context}-{label}", "family": "withheld", "formula_id": label,
                          "formula": formula, "requirements": 3, "configs": ["high_trap"], "target_context": context,
                          "records": [r for r in records if r["config_id"] == "high_trap" and not (r["context"] == context and r["requirement"] == 2)]})
    return tasks


def run(out):
    protocol = json.loads((out / "protocol_frozen.json").read_text())
    for name, expected in protocol["source_hashes"].items():
        if sha(STUDY / name) != expected:
            raise RuntimeError("Frozen source changed: " + name)
    if (out / "summary.json").exists():
        raise FileExistsError("Refusing completed-run overwrite")
    sys.path.insert(0, str(STUDY / "software/src"))
    from fab_evidence_audit import audit, __version__
    engine = module("legacy_decimal_engine", STUDY / "v02_refined/engine_context.py")
    oracle = module("decimal_completion_oracle", STUDY / "v02_refined/oracle_context.py")
    sql_control = module("decimal_sql_control", STUDY / "v03_sqlite/run_sqlite_case.py")
    runtime = {"python": sys.version, "platform": platform.platform(), "decimal_version": d.__version__,
               "libmpdec_version": getattr(d, "__libmpdec_version__", None), "decimal_class_module": d.Decimal.__module__,
               "package_version": __version__, "started_at_utc": datetime.now(timezone.utc).isoformat()}
    try:
        import _decimal
        runtime["backend"] = "_decimal C" if d.Decimal is _decimal.Decimal else "decimal Python fallback"
    except ImportError:
        runtime["backend"] = "decimal Python fallback"
    rows = observe()
    dump(out / "observations.json", rows)
    dump(out / "runtime.json", runtime)
    tasks = tasks_from(rows)
    (out / "tasks.jsonl").write_text("".join(json.dumps(t) + "\n" for t in tasks))
    predictions = []
    for task in tasks:
        result = audit(task)
        gold = oracle.oracle(task)
        methods = {"witness_evaluator": result["state"]}
        for method in ("relational_reference", "config_only_conservative", "context_only_conservative", "optimistic_pool", "missing_as_fail"):
            methods[method] = getattr(engine, method)(task)
        methods["sql_relational"] = sql_control.sql_relational(task, engine.leaves(task["formula"]))[0]
        predictions.append({"task_id": task["task_id"], "family": task["family"], "gold": gold, "methods": methods, "witness": result["witness"]})
    dump(out / "predictions.json", predictions)
    metrics = {}
    for method in predictions[0]["methods"]:
        metrics[method] = {"correct": sum(p["gold"] == p["methods"][method] for p in predictions),
                           "false_pass": sum(p["gold"] == "FAIL" and p["methods"][method] == "PASS" for p in predictions),
                           "unknown_to_pass": sum(p["gold"] == "UNKNOWN" and p["methods"][method] == "PASS" for p in predictions),
                           "unknown_to_fail": sum(p["gold"] == "UNKNOWN" and p["methods"][method] == "FAIL" for p in predictions),
                           "excess_error": sum(p["gold"] != "ERROR" and p["methods"][method] == "ERROR" for p in predictions),
                           "hidden_error": sum(p["gold"] == "ERROR" and p["methods"][method] != "ERROR" for p in predictions)}
    summary = {"protocol_sha256": sha(out / "protocol_frozen.json"), "runtime": runtime, "measured_probe_cells": len(rows), "conditions": 8,
               "queries": len(tasks), "query_families": dict(Counter(t["family"] for t in tasks)), "observed_states": dict(Counter(r["result"] for r in rows)),
               "gold_counts": dict(Counter(p["gold"] for p in predictions)), "metrics": metrics,
               "files_sha256": {p.name: sha(p) for p in sorted(out.iterdir()) if p.is_file()}}
    dump(out / "summary.json", summary)
    print(json.dumps({key: summary[key] for key in ("measured_probe_cells", "queries", "gold_counts", "metrics")}))


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--freeze", action="store_true")
    mode.add_argument("--run", action="store_true")
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    (freeze if args.freeze else run)(args.output.resolve())
