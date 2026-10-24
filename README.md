# FAB Evidence Audit

Version 0.1.1 is an MIT-licensed reference evaluator for functional evidence that a
query explicitly requires to belong to one configuration in one target context.
It preserves missing observations, audits active errors and conflicts, and returns
supporting record IDs for PASS. Correct Python and SQL controls implement the same
contract. This is not a new logic or a validated measure of technology attainment.

## Install the software

Python 3.10 or newer is declared. Isolated local checks passed on 3.10.22,
3.12.3 and 3.12.14; the GitHub-hosted Ubuntu 24.04 check passed on 3.12.15.
These checks replay saved observations and do not rerun the backend probes.
Python 3.11, 3.13, 3.14 and other operating systems were not tested. From this checkout:

```sh
python -m pip install ./software
fab-evidence-audit software/examples/pass.json
```

Or install the supplied wheel offline into a fresh target:

```sh
python -m pip install --no-index --no-deps --target ../fab-installed release/dist/fab_evidence_audit-0.1.1-py3-none-any.whl
python release/verify_distribution.py --installed-root ../fab-installed --output ../fab-verification.json
python release/run_sqlite_walkthrough.py --installed-root ../fab-installed --output ../fab-walkthrough
```

The verification runs 53 tests outside the source tree, checks five CLI examples
and replays 5376 archived dependent queries against a separate completion oracle.
The walkthrough reconstructs an existing SQLite task from saved control, exception
and readback facts, invokes the installed CLI, and maps its three PASS support IDs
back to the original observations. It rejects a forged ID. Neither operation reruns
SQL or decimal measurements. See [the walkthrough](software/docs/walkthrough.md)
and [reproduction guide](software/docs/reproduction.md).

## Input and decision contract

See [software/README.md](software/README.md), [semantics](software/docs/semantics.md)
and [JSON schema](software/schemas/task.schema.json). Valid computed decisions use
CLI exit 0; malformed input uses exit 2. A conservative syntactic support-work
budget can refuse an otherwise valid task before any decision, using
EvaluationLimit and CLI exit 3. Capacity refusal must not be imputed as an evidence
state. The budget is not a total runtime or memory guarantee.

## Validation material and history

- results/context_v0_2_refined: 5120 constructed context tasks.
- results/sqlite_v0_3: 24 measured cells reused across 128 queries.
- results/decimal_v0_4: 24 measured cells reused across 128 queries.
- Earlier development sources and results are retained under their original names.
- release/dist retains prepared 0.1.0 distributions to recover the original adapter
  hashed by the decimal protocol. Use release/prepare_historical_decimal.py in a
  fresh directory for that historical raw-record re-audit.

Reference and audit programs were prepared in the same AI-assisted workflow. This
is not independent human or research-group replication. The cases are purposive;
derived queries and atomic audit checks are dependent. Recorded decimal Rounded
flag differences are retained. Local protocol hashes are not public preregistration.
The source observations contain invented inputs and software outputs; no human
participant or personal records are included. This software does not modify the
registered Doraemon Index main-study protocol.

## License and citation

Code and the included original synthetic fixtures and software observations are
provided under MIT. Third-party documentation is linked, not redistributed.
CITATION.cff describes this software. Public archive metadata will identify the
fixed release separately. No PyPI registry entry is claimed.

Maintainer: Kento Sasano, Okayama University, office@sasano.org.
Issues may be opened in this repository. No response-time commitment is offered.
