"""Restore the frozen decimal audit sources in a fresh directory.

The current public adapter may differ from the adapter hashed by the original
experiment. Obtain that version from the retained 0.1.0 source distribution;
never edit frozen protocols or overwrite current software to make hashes match.
No arithmetic probes or evaluator code are executed by this preparation script.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import tarfile


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    study = Path(__file__).resolve().parents[1]
    destination = args.output.resolve()
    if destination.exists():
        raise FileExistsError("Choose a new historical replay directory")
    protocol = json.loads((study / "results/decimal_v0_4/protocol_frozen.json").read_text())
    restored = {}
    archive = study / "release/dist/fab_evidence_audit-0.1.0.tar.gz"
    with tarfile.open(archive, "r:gz") as stream:
        for member in stream.getmembers():
            parts = PurePosixPath(member.name).parts
            if member.isfile() and len(parts) > 1 and parts[1] == "src":
                path = PurePosixPath("software", *parts[1:])
                if ".." in path.parts:
                    raise ValueError("Unsafe archived source path")
                restored[str(path)] = stream.extractfile(member).read()
    if not restored:
        raise ValueError("No historical source files found")
    for name, expected in protocol["source_hashes"].items():
        relative = PurePosixPath(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe protocol source path")
        data = restored.get(name)
        if data is None:
            data = (study / name).read_bytes()
            restored[name] = data
        if sha(data) != expected:
            raise ValueError("Cannot restore frozen source: " + name)
    destination.mkdir(parents=True)
    for name in ("v02_refined", "v03_sqlite", "v04_decimal"):
        shutil.copytree(study / name, destination / name,
                        ignore=shutil.ignore_patterns("__pycache__"))
    for name, data in restored.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    results = destination / "results/decimal_v0_4"
    shutil.copytree(study / "results/decimal_v0_4", results)
    (results / "audit_report.json").rename(results / "saved_audit_report.json")
    report = {
        "purpose": "Restore original frozen sources for raw-record re-audit; no new measurements",
        "historical_distribution": archive.name,
        "historical_distribution_sha256": sha(archive.read_bytes()),
        "frozen_sources_checked": len(protocol["source_hashes"]),
        "restored_adapter_sha256": sha(restored["software/src/fab_evidence_audit/api.py"]),
        "protocol_sha256": sha((study / "results/decimal_v0_4/protocol_frozen.json").read_bytes()),
        "destination": str(destination),
    }
    (destination / "RESTORED_SOURCES.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
