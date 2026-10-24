# Query semantics

A valid task asks whether one listed configuration satisfies its monotone
expression in the stated target context. Only referenced requirements are active.
It does not ask whether separate configurations collectively cover the leaves.

1. Validate all fields before interpreting results.
2. Check the conservative support-work budget. If exceeded, raise EvaluationLimit
   without computing a verdict, even if the data would otherwise yield ERROR.
3. Select target-context records for active configuration-requirement cells.
4. Any explicit ERROR or opposed PASS/FAIL blocks the whole task as ERROR.
5. For error-free cells, evaluate lower and upper completions of missing leaves.
   True lower bound means PASS; false upper bound means FAIL; otherwise UNKNOWN.
6. Configurations are alternatives: any PASS wins after the global integrity
   gate; all FAIL gives FAIL; otherwise UNKNOWN.
7. A PASS witness cites actual, consistent positive records in one configuration
   and the target context that suffice for the expression.

An OR route may establish PASS despite unrelated missing leaves. An input error
in another active route still blocks it under the chosen audit policy. Failure
and missing evidence therefore cannot be closed interchangeably. Repeated leaf
references use one requirement's evidence, not independent copies.
Unknown cells range over all Boolean completions without additional cross-cell
constraints. For monotone formulas, the all-false and all-true completions give
the required lower and upper bounds.

`audit` chooses a deterministic positive support witness. `check_witness`
verifies the global state and positive support sufficiency; it need not receive
the chosen witness and does not require minimum cardinality or canonical IDs.

The implementation reuses the preserved v0.2 evaluation core unchanged. The
public validation and JSON adapter are new; historical studies remain archived
under their original sources and do not silently become package-version tests.

## Operational work budget in version 0.1.1

The preserved core enumerates Cartesian products while constructing deduplicated
support sets, before subset pruning. Size bounds alone are insufficient. Before invoking it,
both `audit` and `check_witness` apply this conservative syntactic estimate:

- A leaf has candidate bound U = 1 and work W = 1.
- For OR, U is the sum of child candidate bounds.
- For AND, U is the product of child candidate bounds.
- At either internal node, W is the sum of child work plus U plus U squared.
- Query work is root W multiplied by the number of declared configurations.

All additions and multiplications saturate at 1000001. Query work above 1000000
raises `EvaluationLimit`. The candidate count assumes all leaves positive;
the squared term accounts conservatively for pairwise subset comparisons.
These are abstract work units, not seconds, bytes, or measured operation counts.
Deduplication, pruning and actual negative/missing evidence can make evaluation
much cheaper than this estimate. Equivalent formulas may differ in whether they
are accepted. `normalize` performs shape validation without this capacity check.

For example, let G be an AND of eight OR pairs over 16 distinct requirements.
G has 256 raw support combinations. The valid 101-node expression AND(G,G,G,G)
would require the old core to enumerate 256 to the fourth power combinations at
its root despite deduplication of their unions. Version 0.1.1 refuses it before calling the core.
The regression test mocks the core to ensure the large product is never run.

The budget covers support expansion and pruning only. It is not a bound on
total runtime, memory use, JSON parsing or record scans. The CLI distinguishes
computed decisions (exit 0), invalid input (exit 2) and capacity refusal
(exit 3). Refusal produces no evidence decision and must not be imputed as
FAIL, UNKNOWN or ERROR by a calling pipeline.
