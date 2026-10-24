"""Independent SQLite runtime reference for a synthetic transaction-setting study.

This source defines measured behavior, not expected recipe outcome vectors.
Each recipe/phase uses a fresh in-memory connection. Every probe runs within the
same outer transaction for that condition and restores its own savepoint.
No evaluator or primary study runner is imported.

Do not execute this module until the study owner has frozen the protocol/source
hashes and explicitly authorized execution.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import sqlite3
import sys


RECIPES = {
    "strict": {"foreign_keys": 1, "ignore_check_constraints": 0},
    "fk_only": {"foreign_keys": 1, "ignore_check_constraints": 1},
    "check_only": {"foreign_keys": 0, "ignore_check_constraints": 0},
    "open": {"foreign_keys": 0, "ignore_check_constraints": 1},
}
PHASES = ("before_begin", "after_begin")

SCHEMA = """
CREATE TABLE telemetry_parent (
    parent_id INTEGER PRIMARY KEY
);
CREATE TABLE telemetry_sample (
    sample_id INTEGER PRIMARY KEY,
    parent_id INTEGER NOT NULL,
    reading INTEGER NOT NULL,
    token TEXT NOT NULL,
    CONSTRAINT positive_reading CHECK (reading > 0),
    CONSTRAINT unique_sample_token UNIQUE (token),
    CONSTRAINT sample_parent_reference FOREIGN KEY (parent_id)
        REFERENCES telemetry_parent (parent_id)
);
"""
INSERT_SAMPLE = "INSERT INTO telemetry_sample (sample_id, parent_id, reading, token) VALUES (?, ?, ?, ?)"

# These describe the constraint-specific rejection each requirement demands.
# They do not encode which recipe or phase will produce such a rejection.
PROBES = (
    {
        "requirement": "reject_orphan_foreign_key",
        "savepoint": "reference_fk",
        "control_token": "control_fk",
        "violation": (11, 900001, 5, "violation_fk"),
        "exception_name": "SQLITE_CONSTRAINT_FOREIGNKEY",
        "exception_code": 787,
        "exception_message": "FOREIGN KEY constraint failed",
    },
    {
        "requirement": "reject_negative_check_value",
        "savepoint": "reference_check",
        "control_token": "control_check",
        "violation": (11, 7, -1, "violation_check"),
        "exception_name": "SQLITE_CONSTRAINT_CHECK",
        "exception_code": 275,
        "exception_message": "CHECK constraint failed: positive_reading",
    },
    {
        "requirement": "reject_duplicate_unique_token",
        "savepoint": "reference_unique",
        "control_token": "control_unique",
        "violation": (11, 7, 5, "control_unique"),
        "exception_name": "SQLITE_CONSTRAINT_UNIQUE",
        "exception_code": 2067,
        "exception_message": "UNIQUE constraint failed: telemetry_sample.token",
    },
)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def exception_details(exc):
    return {
        "python_type": type(exc).__name__,
        "message": str(exc),
        "sqlite_errorcode": getattr(exc, "sqlite_errorcode", None),
        "sqlite_errorname": getattr(exc, "sqlite_errorname", None),
    }


def read_settings(connection):
    values = {}
    for name in ("foreign_keys", "ignore_check_constraints"):
        row = connection.execute("PRAGMA " + name).fetchone()
        if row is None or len(row) != 1 or row[0] not in (0, 1):
            raise RuntimeError("Required PRAGMA has no supported Boolean readback: " + name)
        values[name] = int(row[0])
    return values


def apply_settings(connection, requested):
    for name in ("foreign_keys", "ignore_check_constraints"):
        value = requested[name]
        if type(value) is not int or value not in (0, 1):
            raise ValueError("Only explicit Boolean recipe values are accepted")
        connection.execute("PRAGMA " + name + " = " + str(value))


def expected_constraint_exception(exc, definition):
    """Require the intended IntegrityError subtype, not just any failed write."""
    if not isinstance(exc, sqlite3.IntegrityError):
        return False
    code = getattr(exc, "sqlite_errorcode", None)
    name = getattr(exc, "sqlite_errorname", None)
    if code is not None and code != definition["exception_code"]:
        return False
    if name is not None and name != definition["exception_name"]:
        return False
    # The named schema gives a precise fallback on Python versions that do not
    # expose SQLite's extended code/name. Require the same message in all cases.
    return str(exc) == definition["exception_message"]


def run_probe(connection, definition, trace):
    first_trace = len(trace)
    result = {
        "requirement": definition["requirement"],
        "state": "ERROR",
        "transaction_active_before": connection.in_transaction,
        "valid_control": None,
        "violation_parameters": list(definition["violation"]),
        "required_exception": {
            "python_type": "IntegrityError",
            "sqlite_errorname": definition["exception_name"],
            "sqlite_errorcode": definition["exception_code"],
            "message": definition["exception_message"],
        },
        "observed_exception": None,
        "violating_write_accepted": None,
        "cleanup": None,
    }
    savepoint = definition["savepoint"]
    savepoint_created = False
    try:
        if not connection.in_transaction:
            raise RuntimeError("Probe is not inside the required outer transaction")
        connection.execute("SAVEPOINT " + savepoint)
        savepoint_created = True
        if connection.execute("SELECT count(*) FROM telemetry_sample").fetchone()[0] != 0:
            raise RuntimeError("A preceding probe left sample rows behind")
        control = (10, 7, 5, definition["control_token"])
        try:
            connection.execute(INSERT_SAMPLE, control)
            observed = connection.execute(
                "SELECT sample_id, parent_id, reading, token FROM telemetry_sample WHERE sample_id = ?",
                (10,),
            ).fetchone()
            if observed != control:
                raise RuntimeError("Valid insertion control did not persist the requested row")
            result["valid_control"] = {"accepted": True, "row": list(observed)}
        except Exception as exc:
            result["valid_control"] = {"accepted": False, "exception": exception_details(exc)}
            result["error_reason"] = "valid insertion control failed"
            return result

        try:
            connection.execute(INSERT_SAMPLE, definition["violation"])
        except Exception as exc:
            result["observed_exception"] = exception_details(exc)
            result["violating_write_accepted"] = False
            if not expected_constraint_exception(exc, definition):
                result["error_reason"] = "write failed with an unexpected exception"
            else:
                invalid_count = connection.execute(
                    "SELECT count(*) FROM telemetry_sample WHERE sample_id = ?", (11,)
                ).fetchone()[0]
                control_count = connection.execute(
                    "SELECT count(*) FROM telemetry_sample WHERE sample_id = ?", (10,)
                ).fetchone()[0]
                if invalid_count != 0 or control_count != 1:
                    result["error_reason"] = "constraint exception did not preserve the expected rows"
                else:
                    result["state"] = "PASS"
        else:
            observed = connection.execute(
                "SELECT sample_id, parent_id, reading, token FROM telemetry_sample WHERE sample_id = ?",
                (11,),
            ).fetchone()
            if observed != definition["violation"]:
                result["error_reason"] = "accepted violating write has no matching row"
            else:
                result["violating_write_accepted"] = True
                result["accepted_violating_row"] = list(observed)
                result["state"] = "FAIL"
    except Exception as exc:
        result["error_reason"] = "probe setup or observation failed"
        result["probe_exception"] = exception_details(exc)
        result["state"] = "ERROR"
    finally:
        if savepoint_created:
            try:
                connection.execute("ROLLBACK TO SAVEPOINT " + savepoint)
                connection.execute("RELEASE SAVEPOINT " + savepoint)
                left = connection.execute("SELECT count(*) FROM telemetry_sample").fetchone()[0]
                if left != 0 or not connection.in_transaction:
                    raise RuntimeError("Savepoint cleanup lost the outer transaction or retained rows")
                result["cleanup"] = {"restored_empty_samples": True, "outer_transaction_active": True}
            except Exception as exc:
                result["cleanup"] = {"exception": exception_details(exc)}
                result["error_reason"] = "savepoint cleanup failed"
                result["state"] = "ERROR"
        result["transaction_active_after"] = connection.in_transaction
        result["sql_trace"] = trace[first_trace:]
    return result


def measure_condition(recipe_name, requested, phase):
    if phase not in PHASES:
        raise ValueError("Unsupported application phase")
    trace = []
    result = {
        "recipe": recipe_name,
        "application_phase": phase,
        "requested_settings": dict(requested),
        "condition_status": "ERROR",
        "baseline_settings_before_schema": None,
        "settings_inside_transaction_after_application": None,
        "probes": [],
    }
    connection = None
    try:
        connection = sqlite3.connect(":memory:", isolation_level=None)
        connection.set_trace_callback(trace.append)
        apply_settings(connection, {"foreign_keys": 0, "ignore_check_constraints": 0})
        result["baseline_settings_before_schema"] = read_settings(connection)
        if result["baseline_settings_before_schema"] != {"foreign_keys": 0, "ignore_check_constraints": 0}:
            raise RuntimeError("Explicit pre-schema baseline settings did not take effect")
        connection.executescript(SCHEMA)
        connection.execute("INSERT INTO telemetry_parent (parent_id) VALUES (?)", (7,))
        if connection.in_transaction:
            raise RuntimeError("Baseline/schema preparation unexpectedly left an active transaction")
        result["sqlite_compile_options"] = [
            row[0] for row in connection.execute("PRAGMA compile_options").fetchall()
        ]
        if phase == "before_begin":
            apply_settings(connection, requested)
            connection.execute("BEGIN")
        else:
            connection.execute("BEGIN")
            apply_settings(connection, requested)
        result["transaction_active_at_measurement"] = connection.in_transaction
        if not connection.in_transaction:
            raise RuntimeError("Measurement did not enter the required transaction")
        result["settings_inside_transaction_after_application"] = read_settings(connection)
        for definition in PROBES:
            result["probes"].append(run_probe(connection, definition, trace))
        result["settings_inside_transaction_after_probes"] = read_settings(connection)
        result["condition_status"] = (
            "MEASURED" if all(probe["state"] in {"PASS", "FAIL"} for probe in result["probes"])
            else "ERROR"
        )
    except Exception as exc:
        result["condition_exception"] = exception_details(exc)
        result["condition_status"] = "ERROR"
    finally:
        if connection is not None:
            try:
                if connection.in_transaction:
                    connection.execute("ROLLBACK")
                connection.close()
            except Exception as exc:
                result["close_exception"] = exception_details(exc)
                result["condition_status"] = "ERROR"
        result["sql_trace"] = trace
    return result


def collect_reference():
    started = utc_now()
    result = {
        "reference_kind": "independent_sqlite_runtime_constraint_observation",
        "started_at_utc": started,
        "metadata": {
            "python_version": sys.version,
            "python_implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "sqlite_runtime_version": sqlite3.sqlite_version,
            "sqlite_runtime_version_info": list(sqlite3.sqlite_version_info),
            "connection_isolation_level": None,
            "database": ":memory:",
            "fresh_connection_per_recipe_phase": True,
        },
        "method": {
            "baseline_before_schema": {"foreign_keys": 0, "ignore_check_constraints": 0},
            "measurement_scope": "Post-application PRAGMA readbacks and all three behavior probes occur inside the outer transaction; the explicit baseline readback is separately recorded before schema creation.",
            "pass_definition": "Valid insertion succeeds; violating insertion is rejected by the required constraint-specific IntegrityError; cleanup preserves the outer transaction.",
            "fail_definition": "A valid insertion control succeeds and the violating row is accepted and observed.",
            "error_definition": "Control failure, unexpected exception, inconsistent observation, unsupported PRAGMA or failed cleanup.",
            "inference_scope": "Measurements characterize this Python/SQLite runtime only; they are not a new algorithm or human-subject result.",
        },
        "schema_sql": SCHEMA,
        "insert_sql": INSERT_SAMPLE,
        "conditions": [],
    }
    for recipe_name, requested in RECIPES.items():
        for phase in PHASES:
            result["conditions"].append(measure_condition(recipe_name, requested, phase))
    result["source_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    result["completed_at_utc"] = utc_now()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    destination = args.output.resolve()
    if destination.exists():
        raise FileExistsError("Refusing to overwrite a reference measurement output")
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = collect_reference()
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps({"output": str(destination), "conditions_measured": len(result["conditions"])}))


if __name__ == "__main__":
    main()
