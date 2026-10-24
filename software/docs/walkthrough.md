# From a saved observation to an installed CLI witness

This example demonstrates reuse of an existing evidence table. It reconstructs
one three-requirement task from the archived SQLite observation facts, evaluates
it with the installed FAB Evidence Audit 0.1.1 CLI, and checks every witness ID
against its original observation. It executes no SQL probe and adds no measured
cell or independent validation sample.

The installable source distribution supplies the API, examples and documentation.
This end-to-end example additionally needs the **wider study archive**, including
`release/run_sqlite_walkthrough.py` and `results/sqlite_v0_3/`; these historical
records are not part of the standalone wheel or source distribution.

From the wider study root, install the supplied wheel into a new local target:

```sh
python -m pip install --no-index --no-deps --target ../fab-installed \
  release/dist/fab_evidence_audit-0.1.1-py3-none-any.whl
```

Then one command runs the walkthrough:

```sh
python release/run_sqlite_walkthrough.py \
  --installed-root ../fab-installed --output ../walkthrough-output
```

The output directory must not already exist. The script requires installed
distribution metadata for version 0.1.1. Its CLI subprocess uses Python isolated
mode and explicitly selects that installed package directory; it rejects an
import from a source checkout or global package as a fallback. Use the same
Python interpreter for installation and execution.

The script performs the following steps:

1. Read the saved controls, accepted/rejected writes, exception names/codes,
   readbacks and cleanup facts in `observations.json`; derive each result without
   using its saved `result` label, then require agreement with that label.
2. Construct the `strict` configuration task with target `before_begin` and
   `AND(0,1,2)` from the frozen protocol. Retain both recorded contexts in the
   task, so the evaluator must exclude off-target observations. Require exact
   equality with archived task
   `complete-before_begin-strict-both_with_unique`.
3. Supply the reconstructed task on stdin to the installed CLI. Require PASS
   and the three-record witness shown below.
4. Independently check positive support and the active-cell integrity policy
   against the original observations, without importing the evaluator. Replace
   one witness ID with an absent ID and require rejection of that forged witness.

Expected output files:

| File | Contents |
| --- | --- |
| `task.json` | Six reconstructed records: three requirements in each of two contexts |
| `decision.json` | Installed CLI PASS decision with a three-record support witness |
| `record_source_map.json` | All six record IDs mapped to JSON pointers in the original observation/condition files, with requirement text and raw observation facts |
| `report.json` | Version, hashes, exact archived-task comparison, witness audit and forged-ID rejection |

The support records are:

| Record ID | Requirement | Original observation pointer |
| --- | --- | --- |
| `sql-before_begin-strict-r0` | Reject orphan foreign-key insertion | `observations.json#/0` |
| `sql-before_begin-strict-r1` | Reject a CHECK violation | `observations.json#/1` |
| `sql-before_begin-strict-r2` | Reject duplicate non-null UNIQUE token | `observations.json#/2` |

The source map also gives their `conditions.json` pointer and the requested
settings, effective readbacks, schema digest and saved SQL trace. Source hashes
are checked again before writing output; the historical input files are never
modified. A digest records byte identity, not source authenticity.

The witness audit is deliberately limited to this declared functional contract.
It does not establish general engineering validity, physical integration,
independent reproduction of SQLite behavior, or usefulness to a user population.
To rerun the original measurements, use the separate reproduction commands in
the wider study `README.txt`; do not interpret this walkthrough as a new run.
