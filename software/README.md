# FAB Evidence Audit

FAB Evidence Audit is a small reference implementation for evaluating functional
evidence that a research query explicitly requires to belong to one configuration
in one common context. It retains missing evidence and returns supporting record
identifiers with every successful decision. It supports reproducible testing of
assessment software; it does not authenticate sources or rate technology progress.

## Install and run

Python 3.10 or newer is required. The runtime has no third-party dependencies.
From this directory, install with:

```sh
python -m pip install .
fab-evidence-audit examples/pass.json
python -m fab_evidence_audit examples/unknown.json
python -m unittest discover -s tests -v
```

Building the package requires setuptools 77 or newer. With that build dependency
already available, `python -m pip install . --no-build-isolation --no-deps` works
offline. A wheel included in the research release can also be installed offline.

```python
import json
from fab_evidence_audit import audit, check_witness

with open("examples/pass.json", encoding="utf-8") as f:
    task = json.load(f)
result = audit(task)
assert check_witness(task, result)
print(result)
```

## Input and output

A task has a monotone AND/OR expression, a requirement count, candidate
configuration identifiers, a target context and records. Integer leaves start at
zero. Operator arrays require at least two children. Every record identifies its
configuration, context, requirement, result and unique record ID. The portable
structure is described in `schemas/task.schema.json`; domain bounds, unique
configuration/record IDs and cross-field references are checked by the Python API.

```json
{"formula":["AND",0,1],"requirements":2,"configs":["a"],
 "target_context":"target",
 "records":[
   {"record_id":"a0","config_id":"a","context":"target","requirement":0,"result":"PASS"},
   {"record_id":"a1","config_id":"a","context":"target","requirement":1,"result":"PASS"}
 ]}
```

The positive example returns:

```json
{"state":"PASS","witness":{"config_id":"a","context":"target","record_ids":["a0","a1"]}}
```

`audit(task)` returns a state (`PASS`, `FAIL`, `UNKNOWN`, or `ERROR`) and a witness.
A PASS witness contains one configuration, the target context and sufficient
positive record IDs. Other states have a null witness. A malformed task raises
`InvalidTask`; it is not a valid evidence task with decision ERROR. The CLI exits
zero for any valid decision, including FAIL, UNKNOWN and ERROR. Invalid input
returns exit code 2 with a JSON diagnostic on stderr and no decision on stdout.
An otherwise valid task can exceed the conservative support-work budget. In that
case `audit` and `check_witness` raise `EvaluationLimit` before computing a
decision; the CLI emits `EVALUATION_LIMIT` on stderr, no stdout decision, and
exit code 3. This is an operational refusal, not a fifth evidence state. JSON
output uses reversible ASCII escapes, including for non-ASCII identifiers.

## Meaning and limits

The query's same-context requirement must be justified by the intended use. It is
not a universal engineering rule. Evidence from a demonstrated integration can be
entered under its own configuration; the tool does not infer compatibility.

Consistent target-cell PASS/FAIL rows mean true/false. No row means unknown.
Opposing PASS/FAIL or explicit ERROR in an active target cell blocks the whole
query, even if another configuration has a successful route. Other contexts are
excluded. A user needing a different audit policy must explicitly version it.

The public interface limits a task to 16 distinct requirements, 64 configurations,
100000 rows, 256 formula nodes, depth 24 and 256-character identifiers. Witness
enumeration may grow exponentially even within these size limits. Version 0.1.1
therefore checks a fixed budget of 1000000 abstract support-work units before
calling the preserved evaluator. The syntactic estimate assumes positive leaves
and charges candidate expansion and pairwise pruning across every configuration.
It ignores actual outcomes and pruning savings, so even a cheap valid task or a
task that would return ERROR can be refused. `normalize` checks shape only;
successful normalization does not imply that evaluation fits this budget.
See `docs/semantics.md` for the exact estimate. The budget does not bound total
runtime or memory, including input parsing and record scans. This is a local
reference tool, not a performance-optimized or untrusted multi-user service.

`audit` selects a shortest witness among its constructed candidates; no global
optimality claim is made. `check_witness` checks the overall state and positive
support sufficiency; it accepts sufficient noncanonical or redundant support
sets and does not require the generator's tie-breaking or minimum cardinality.
It shares the evaluator and is a convenience check. Separate completion-oracle
audits are in the research package.

## Reuse

Use the JSON interface to connect an existing evidence table or test-result
pipeline, retain the source-to-record mapping, and validate configuration and
context definitions before interpreting a decision. Templates cover positive,
negative, missing, conflicting and mixed-configuration evidence.

Correct relational processing can implement the same semantics. The package
offers an explicit reference contract, examples and evidence witnesses; it is
not claimed to outperform SQL, PROV, RO-Crate or other provenance tools. See
`docs/semantics.md` and `docs/comparison.txt` for scope and limitations.

For a concrete boundary check, `examples/mixed_configuration.json` gives two
requirements PASS/FAIL in configuration a and FAIL/PASS in configuration b.
The AND query returns FAIL. Pooling positives across configurations would
incorrectly return PASS. Reuse this four-record fixture to check your own
pipeline's grouping behavior.

The source distribution includes the schema, five examples, documentation and
53 tests. The wider study archive separately contains generated tasks, raw
observations, frozen protocols and reference/audit programs. See
`docs/reproduction.md` for installation and full-archive reproduction commands.

## Development and licensing

Source repository: https://github.com/ken-sasa/fab-evidence-audit

Version 0.1.1 uses the MIT license. The source repository includes the wider
validation material and `release/run_sqlite_walkthrough.py`; see
`docs/walkthrough.md` for the path from saved observations to an installed CLI
decision and source-mapped support witness. A separate archive identifier will
be supplied with the deposited release; no PyPI entry is claimed. The package
does not change the main Doraemon Index study protocol or analyze human data.
