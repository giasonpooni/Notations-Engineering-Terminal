# Notations Systems Terminal

**A programmable scientific and engineering workbench for measurement, state estimation, simulation and interactive worlds.**

[Notation Systems](https://notation.systems) · [Quickstart](#quickstart) ·
[Instruments](docs/INSTRUMENTS.md) · [Integration coverage](docs/INTEGRATION_COVERAGE.md) ·
[Technical reference](TECHNICAL_REFERENCE.md) · [Documentation](docs)

**NET** brings modular computational instruments into one engineering monorepo.
It connects observations, models, parameters, geometry, execution and verification
in retained investigations. The same substrate supports materials and process
development, machinery instrumentation, scientific experiments, and game and
simulation production.

The working loop is **author → run → observe → compare → modify → check**.
Inputs, assumptions, results, failed attempts and explicit checks remain
inspectable. Reopening an investigation reads its retained state; an explicit
replay creates a new execution.

## What NET contains

| Area | Implemented surfaces and guides |
| --- | --- |
| Investigation and execution | Sessions, operation and capability registries, retained runs, save/reopen, explicit replay and runtime preflight. [Instrument catalogue](docs/INSTRUMENTS.md). |
| Typed composition and design | Workflow composition, parameterized Boards, declared input/output contracts and coupled thermomechanical reference configurations. [Workflow algebra](docs/WORKFLOW_ALGEBRA.md) · [Scientific workflows](docs/NET_SCIENTIFIC_WORKFLOWS.md) · [System composition](docs/SYSTEM_COMPOSITION.md). |
| Evolving workflow structure | Bounded typed hypergraph rewriting, invariant checks, retained transformation dependencies, alternative paths and explicit schedule comparisons. [Hypergraph rewriting](docs/HYPERGRAPH_REWRITE.md). |
| Measurement and estimation | Calibration, time reconciliation, telemetry, covariance propagation, state estimation and bounded observation-design workflows. [Integration coverage](docs/INTEGRATION_COVERAGE.md). |
| Scientific models and testbeds | Bounded impact and atmospheric models, thermal observers, geometric calculations and domain-specific numerical checks. [Atmospheric engine](docs/ATMOSPHERIC_ENGINE.md). |
| Polymer processing | Cycle metrology, scoped cooling and pressure-arrival estimates, evidence-linked copilot context, and bounded control simulations for injection and extrusion blow molding. [Polymer processing](docs/POLYMER_PROCESSING.md). |
| Evidence and representation | Typed contracts, identity-bound representations, source correction and dependency status, and signed human/reasoning/vision bundles. [Correction loop](docs/CORRECTION_LOOP.md) · [Legibility instrument](docs/LEGIBILITY.md). |
| Interactive worlds | Supported BIM, spatial and geometric interfaces; native computation and bounded Blender/Godot/Bevy development workflows. [Technical reference](TECHNICAL_REFERENCE.md). |

Support is specific to each profile. The coverage matrix identifies callable
operations, provider requirements, recorded checks and remaining qualification.
Runtime availability, numerical agreement and experimental physical validation
are separate conditions.

Run `net polymer demo --output-dir polymer-demo` for a saved cycle report and audit,
or `net polymer qualify --output-dir polymer-qualification` to exercise both
process profiles and the complete agent workflow. Retained native calibration
import and the finite qualification evidence are described in the
[operator guide](docs/POLYMER_PROCESSING.md).

`net polymer leakage` adds retained coolant-volume and material-mass accounting
through the existing pinned FlowState kernels, with full covariance, replay and
independent numerical audits. The [leakage guide](docs/leakage.md) includes provider
setup and runnable examples; an unaccounted deficit does not establish its cause.

## One repository, modular instruments

The [engineering monorepo](docs/MONOREPO.md) co-locates 21 imported public modules
under [instruments/](instruments), alongside the root Terminal package. Modules
retain their mathematics, package identities, original source history, tests and
licences. Shared contracts connect them through declared interfaces.

| Boundary | Responsibility |
| --- | --- |
| Terminal and composition | Investigation state, configuration, operation selection and dispatch. |
| Measurement and inference | Observations, timing, uncertainty, estimation and diagnostics. |
| Domain engines | Physical models, numerical implementations and their validity domains. |
| Evidence and verification | Provenance, reference checks, correction dependencies and explicit admission boundaries. |
| Representations and clients | Human, reasoning, spatial and visual projections of retained state. |

The Python package remains **`ciw`**; **`net`** is the composition and control CLI.
Both extend the existing Computational Instrumentation Workbench runtime.
Evidence, operation, execution and verification identities remain separate.
NET retains investigation records; canonical evidence/state authority stays at
its declared evidence and admission interfaces.

Blender remains an authoring application. Godot and Bevy retain their own live
worlds. Scientific engines retain their numerical responsibilities. Game state
and industrial evidence keep separate authority even when they share tooling.

The repository consolidates source, but some provider bindings still require
explicit runtime checkouts, executables or credentials. Consolidation alone does
not establish that every workflow can run after an upstream repository is removed.
The migration and qualification guides record the current dependency closure.

## Bounded dynamic fluid models

`net fluid` retains a time-dependent two-reservoir exchange model with a
bidirectionally coupled compliant boundary, and a separate periodic linear
surface-wave profile. Independent references, mass/energy/interface-work
checks, and temporal/spatial refinement qualify their declared numerical
domains. The [fluid guide](docs/FLUID_DYNAMICS.md) includes runnable commands,
model equations, a qualified scalar-state handoff, and explicit expansion
routes. The existing FlowState snapshot estimator and runtime pin remain
unchanged; experimental validation and general molecular/CFD coupling remain
unestablished.

## Evolving workflow structures

`net rewrite` adds an optional, bounded hypergraph computation model for ensemble
construction and workflow auditing. Fixed rules replicate nodes, vary declared
parameters, add typed couplings and split a thermal zone while preserving its
declared heat capacity. Retained histories distinguish content identities from
rewrite execution, result and verification occurrences. Bounded exploration
keeps branch identities; explicit schedule checks compare exact terminal graphs.

```sh
net rewrite example --profile thermal --output results/thermal-request.json
net rewrite run results/thermal-request.json --output results/thermal-rewrite.json
net rewrite verify results/thermal-rewrite.json --output results/thermal-verification.json
```

The [rewrite guide](docs/HYPERGRAPH_REWRITE.md) covers types, limits, replay and
qualification. These transformations describe structure; numerical models still
calculate behavior, and existing operation contracts govern execution. Preserved
declarations do not establish predictive accuracy or validate a proposed mapping
from hypergraph evolution to fundamental physics.

## Quickstart

Follow the [operator quickstart](docs/quickstart.md) to install, check and use the
instrument. `net catalog` shows its command surfaces and scientific provider
requirements; `net start --output-dir results/first-use-001` runs the bounded
synthetic first-use check and retains its completion receipt. Start or resume
one local investigation with `net workbench --output-dir results/workbench`.

The [operator readiness guide](docs/OPERATOR_READINESS.md) explains exact public
provider provisioning and specialist qualification. Scientific provider and
engine dependencies remain optional and explicitly bound. Reading retained
results must not silently launch a runtime or rerun an experiment.

The [installed public-tool guide](docs/RUN_NET.md) adds checked paths through
impact, signed Legibility and atmospheric handoffs. Python 3.11 or newer is
required.


```sh
python -m pip install .
net doctor --profile core
net catalog
net start --output-dir results/first-use-001
net workbench --output-dir results/workbench
```

For the signed legibility demonstration:

```sh
python -m pip install -e ".[legibility]"
ciw legibility demo --output-dir results/legibility-demo
ciw legibility verify results/legibility-demo \
  --trust results/legibility-demo/demo-trust.json --expected-version 1
```

Open `results/legibility-demo/review.html` to inspect the synthetic specimen.
The demo trust anchor exercises key matching; it is not an organizational
identity certificate. See [LEGIBILITY.md](docs/LEGIBILITY.md) for operator keys,
artifact commitments and verification boundaries.

Inspect and audit the imported module registry:

```sh
python scripts/superrepo.py list
python scripts/superrepo.py audit
```

Use a full-history checkout for the source-preservation audit. The
[technical reference](TECHNICAL_REFERENCE.md#quickstart) and module guides provide
the remaining installation, analysis, provider-binding and replay commands.
Graphical clients and scientific providers are optional, explicitly configured
dependencies.

## Development and qualification

NET is in active development. Merged implementations, development branches and
planned capabilities are tracked separately. Broader fluid dynamics, configurable
sensor fusion, atom-trapping simulation and implicit geometry are extension
workstreams; their individual source and qualification records determine availability.
The expanded legibility operator workflow is tracked in
[PR #128](https://github.com/atomtrapping/Notations-Systems-Terminal/pull/128).

Numerical checks are scoped to a declared model and configuration. Production
metrology requires calibrated observations and experimental evidence. Machinery
control requires its own commissioning and authorization. Cryptographic integrity
does not establish physical truth or current source dependencies.

[Integration coverage](docs/INTEGRATION_COVERAGE.md), the
[concerns audit](docs/CONCERNS_AUDIT_2026-10-03.md), and each instrument's operating
guide provide the detailed status. [TECHNICAL_REFERENCE.md](TECHNICAL_REFERENCE.md)
preserves the earlier detailed reference and its historical terminology; current
module guides take precedence for later additions.

## Organization and licence

NET is shared infrastructure developed by **Notation Systems Inc.** across
**Notations Gaming**, **Notation Manufacturing** and **Notations Laboratories**:
interactive worlds, design and manufacturing systems, and scientific research
and validation.

**© 2026 Giason Pooni, for original contributions.** The root code is governed by
the existing [LICENSE](LICENSE). Imported modules retain their own licences and
upstream notices. Project-owned original assets use the
[asset permission policy](docs/licensing/ORIGINAL_ASSETS.md). Repository publication
does not change those terms or confer evidence, signing or execution authority.
