# Installation and reproduction

The software source distribution and the wider study archive serve different
purposes. The source distribution contains the API, CLI, schema, examples,
documentation and tests. Development generators, raw measurements, completion
oracles and case audits are in the wider study archive. No public download URL
or permanent identifier is claimed until an actual release is deposited.

## Minimal software check

From an unpacked source distribution, using a local Python environment:

```sh
python -m pip install .
fab-evidence-audit examples/pass.json
fab-evidence-audit examples/mixed_configuration.json
python -m unittest discover -s tests -v
```

The first decision is PASS with configuration a, context target and record IDs
a0 and a1. The second is FAIL with a null witness. All 53 tests should succeed.
The declared minimum is Python 3.10; only CPython 3.12.14 on Linux x86_64 was
executed for this release. Source builds need setuptools 77 or newer.

When that build dependency is already available, source installation can use
`--no-build-isolation --no-deps --no-index`. The wheel requires no runtime
third-party packages and can be installed offline with `--no-deps --no-index`.

## Installed distribution replay from the study archive

Run from the study archive root, which contains software/, release/ and results/.
Choose a new target directory and output report; do not overwrite saved reports.

```sh
python -m pip install --no-deps --no-index --target /tmp/fab_audit_011 \
  release/dist/fab_evidence_audit-0.1.1-py3-none-any.whl
python release/verify_distribution.py --installed-root /tmp/fab_audit_011 \
  --output /tmp/fab_audit_011_report.json
```

The verifier copies tests outside the source tree, imports from the installed
target, checks five CLI examples and replays 5376 archived queries against the
separate completion oracle. It verifies the unchanged core against the historical
context evaluator. This replay performs no new SQLite or decimal operations and
does not constitute a new empirical sample. `release/verification_v0_1_1.json`
records the current check; `release/verification.json` retains version 0.1.0.

The saved decimal protocol also hashes its original 0.1.0 adapter. To re-audit
that original run after upgrading the public adapter, restore its sources in a
fresh directory using the retained 0.1.0 source archive:

```sh
python release/prepare_historical_decimal.py --output /tmp/fab_decimal_historical
python /tmp/fab_decimal_historical/v04_decimal/audit_decimal.py \
  --results /tmp/fab_decimal_historical/results/decimal_v0_4
```

The helper verifies every frozen source hash, preserves the saved audit as
`saved_audit_report.json` in that copy and leaves the original study unchanged.
It does not rerun arithmetic. Do not change the saved protocol's hashes to
match the current adapter. A fresh freeze/run below uses the current adapter
and constitutes a new local execution, not a reconstruction of the old freeze.

## Re-executing documented software probes

From the study root, choose unused output directories. Run Python without `-O`,
as the retained audit and verification programs use assertions. Runtime versions
may affect values and flags; a difference from a saved run is not automatically
a defect. The stored run used SQLite 3.53.1 and CPython 3.12.14 with decimal 1.70
and libmpdec 4.0.0. Backend observation runs are separate from package replay.

```sh
python v03_sqlite/run_sqlite_case.py --freeze --output results/sqlite_new
python v03_sqlite/run_sqlite_case.py --run --output results/sqlite_new
python v03_sqlite/sqlite_reference.py --output results/sqlite_new/sqlite_reference.json
python v03_sqlite/audit_sqlite.py results/sqlite_new

python v04_decimal/run_decimal.py --freeze --output results/decimal_new
python v04_decimal/run_decimal.py --run --output results/decimal_new
python v04_decimal/decimal_reference.py --output results/decimal_new/decimal_reference.json
python v04_decimal/audit_decimal.py --results results/decimal_new
```

The synthetic context generator can also be rerun into a fresh directory:

```sh
python v02_refined/run_context.py --output results/context_new
python v02_refined/audit_context.py --results results/context_new \
  --output results/context_new/audit_report.json
```

The protocol freezes are local records, not external preregistration. Reference
and audit programs were prepared within the same AI-assisted workflow, without
independent human or group replication. Existing experiment sources and outputs
remain unchanged by version 0.1.1.
