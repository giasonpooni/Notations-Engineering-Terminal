# Cyber-physical mathematical contract audit

Original audit baseline: `d313351f96aad73fe40229b7d705b18c12e96b64`.
Reconciled source baseline: `ffad7dba2b258339b27170e071c0de431f6f2aef`.
Scope: shared operation, retained-result, observation-projection and preservation
boundaries, plus the indexed operator inventory. This is an implementation audit,
not a claim that every engine has experimental validation or NET is qualified
for physical control.

## Findings and enforcement

| Finding | Enforced behavior | Regression evidence |
| --- | --- | --- |
| Extension payload schemas did not bind their operation role. A saved historical-authoring result could be resealed as a verification result and reopened. | Trusted schema registration binds the role; execution and reopening refuse conflicting roles. A provider without an offline schema refuses before execution. Existing backend registrations remain compatible; DSP declares analysis explicitly. | `tests/test_operation_contract_enforcement.py` |
| Direct runner calls could calculate from modified source values while retaining a stale evidence digest. | The runner detaches inputs and validates source contracts and evidence identity before runtime discovery or provider invocation. | `tests/test_operation_contract_enforcement.py` |
| An unknown or removed result schema could fall back to the legacy reader, bypassing modern role and occurrence checks. | Modern envelopes have an exact supported shape; legacy results have the original closed shape and two historical analysis IDs. Genuine legacy workspaces remain readable. Rejected workspaces produce no destination writes. | `tests/test_saved_result_discriminator.py` |
| A caller could project the analytic oscillator as an acquired observation. | Observed projections require retained observed-origin declarations without conflicting source/channel semantics. Fixed analytic sources cannot acquire observed status by annotation. These checks do not authenticate an acquisition device. | `tests/test_control_plane.py` |
| A verified preservation contract could yield eligibility despite omitting a property whose loss policy protects it. | An omitted protected effect yields `UNRESOLVED`; explicit forbidden loss yields `REFUSED`. Reopening recomputes that decision. Eligibility still does not admit state or grant actuation. | `tests/test_preservation_contracts.py` |
| Tool growth had no review-coverage drift gate across operator navigation and Workbench workflows. | `net catalog --audit` derives the inventory from existing authorities and fails unreviewed, removed, duplicate or replaced workflow identities. It records reference coverage separately from executed qualification. | `tests/test_scientific_audit.py` |

Existing contracts already enforce ordered finite samples, explicit unit/frame
bindings, full covariance axis/reference bindings, scale-normalized PSD checks,
immutable retained identities, explicit provider binding, refusal history, and
separate verification/admission fields. This increment extends those contracts.

## Recurring enforcement

```bash
net catalog --audit --repository . --json
python -m pytest -q tests/test_scientific_audit.py \
  tests/test_operation_contract_enforcement.py \
  tests/test_saved_result_discriminator.py \
  tests/test_observation_origin_enforcement.py \
  tests/test_control_plane.py tests/test_preservation_contracts.py
```

The audit currently covers **51 indexed command surfaces, 28 Workbench workflow
kinds and ten mathematical ladder areas**. The review map does not register or
execute tools. New indexed surfaces require explicit review references. A
version change under an existing workflow kind also fails until reviewed.

With `--repository`, the audit checks that referenced source/test files exist
inside that checkout. Without it, installed use reports source references as
unchecked. Neither mode runs the referenced tests or asserts their results.
Nested subcommands, library functions and provider-internal operations remain
the responsibility of domain suites; command-level coverage is not exhaustive
operation-level qualification.

The unfiltered `Workflow contracts` CI job runs the coverage audit and shared
regressions on pull requests and main pushes. The existing full Python suite
also discovers the new tests. Domain-specific native/installed gates remain
required for their own claims.

## Mathematical ladder: scope still requiring evidence

| Foundation | Existing bounded enforcement | Open obligation |
| --- | --- | --- |
| Identity, logic and types | Source hashes, versioned schemas, roles, retained occurrence links | Content integrity does not establish source authenticity. |
| Composition | Explicit provider binding and schema/unit/frame ports | Generic ports do not express every quantity, shape, clock, uncertainty or entity constraint. |
| Geometry and representation | Domain geometry contracts and explicit preservation effects | Finite examples are not universal equivalence proofs. |
| Dynamics and physical scale | Profile-specific equations and source/result validators | General multiphysics coupling, closure assumptions and cross-scale error require separate validation. |
| Measurement and uncertainty | Covariance structure, units, references and calibration profiles | Empirical calibration traceability and uncertainty-model adequacy remain workload-specific. |
| Signals and time | Declared timestamps/clocks and bounded DSP profiles | Correct sampling, synchronization and delay bounds need acquisition-specific evidence. |
| Inference | Declared estimator profiles and retained uncertainty | Observability, identifiability and statistical consistency cannot be assumed from a successful run. |
| Intervention | Bounded proposals, declared effects and transition envelopes | Physical actuation requires a separately commissioned authorization/control boundary. |
| Hybrid and distributed coordination | Bounded mode changes and workflow occurrences | General hybrid reachability, real-time deadlines and distributed recovery are not established. |
| Assurance | Scoped numerical checks, replay and verification artifacts | Solver convergence, physical validation, formal proof and operational qualification are distinct obligations. |

Portable manifest model, input, output and verification dictionaries still
permit broad metadata. Their presence is not a general scientific type checker.
Operation registration remains trusted host configuration; execution now
requires the corresponding trusted payload schema and role. Further per-domain
declarations must attach to those existing interfaces.

Preservation composition tracks property availability by declared ID.
`FORGET P` alongside `TRANSFORM Q -> P` can make P available downstream; that
does not prove equivalence to the original P. An information-preservation claim
requires the corresponding explicit verification evidence.

## Historical PR validation record

The initial broad baseline run was interrupted by shared-environment disk
exhaustion and is not counted as a passing run. Regression testing used
memory-backed temporary storage and loopback access for service tests. Optional
native-provider skips remain unqualified, not passes. The original PR's reported validation counts
were **584 passed, 41 skipped, 33 subtests passed** across 21 focused test
modules after the review fixes. The skips require optional native DSP or
scientific-provider bindings. CI routing checks also passed (13 tests and the
workflow policy check); the catalog audit passed its declared coverage scope.

A second broad run was stopped after storage failures: 2,877 tests had passed,
but write failures and incomplete execution prevent any full-suite success
claim. GitHub's full suite and domain-specific qualification remain outstanding.

## Reconciliation with the integrated research candidate

The shared changes are applied to main's existing runner and Session, preserving
irrigation and leakage source dependency checks, payload dispatch and runtime
validation. Their four built-in operation roles remain reserved. The coverage
map now includes `net irrigation`, `net polymer leakage` and `net system`, with explicit
implementation and regression references. No imported instrument tree changes.

Observed projection also refuses the existing analytic, simulated, estimated,
reference and computed markers when they occur in either `origin` or
`source_class`; an observed label cannot override a contradictory retained
declaration. Focused regression cases cover these markers and the four retained
irrigation/leakage and five system role bindings. Scientific composition source
and result dependency checks remain in the existing runner and Session; saved
restoration checks all registered domain families.

The reconciliation was reviewed against GitHub source and blob identities.
Local process startup was unavailable because the desktop sandbox failed
initialization, so no execution success is claimed for this integrated tree.
The counts above remain historical PR evidence. The shared contract workflow,
full Prototype matrix and affected domain qualification must run on the final
integrated candidate before release.
