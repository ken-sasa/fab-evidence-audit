"""Audit recorded pilot outputs without importing or executing either evaluator.

This is a post-run integrity/counting audit, not a new experiment, an independent
proof that recorded gold labels are semantically correct, or FAB certification.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path


METHODS = (
    "strict_four_state", "zero_imputation", "mean_aggregation", "short_circuit"
)
STATES = {"PASS", "FAIL", "UNKNOWN", "ERROR"}
SAMPLING = {"exhaustive", "random_with_replacement"}


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def numeric_vector(encoded):
    tokens = encoded.split("|")
    for token in tokens:
        if token not in {"U", "ERROR", "0", "1", "2", "3", "4"}:
            raise ValueError(f"Invalid recorded leaf token: {token!r}")
    return all(token not in {"U", "ERROR"} for token in tokens)


def read_single(path):
    counts = defaultdict(Counter)
    confusion = Counter()
    rows = 0
    with gzip.open(path, "rt", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            rows += 1
            if int(row["case_id"]) != rows:
                raise ValueError("Single-system case IDs are not contiguous")
            threshold = int(row["threshold"])
            if threshold not in range(1, 5):
                raise ValueError("Invalid recorded threshold")
            gold = row["gold"]
            if gold not in STATES:
                raise ValueError("Invalid recorded gold state")
            known = numeric_vector(row["values"])
            for method in METHODS:
                predicted = row[method]
                if predicted not in STATES:
                    raise ValueError("Invalid recorded prediction state")
                metric = counts[method]
                metric["total"] += 1
                metric["correct"] += predicted == gold
                metric["gold_" + gold] += 1
                metric["false_pass"] += gold == "FAIL" and predicted == "PASS"
                metric["unsupported_pass"] += gold == "UNKNOWN" and predicted == "PASS"
                metric["false_fail"] += gold == "PASS" and predicted == "FAIL"
                metric["unknown_forced_decision"] += (
                    gold == "UNKNOWN" and predicted in {"PASS", "FAIL"}
                )
                metric["masked_error"] += gold == "ERROR" and predicted != "ERROR"
                if known:
                    metric["fully_known_total"] += 1
                    metric["fully_known_mismatches"] += predicted != gold
                confusion[method, row["formula"], gold, predicted] += 1
    return rows, counts, confusion


def read_cross(path):
    by_formula = defaultdict(Counter)
    by_sampling = Counter()
    unique_pairs = defaultdict(set)
    rows = 0
    chunk_key = None
    with gzip.open(path, "rt", encoding="utf-8", newline="") as stream:
        for row in csv.DictReader(stream):
            rows += 1
            if int(row["case_id"]) != rows:
                raise ValueError("Cross-system case IDs are not contiguous")
            sampling = row["sampling"]
            if sampling not in SAMPLING:
                raise ValueError("Invalid sampling label")
            threshold = int(row["threshold"])
            # Each evaluated pair draw must be recorded at thresholds 1,2,3,4.
            if threshold != ((rows - 1) % 4) + 1:
                raise ValueError("Cross-system threshold groups are incomplete")
            key = (row["formula"], sampling, row["system_a"], row["system_b"])
            if threshold == 1:
                chunk_key = key
                unique_pairs[sampling].add(key)
            elif key != chunk_key:
                raise ValueError("A four-threshold group mixes different pairs")
            if not numeric_vector(row["system_a"]) or not numeric_vector(row["system_b"]):
                raise ValueError("Cross-system records must contain fully known leaves")
            consistent = int(row["consistent_score"])
            mixed = int(row["mixed_score"])
            if not (0 <= consistent <= mixed <= 4):
                raise ValueError("Invalid or negatively inflated recorded score")
            gold = row["gold"]
            prediction = row["mixed_prediction"]
            if gold != ("PASS" if consistent >= threshold else "FAIL"):
                raise ValueError("Recorded gold is inconsistent with recorded score")
            if prediction != ("PASS" if mixed >= threshold else "FAIL"):
                raise ValueError("Recorded prediction is inconsistent with recorded score")
            metric = by_formula[row["formula"], sampling]
            metric["total"] += 1
            metric["false_pass"] += gold == "FAIL" and prediction == "PASS"
            metric["false_fail"] += gold == "PASS" and prediction == "FAIL"
            metric["oracle_fail"] += gold == "FAIL"
            metric["score_inflated"] += mixed > consistent
            metric["total_score_gap"] += mixed - consistent
            by_sampling[sampling] += 1
    if rows % 4:
        raise ValueError("Final cross-system pair draw has missing thresholds")
    total = Counter()
    for metric in by_formula.values():
        total.update(metric)
    return rows, total, by_formula, by_sampling, unique_pairs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    folder = args.results.resolve()
    output = args.output.resolve() if args.output else folder / "audit_report.json"
    code_root = Path(__file__).resolve().parent
    report = {
        "audit_kind": "post_run_read_only_count_and_integrity_audit",
        "audited_at_utc": datetime.now(timezone.utc).isoformat(),
        "audit_code_sha256": sha256(Path(__file__).resolve()),
        "scope": [
            "Recount recorded CSV columns independently of engine and oracle imports.",
            "Check confusion/summary counts, sampling denominators and score-column invariants.",
            "Verify frozen-protocol hash and its three recorded source-code hashes.",
            "Original protocol, code, raw data and summary are not modified.",
        ],
        "limitations": [
            "Recorded gold labels are inputs; their semantics are not independently recomputed here.",
            "Threshold cases share configuration pairs and are not independent observations.",
            "This audit is not a new experiment or registered FAB production certification.",
        ],
        "checks": [],
    }

    def check(name, actual, expected):
        report["checks"].append({
            "name": name, "passed": actual == expected,
            "actual": actual, "expected": expected,
        })

    def compare_counter(name, actual, expected):
        differences = {
            key: {"actual": actual.get(key, 0), "expected": expected.get(key, 0)}
            for key in sorted(set(actual) | set(expected))
            if actual.get(key, 0) != expected.get(key, 0)
        }
        check(name, differences, {})

    try:
        summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
        protocol = json.loads((folder / "protocol_frozen.json").read_text(encoding="utf-8"))
        report["study_id"] = summary["study_id"]
        check("protocol_hash_matches_summary", sha256(folder / "protocol_frozen.json"), summary["protocol_sha256"])
        check("original_frozen_source_set", sorted(protocol["code_sha256"]), ["engine.py", "oracle.py", "run_pilot.py"])
        for name, expected in sorted(protocol["code_sha256"].items()):
            if Path(name).name != name:
                raise ValueError("Unsafe source filename in protocol")
            check("frozen_source_hash:" + name, sha256(code_root / name), expected)

        single_rows, metrics, confusion = read_single(folder / "single_system_cases.csv.gz")
        check("single_rows_expected", single_rows, 63504)
        check("single_rows_match_summary", single_rows, summary["single_system_cases"])
        for method in METHODS:
            compare_counter("single_metrics:" + method, metrics[method], summary["single_system_metrics"][method])
        check("strict_mismatches", single_rows - metrics["strict_four_state"]["correct"], 0)
        check("known_mean_mismatches", metrics["mean_aggregation"]["fully_known_mismatches"], 4174)
        check("known_mean_denominator", metrics["mean_aggregation"]["fully_known_total"], 17200)
        recorded_confusion = Counter()
        with (folder / "confusion.csv").open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                recorded_confusion[row["method"], row["formula"], row["gold"], row["prediction"]] += int(row["count"])
        check("confusion_table_matches_raw_rows", dict(recorded_confusion) == dict(confusion), True)

        cross_rows, cross_total, cross_formula, sampling, unique_pairs = read_cross(folder / "cross_system_cases.csv.gz")
        check("cross_rows_expected", cross_rows, 303000)
        check("cross_rows_match_summary", cross_rows, summary["cross_system_threshold_cases"])
        compare_counter("cross_metrics_match_summary", cross_total, summary["cross_system_metrics"])
        check("cross_false_pass", cross_total["false_pass"], 16870)
        check("cross_gold_fail_denominator", cross_total["oracle_fail"], 107558)
        check("cross_false_fail", cross_total["false_fail"], 0)
        check("exhaustive_threshold_cases", sampling["exhaustive"], 255000)
        check("sampled_threshold_cases", sampling["random_with_replacement"], 48000)
        check("sampling_counts_sum", sum(sampling.values()), cross_rows)
        check("exhaustive_count_matches_summary", sampling["exhaustive"], summary["exhaustive_cross_threshold_cases"])
        check("sampled_count_matches_summary", sampling["random_with_replacement"], summary["random_cross_threshold_cases"])
        expected_formula = {(row["formula"], row["sampling"]): {key: value for key, value in row.items() if key not in {"formula", "sampling"}} for row in summary["cross_system_by_formula"]}
        check("cross_formula_keys_match_summary", sorted(cross_formula), sorted(expected_formula))
        for key in cross_formula:
            compare_counter("cross_formula:" + "/".join(key), cross_formula[key], expected_formula.get(key, {}))
        with (folder / "cross_system_summary.csv").open(encoding="utf-8", newline="") as stream:
            from_csv = {}
            for row in csv.DictReader(stream):
                key = row.pop("formula"), row.pop("sampling")
                if key in from_csv:
                    raise ValueError("Duplicate cross-system summary row")
                from_csv[key] = {field: int(value) for field, value in row.items()}
        check("cross_summary_csv_keys", sorted(from_csv), sorted(cross_formula))
        for key in cross_formula:
            compare_counter("cross_summary_csv:" + "/".join(key), cross_formula[key], from_csv.get(key, {}))

        report["recounted_single_system_metrics"] = {method: dict(metrics[method]) for method in METHODS}
        report["recounted_cross_system_metrics"] = dict(cross_total)
        report["denominators"] = {
            "single_system_threshold_cases": single_rows,
            "known_mean_mismatch_numerator": metrics["mean_aggregation"]["fully_known_mismatches"],
            "known_mean_mismatch_denominator": metrics["mean_aggregation"]["fully_known_total"],
            "cross_system_threshold_cases": cross_rows,
            "cross_false_pass_incidence_denominator": cross_rows,
            "cross_false_positive_rate_denominator": cross_total["oracle_fail"],
            "pair_draws_with_all_four_thresholds": cross_rows // 4,
            "pair_draws_by_sampling": {key: value // 4 for key, value in sampling.items()},
            "unique_formula_labelled_ordered_pairs_by_sampling": {key: len(value) for key, value in unique_pairs.items()},
            "interpretation": "Pairs can repeat under sampling with replacement; formula families have unequal weights in the pooled case distribution.",
        }
        report["source_sha256"] = {name: sha256(folder / name) for name in (
            "protocol_frozen.json", "summary.json", "single_system_cases.csv.gz",
            "cross_system_cases.csv.gz", "confusion.csv", "cross_system_summary.csv"
        )}
        report["status"] = "PASS" if all(item["passed"] for item in report["checks"]) else "FAIL"
    except Exception as exc:
        report["status"] = "ERROR"
        report["exception"] = {"type": type(exc).__name__, "message": str(exc)}
    report["audit_completed_at_utc"] = datetime.now(timezone.utc).isoformat()
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "checks": len(report["checks"]), "audit_report": str(output)}, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
