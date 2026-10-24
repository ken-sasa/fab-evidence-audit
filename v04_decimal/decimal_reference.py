"""Independent decimal runtime reference, prepared before outcome collection.

The preferred backend is Python's standard-library _pydecimal implementation;
the primary study implementation uses the C decimal backend. If _pydecimal is
unavailable, the fallback is explicitly identified and does not establish
backend independence. No primary evaluator or runner is imported.

Do not execute or import before the owner freezes the protocol/source hashes
and authorizes execution. Recipe outcome vectors are not encoded in this file.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import sys

try:
    import _pydecimal as B
    BACKEND_NAME = "_pydecimal"
    BACKEND_INDEPENDENT = True
except ImportError:
    import decimal as B
    BACKEND_NAME = "decimal_fallback"
    BACKEND_INDEPENDENT = False


PROFILES = {
    "high_trap": {"prec": 6, "trap_inexact": True},
    "high_open": {"prec": 6, "trap_inexact": False},
    "low_trap": {"prec": 3, "trap_inexact": True},
    "low_open": {"prec": 3, "trap_inexact": False},
}
ROUNDINGS = ("ROUND_HALF_EVEN", "ROUND_HALF_UP")


def now():
    return datetime.now(timezone.utc).isoformat()


def signal_map(mapping):
    return {signal.__name__: bool(value) for signal, value in sorted(mapping.items(), key=lambda pair: pair[0].__name__)}


def snapshot(context):
    return {
        "prec": context.prec,
        "rounding": context.rounding,
        "Emin": context.Emin,
        "Emax": context.Emax,
        "capitals": context.capitals,
        "clamp": context.clamp,
        "flags": signal_map(context.flags),
        "traps": signal_map(context.traps),
    }


def make_context(profile, rounding_name):
    return B.Context(
        prec=profile["prec"],
        rounding=getattr(B, rounding_name),
        Emin=-999,
        Emax=999,
        capitals=1,
        clamp=0,
        flags=[],
        traps=[B.Inexact] if profile["trap_inexact"] else [],
    )


def observe(context, operation):
    observation = {
        "outcome": None,
        "value": None,
        "tuple": None,
        "is_finite": None,
        "returned_type": None,
        "exception": None,
        "flags": None,
    }
    context.clear_flags()
    try:
        value = operation()
        observation["outcome"] = "return"
        observation["value"] = str(value)
        observation["returned_type"] = type(value).__name__
        if isinstance(value, B.Decimal):
            parts = value.as_tuple()
            observation["tuple"] = {
                "sign": parts.sign,
                "digits": list(parts.digits),
                "exponent": parts.exponent,
            }
            observation["is_finite"] = value.is_finite()
    except Exception as exc:
        observation["outcome"] = "exception"
        observation["exception"] = {
            "type": type(exc).__name__,
            "module": type(exc).__module__,
            "message": str(exc),
            "is_exact_Inexact": type(exc) is B.Inexact,
        }
    observation["flags"] = signal_map(context.flags)
    return observation


def no_flags(observation):
    return not any(observation["flags"].values())


def exact_return(observation, expected):
    return (
        observation["outcome"] == "return"
        and observation["is_finite"] is True
        and B.Decimal(observation["value"]) == B.Decimal(expected)
    )


def exact_inexact_exception(observation):
    exc = observation["exception"]
    return observation["outcome"] == "exception" and exc is not None and exc["is_exact_Inexact"]


def classify(requirement, control, probe):
    """Classify directly observed outputs, not the requested profile label."""
    control_expected = {0: "1.23", 1: "2", 2: "0.25"}[requirement]
    control["passed"] = exact_return(control, control_expected) and no_flags(control)
    if not control["passed"]:
        return "ERROR", "valid arithmetic control failed"
    if requirement == 0:
        if exact_return(probe, "1.2346") and no_flags(probe):
            return "PASS", "exact addition returned the required value without flags"
        if exact_inexact_exception(probe):
            return "FAIL", "addition raised the exact Inexact exception"
        if probe["outcome"] == "return" and probe["is_finite"] is True and not exact_return(probe, "1.2346"):
            return "FAIL", "addition returned a different finite value"
        return "ERROR", "unexpected addition observation or flag combination"
    if requirement == 1:
        if exact_return(probe, "3") and no_flags(probe):
            return "PASS", "integral rounding returned three without flags"
        if exact_return(probe, "2") and no_flags(probe):
            return "FAIL", "integral rounding returned two without flags"
        return "ERROR", "unexpected integral-rounding observation or flags"
    if requirement == 2:
        # Static, pre-outcome portability refinement: _pydecimal may raise
        # Inexact before setting Rounded. Rounded is preserved as telemetry,
        # but is not required to recognize the exact trapped Inexact signal.
        if exact_inexact_exception(probe) and probe["flags"].get("Inexact") is True:
            return "PASS", "division raised exact Inexact with its flag set"
        if (
            probe["outcome"] == "return"
            and probe["is_finite"] is True
            and probe["flags"].get("Inexact") is True
            and probe["flags"].get("Rounded") is True
        ):
            return "FAIL", "division returned a rounded finite approximation"
        return "ERROR", "unexpected division observation or flag combination"
    raise ValueError("Unknown requirement")


def measure(config_id, profile, rounding_name, requirement):
    context = make_context(profile, rounding_name)
    row = {
        "config_id": config_id,
        "context": rounding_name,
        "requirement": requirement,
        "result": "ERROR",
        "effective": snapshot(context),
        "control": None,
        "probe": None,
    }
    if requirement == 0:
        control = observe(context, lambda: context.add(B.Decimal("1.20"), B.Decimal(".03")))
        probe = observe(context, lambda: context.add(B.Decimal("1.2345"), B.Decimal(".0001")))
    elif requirement == 1:
        control = observe(context, lambda: B.Decimal("2").to_integral_value(context=context))
        probe = observe(context, lambda: B.Decimal("2.5").to_integral_value(context=context))
    elif requirement == 2:
        control = observe(context, lambda: context.divide(B.Decimal("1"), B.Decimal("4")))
        probe = observe(context, lambda: context.divide(B.Decimal("1"), B.Decimal("3")))
    else:
        raise ValueError("Unknown requirement")
    row["control"] = control
    row["probe"] = probe
    row["result"], row["result_reason"] = classify(requirement, control, probe)
    row["effective_after"] = snapshot(context)
    return row


def collect_reference():
    result = {
        "reference_kind": "independent_decimal_backend_runtime_observation",
        "started_at_utc": now(),
        "metadata": {
            "backend": BACKEND_NAME,
            "backend_independence_established": BACKEND_INDEPENDENT,
            "backend_module_name": B.__name__,
            "backend_module_file": getattr(B, "__file__", None),
            "backend_version": getattr(B, "__version__", None),
            "reported_libmpdec_version": getattr(B, "__libmpdec_version__", None),
            "python_version": sys.version,
            "python_implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "fresh_context_per_probe": True,
        },
        "design": {
            "profiles": PROFILES,
            "rounding_contexts": list(ROUNDINGS),
            "explicit_shared_attributes": {"Emin": -999, "Emax": 999, "capitals": 1, "clamp": 0},
            "flags_initialization": "empty; cleared again before every control and probe",
            "traps_initialization": "Inexact only for trap profiles; otherwise empty",
            "comparison_scope": "Compare measured states, numeric values, exact exception identity and Inexact signal. Preserve other flag differences descriptively.",
            "trap_flag_portability_note": "Before outcomes, static _pydecimal source showed Inexact can be raised before Rounded is set. R2 trapped PASS requires exact Inexact plus Inexact flag, not Rounded flag.",
            "inference_scope": "This Python runtime and these explicit contexts only; no human data, no novel algorithm claim.",
        },
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "observations": [],
        "normalized_records": [],
    }
    for config_id, profile in PROFILES.items():
        for rounding_name in ROUNDINGS:
            for requirement in (0, 1, 2):
                row = measure(config_id, profile, rounding_name, requirement)
                result["observations"].append(row)
                result["normalized_records"].append({
                    "record_id": f"reference:{config_id}:{rounding_name}:{requirement}",
                    "config_id": config_id,
                    "context": rounding_name,
                    "requirement": requirement,
                    "result": row["result"],
                })
    result["completed_at_utc"] = now()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    path = args.output.resolve()
    if path.exists():
        raise FileExistsError("Refusing to overwrite decimal reference observations")
    path.parent.mkdir(parents=True, exist_ok=True)
    result = collect_reference()
    with path.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
    print(json.dumps({"output": str(path), "observations": len(result["observations"]), "backend": result["metadata"]["backend"]}))


if __name__ == "__main__":
    main()
