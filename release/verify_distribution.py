"""Verify an already installed distribution outside its source tree."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--installed-root", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    study = Path(__file__).resolve().parents[1]
    installed = args.installed_root.resolve()
    destination = args.output.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    sys.path.insert(0, str(installed))
    import fab_evidence_audit as api
    assert Path(api.__file__).resolve().is_relative_to(installed)
    assert hashlib.sha256((installed / "fab_evidence_audit/_core.py").read_bytes()).hexdigest() == hashlib.sha256((study / "v02_refined/engine_context.py").read_bytes()).hexdigest()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(installed)
    with tempfile.TemporaryDirectory() as temp:
        temp = Path(temp)
        tests = temp / "tests"
        tests.mkdir()
        for src in (study / "software/tests").glob("test_*.py"):
            shutil.copy2(src, tests / src.name)
        # test_cli's source fallback resolves to this nonexistent temp/src;
        # therefore its subprocesses also import the installed distribution.
        ran = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", str(tests), "-v"], cwd=temp, env=env, capture_output=True, text=True, check=False)
        if ran.returncode:
            raise AssertionError(ran.stderr)
        test_count = int(re.search(r"Ran (\d+) tests?", ran.stderr).group(1))
        cli_checks = []
        for name, expected in [("pass", "PASS"), ("fail", "FAIL"), ("unknown", "UNKNOWN"), ("error", "ERROR"), ("mixed_configuration", "FAIL")]:
            result = subprocess.run([sys.executable, "-m", "fab_evidence_audit", str(study / "software/examples" / (name + ".json"))], cwd=temp, env=env, text=True, capture_output=True, check=False)
            assert result.returncode == 0 and not result.stderr
            assert json.loads(result.stdout)["state"] == expected
            cli_checks.append({"example": name, "expected": expected, "passed": True})
    spec = importlib.util.spec_from_file_location("distribution_oracle", study / "v02_refined/oracle_context.py")
    oracle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oracle)
    def tuples(x):
        return tuple(tuples(c) for c in x) if isinstance(x, list) else x
    replay = []
    for name in ("context_v0_2_refined", "sqlite_v0_3", "decimal_v0_4"):
        parent = study / "results" / name
        path = parent / ("tasks.jsonl.gz" if name == "context_v0_2_refined" else "tasks.jsonl")
        opener = gzip.open if path.suffix == ".gz" else open
        counts = Counter()
        with opener(path, "rt", encoding="utf-8") as stream:
            for line in stream:
                task = json.loads(line)
                task["formula"] = tuples(task["formula"])
                result = api.audit(task)
                assert result["state"] == oracle.oracle(task)
                assert api.check_witness(task, result)
                counts[result["state"]] += 1
        replay.append({"archive": name, "cases": sum(counts.values()), "states": dict(counts), "oracle_mismatches": 0, "invalid_package_witness_checks": 0})
    report = {"verified_at_utc": datetime.now(timezone.utc).isoformat(), "python": sys.version,
              "package_version": api.__version__, "imported_installed_distribution": True,
              "preserved_core_byte_identical": True, "unittest_count": test_count, "unittest_exit_code": ran.returncode,
              "unittest_log": ran.stderr, "installed_cli_examples": cli_checks, "archive_replay": replay,
              "replay_total": sum(r["cases"] for r in replay),
              "scope": "Package regression checks on existing dependent tasks. Package check_witness shares its core; independent historical audits remain separate. No new independent empirical samples or new backend operations in replay."}
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ("package_version", "unittest_count", "unittest_exit_code", "replay_total", "preserved_core_byte_identical")}))


if __name__ == "__main__":
    main()
