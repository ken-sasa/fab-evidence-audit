"""Document-grounded SQLite case study. Standard library only.

No result vector is hard-coded: behavior records come from SQL executions.
Run --freeze first; then --run with an existing, unchanged local protocol.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
from itertools import combinations
import json
from pathlib import Path
import platform
import sqlite3
import sys

HERE = Path(__file__).resolve().parent
STUDY = HERE.parent
PROFILES = {"strict": (1, 0), "fk_only": (1, 1), "check_only": (0, 0), "open": (0, 1)}
PHASES = ("before_begin", "after_begin")
FORMULAS = {
    "both": ("AND", 0, 1),
    "either": ("OR", 0, 1),
    "both_with_unique": ("AND", 0, 1, 2),
    "either_with_unique": ("AND", ("OR", 0, 1), 2),
}
SOURCE_FILES = ("run_sqlite_case.py", "sqlite_reference.py", "audit_sqlite.py", "sources.json")
LINKED_FILES = ("v02_refined/engine_context.py", "v02_refined/oracle_context.py")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def freeze(out):
    out.mkdir(parents=True, exist_ok=False)
    sources = {str(Path("v03_sqlite") / name): digest(HERE / name) for name in SOURCE_FILES}
    sources.update({name: digest(STUDY / name) for name in LINKED_FILES})
    protocol = {
        "version": "v0.3 SQLite executable case study",
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "Exploratory purposive case study; source selection informed by documentation, not a blinded holdout",
        "local_commitment_only": True,
        "source_outcomes_accessed_before_freeze": False,
        "known_contracts": "Expected documented effects were known before execution; this is not outcome blindness.",
        "selection": {
            "domain": "Official SQLite documentation and local SQLite runtime only; outside main FAB corpus",
            "inclusion": ["public official behavioral contract", "deterministic local in-memory execution", "no human or personal data", "configuration or setting-application-phase distinction"],
            "sampling": "Purposive: foreign_keys and ignore_check_constraints; UNIQUE is a constant positive control",
            "exclusions": ["performance", "concurrency", "disk persistence", "network", "recursive triggers", "other engines or versions"],
            "no_outcome_based_exclusion": True,
        },
        "profiles": PROFILES,
        "phases": PHASES,
        "phase_definition": "Explicit baseline FK=OFF and ignore_check=OFF; create schema; apply requested settings either before or after BEGIN. All probes then execute inside that transaction.",
        "configuration_identity": "Requested recipe plus recorded runtime/build/schema. Phase is a context; effective settings and in-transaction state are recorded for every probe. Names are not statements that requested settings took effect.",
        "requirements": {"0": "reject orphan foreign key insertion", "1": "reject CHECK(value>0) violation", "2": "reject duplicate non-null UNIQUE token"},
        "measurement": "Each of eight connection conditions has three savepoint-isolated probes on one schema/connection: a valid control, one isolated violating insert, a row-count check, and cleanup. Expected constraint-specific IntegrityError means PASS; accepted violating write means FAIL; unexpected exception/control/count/readback failure means ERROR, never auto-PASS.",
        "formulas": FORMULAS,
        "query_design": {"complete": "all 15 nonempty subsets of 4 profiles x 2 phases x 4 formulas =120", "withheld": "strict-only x 2 phases x 4 formulas, remove FK observation in target phase =8", "record_pool": "both phases for included profiles, enabling context-loss contrasts", "unit": "24 measured probe cells from 8 connection conditions; 128 dependent constructed queries, not independent technology samples"},
        "methods": ["witness_evaluator", "relational_reference", "sql_relational", "config_only_conservative", "context_only_conservative", "optimistic_pool", "missing_as_fail"],
        "metrics": ["oracle agreement", "known FAIL to PASS", "UNKNOWN to PASS or FAIL", "excess ERROR", "hidden ERROR", "valid witnesses", "primary vs independent runtime reference on all 24 cells"],
        "integrity_policy": "Unchanged v0.2 task-wide referenced target-cell ERROR priority; legitimate constraint rejection is a requirement PASS, not an input ERROR.",
        "replication": "Separate reference module independently authored in the same agent workflow; not independent human/group reproduction or a second SQLite implementation.",
        "inference": "Descriptive counts only; no confidence interval, population prevalence or statistical superiority claim",
        "changes": "No planned outcome-based changes; failures retained. Any correction requires a separately named amended run and explicit disclosure.",
        "ethics_scope": "Only artificial SQL values, public technical documents and local software outputs; no human participants, personal data or main-study evidence ratings",
        "sources": json.loads((HERE / "sources.json").read_text()),
        "source_hashes": sources,
    }
    write_json(out / "protocol_frozen.json", protocol)
    print(json.dumps({"protocol": str(out / "protocol_frozen.json"), "sha256": digest(out / "protocol_frozen.json")}))


def observe():
    observations, conditions = [], []
    for phase in PHASES:
        for config, (fk, ignore) in PROFILES.items():
            conn = sqlite3.connect(":memory:", isolation_level=None)
            sql_trace = []
            conn.set_trace_callback(sql_trace.append)
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute("PRAGMA ignore_check_constraints=OFF")
            conn.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
            conn.execute("CREATE TABLE child(id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES parent(id), value INTEGER CHECK(value>0), token TEXT UNIQUE)")
            conn.execute("INSERT INTO parent VALUES(1)")
            schema = conn.execute("SELECT name,sql FROM sqlite_schema WHERE sql IS NOT NULL ORDER BY name").fetchall()
            schema_sha256 = hashlib.sha256(json.dumps(schema, separators=(",", ":")).encode()).hexdigest()
            if phase == "after_begin":
                conn.execute("BEGIN")
            conn.execute(f"PRAGMA foreign_keys={fk}")
            conn.execute(f"PRAGMA ignore_check_constraints={ignore}")
            if phase == "before_begin":
                conn.execute("BEGIN")
            condition = {"config_id": config, "context": phase, "condition_id": f"primary-{phase}-{config}",
                         "requested": {"foreign_keys": fk, "ignore_check_constraints": ignore}, "schema": schema,
                         "schema_sha256": schema_sha256, "sql_trace": sql_trace}
            for requirement, invalid, expected in [
                (0, (2, 999, 1, "new"), "SQLITE_CONSTRAINT_FOREIGNKEY"),
                (1, (2, 1, -1, "new"), "SQLITE_CONSTRAINT_CHECK"),
                (2, (2, 1, 1, "base"), "SQLITE_CONSTRAINT_UNIQUE"),
            ]:
                start = len(sql_trace)
                effective = {key: conn.execute(f"PRAGMA {key}").fetchone() for key in ("foreign_keys", "ignore_check_constraints")}
                row = {"record_id": f"sql-{phase}-{config}-r{requirement}", "config_id": config, "context": phase, "requirement": requirement,
                       "expected_constraint": expected, "effective": effective, "in_transaction": conn.in_transaction, "result": "ERROR"}
                savepoint_created = False
                try:
                    conn.execute("SAVEPOINT probe")
                    savepoint_created = True
                    row["initial_count"] = conn.execute("SELECT COUNT(*) FROM child").fetchone()[0]
                    conn.execute("INSERT INTO child VALUES(?,?,?,?)", (1, 1, 1, "base"))
                    row["control_count"] = conn.execute("SELECT COUNT(*) FROM child").fetchone()[0]
                    row["control_row"] = conn.execute("SELECT * FROM child WHERE id=1").fetchone()
                    row["control_accepted"] = row["control_row"] == (1, 1, 1, "base")
                    try:
                        conn.execute("INSERT INTO child VALUES(?,?,?,?)", invalid)
                        row["violating_write"] = "accepted"
                    except sqlite3.IntegrityError as exc:
                        row["violating_write"] = "rejected"
                        row["exception_name"] = getattr(exc, "sqlite_errorname", None)
                        row["exception_code"] = getattr(exc, "sqlite_errorcode", None)
                    row["final_count"] = conn.execute("SELECT COUNT(*) FROM child").fetchone()[0]
                    readback_ok = all(value is not None and len(value) == 1 and value[0] in (0, 1) for value in effective.values())
                    if row["initial_count"] == 0 and row["control_accepted"] and row["control_count"] == 1 and readback_ok and row["in_transaction"]:
                        if row["violating_write"] == "accepted" and row["final_count"] == 2:
                            row["result"] = "FAIL"
                        elif row["violating_write"] == "rejected" and row.get("exception_name") == expected and row["final_count"] == 1:
                            row["result"] = "PASS"
                except sqlite3.Error as exc:
                    row["unexpected_exception"] = {"type": type(exc).__name__, "name": getattr(exc, "sqlite_errorname", None), "message": str(exc)}
                finally:
                    try:
                        if savepoint_created:
                            conn.execute("ROLLBACK TO probe")
                            conn.execute("RELEASE probe")
                        row["cleanup_count"] = conn.execute("SELECT COUNT(*) FROM child").fetchone()[0]
                        row["cleanup_transaction_active"] = conn.in_transaction
                        if not savepoint_created or row["cleanup_count"] != 0 or not row["cleanup_transaction_active"]:
                            row["result"] = "ERROR"
                    except sqlite3.Error as exc:
                        row["cleanup_exception"] = {"type": type(exc).__name__, "message": str(exc)}
                        row["result"] = "ERROR"
                row["sql_trace"] = sql_trace[start:].copy()
                observations.append(row)
            conn.execute("ROLLBACK")
            conditions.append(condition)
            conn.close()
    return observations, conditions


def expression_sql(node):
    if type(node) is int:
        if node not in range(3):
            raise ValueError("requirement out of domain")
        return f"r{node}"
    if node[0] not in ("AND", "OR"):
        raise ValueError("operator out of domain")
    return "(" + (" " + node[0] + " ").join(expression_sql(child) for child in node[1:]) + ")"


def sql_relational(task, active):
    """Executed relational SQL baseline, including NULL and task-wide error gate."""
    db = sqlite3.connect(":memory:")
    db.executescript("CREATE TABLE configs(cfg TEXT PRIMARY KEY); CREATE TABLE reqs(req INTEGER PRIMARY KEY); CREATE TABLE evidence(cfg TEXT,ctx TEXT,req INTEGER,result TEXT);")
    db.executemany("INSERT INTO configs VALUES(?)", [(c,) for c in task["configs"]])
    db.executemany("INSERT INTO reqs VALUES(?)", [(r,) for r in sorted(active)])
    db.executemany("INSERT INTO evidence VALUES(?,?,?,?)", [(r["config_id"], r["context"], r["requirement"], r["result"]) for r in task["records"]])
    sql = """WITH cells AS (
      SELECT c.cfg,q.req,
        CASE WHEN SUM(e.result='ERROR')>0 OR (SUM(e.result='PASS')>0 AND SUM(e.result='FAIL')>0) THEN 1 ELSE 0 END AS err,
        CASE WHEN SUM(e.result='PASS')>0 THEN 1 WHEN SUM(e.result='FAIL')>0 THEN 0 ELSE NULL END AS val
      FROM configs c CROSS JOIN reqs q LEFT JOIN evidence e
      ON e.cfg=c.cfg AND e.req=q.req AND e.ctx=? GROUP BY c.cfg,q.req
    ), pivoted AS (
      SELECT cfg,MAX(err) AS err,
        MAX(CASE WHEN req=0 THEN val END) AS r0,
        MAX(CASE WHEN req=1 THEN val END) AS r1,
        MAX(CASE WHEN req=2 THEN val END) AS r2
      FROM cells GROUP BY cfg
    ), evaluated AS (SELECT err,""" + expression_sql(task["formula"]) + """ AS verdict FROM pivoted)
    SELECT CASE WHEN MAX(err)=1 THEN 'ERROR' WHEN MAX(verdict)=1 THEN 'PASS'
      WHEN SUM(verdict IS NULL)>0 THEN 'UNKNOWN' ELSE 'FAIL' END FROM evaluated"""
    state = db.execute(sql, (task["target_context"],)).fetchone()[0]
    db.close()
    return state, sql


def make_tasks(observations):
    records = [{key: row[key] for key in ("config_id", "context", "requirement", "result", "record_id")} for row in observations]
    tasks = []
    for phase in PHASES:
        for size in range(1, 5):
            for configs in combinations(PROFILES, size):
                for label, formula in FORMULAS.items():
                    tasks.append({"task_id": f"complete-{phase}-{'-'.join(configs)}-{label}", "family": "complete", "formula_id": label,
                                  "formula": formula, "requirements": 3, "configs": list(configs), "target_context": phase,
                                  "records": [r for r in records if r["config_id"] in configs]})
        for label, formula in FORMULAS.items():
            tasks.append({"task_id": f"withheld-{phase}-{label}", "family": "withheld", "formula_id": label,
                          "formula": formula, "requirements": 3, "configs": ["strict"], "target_context": phase,
                          "records": [r for r in records if r["config_id"] == "strict" and not (r["context"] == phase and r["requirement"] == 0)]})
    return tasks


def run(out):
    protocol = json.loads((out / "protocol_frozen.json").read_text())
    for name, expected in protocol["source_hashes"].items():
        if digest(STUDY / name) != expected:
            raise RuntimeError("Frozen source changed: " + name)
    if (out / "summary.json").exists():
        raise FileExistsError("Do not overwrite a completed run")
    engine = load_module("context_engine_v03", STUDY / "v02_refined/engine_context.py")
    oracle = load_module("context_oracle_v03", STUDY / "v02_refined/oracle_context.py")
    conn = sqlite3.connect(":memory:")
    metadata = {"python": sys.version, "platform": platform.platform(), "sqlite_version": sqlite3.sqlite_version,
                "sqlite_source_id": conn.execute("SELECT sqlite_source_id()").fetchone()[0],
                "compile_options": [r[0] for r in conn.execute("PRAGMA compile_options")],
                "started_at_utc": datetime.now(timezone.utc).isoformat()}
    conn.close()
    observations, conditions = observe()
    write_json(out / "observations.json", observations)
    write_json(out / "conditions.json", conditions)
    write_json(out / "runtime.json", metadata)
    tasks = make_tasks(observations)
    with (out / "tasks.jsonl").open("w") as handle:
        for task in tasks:
            handle.write(json.dumps(task) + "\n")
    predictions = []
    sql_templates = {}
    for task in tasks:
        gold = oracle.oracle(task)
        if isinstance(gold, dict):
            gold = gold["state"]
        result = engine.evaluate(task)
        states = {"witness_evaluator": result["state"]}
        for method in ("relational_reference", "config_only_conservative", "context_only_conservative", "optimistic_pool", "missing_as_fail"):
            states[method] = getattr(engine, method)(task)
        states["sql_relational"], sql = sql_relational(task, engine.leaves(task["formula"]))
        sql_templates[task["formula_id"]] = sql
        predictions.append({"task_id": task["task_id"], "family": task["family"], "gold": gold, "methods": states, "witness": result["witness"]})
    write_json(out / "predictions.json", predictions)
    write_json(out / "sql_templates.json", sql_templates)
    metrics = {}
    for method in predictions[0]["methods"]:
        metrics[method] = {"correct": sum(p["gold"] == p["methods"][method] for p in predictions),
                           "false_pass": sum(p["gold"] == "FAIL" and p["methods"][method] == "PASS" for p in predictions),
                           "excess_error": sum(p["gold"] != "ERROR" and p["methods"][method] == "ERROR" for p in predictions),
                           "unknown_to_pass": sum(p["gold"] == "UNKNOWN" and p["methods"][method] == "PASS" for p in predictions),
                           "unknown_to_fail": sum(p["gold"] == "UNKNOWN" and p["methods"][method] == "FAIL" for p in predictions),
                           "hidden_error": sum(p["gold"] == "ERROR" and p["methods"][method] != "ERROR" for p in predictions)}
    summary = {"protocol_sha256": digest(out / "protocol_frozen.json"), "runtime": metadata, "measured_probe_cells": len(observations),
               "connection_conditions": len(conditions), "observed_states": dict(Counter(r["result"] for r in observations)),
               "queries": len(tasks), "query_families": dict(Counter(t["family"] for t in tasks)), "gold_counts": dict(Counter(p["gold"] for p in predictions)), "metrics": metrics,
               "no_natural_unknown_or_conflicting_records": True, "withholding_is_derived_control": True,
               "files_sha256": {p.name: digest(p) for p in sorted(out.iterdir()) if p.is_file()}}
    write_json(out / "summary.json", summary)
    print(json.dumps({k: summary[k] for k in ("measured_probe_cells", "connection_conditions", "queries", "gold_counts", "metrics")}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--freeze", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    (freeze if args.freeze else run)(args.output.resolve())
