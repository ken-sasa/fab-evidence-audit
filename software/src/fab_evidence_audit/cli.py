"""One JSON task on stdin or a file; one JSON result on stdout."""
import argparse
import json
import sys
from . import EvaluationLimit, InvalidTask, __version__, audit


def _pairs(items):
    value = {}
    for key, entry in items:
        if key in value:
            raise InvalidTask("duplicate JSON object key: " + key)
        value[key] = entry
    return value


def _constant(value):
    raise InvalidTask("nonstandard JSON numeric constant: " + value)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task", nargs="?", default="-", help="JSON path, or - for stdin")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)
    try:
        if args.task == "-":
            raw = sys.stdin.read()
        else:
            with open(args.task, encoding="utf-8") as handle:
                raw = handle.read()
        task = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
        result = audit(task)
    except EvaluationLimit as exc:
        print(json.dumps({"error": "EVALUATION_LIMIT", "message": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 3
    except (OSError, UnicodeError, json.JSONDecodeError, InvalidTask, RecursionError) as exc:
        print(json.dumps({"error": "INVALID_INPUT", "message": str(exc)}, ensure_ascii=True), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
