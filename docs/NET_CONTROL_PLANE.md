# NET control primitives

NET can describe an experiment, dispatch its operations through the existing
Python `ciw.Session`, retain observations, and inspect or compare the results.
The implementation is additive: five optional modules, one test module, a
focused qualification workflow, and the `net` entry point. Existing `ciw`
commands, scientific implementations, schema dispatch and Session are unchanged.
There is no new mandatory dependency.

## Run a complete local example

Install this revision, then use a new output directory for each execution:

```sh
python -m pip install -e '.[dev]'
net providers
net capabilities analyze
net demo --output-dir results/control-001
net inspect results/control-001/workspace.json
net inspect experiment results/control-001/experiment.json
net inspect results/control-001/graph-run.json --json
net compare results/control-001/observations.json results/control-001/observations.json --atol 0
```

`python -m ciw.net` is equivalent to `net`. The demo uses the **existing analytic
damped oscillator source and existing statistics/periodogram operations**, not
new mathematics, engine doubles or a native simulation qualification. It creates
two ordinary CIW execution/result pairs and retained observations. Comparing a
file with itself demonstrates transport/checking, not independent validation.

To explicitly execute the retained graph against its retained source again:

```python
from pathlib import Path
from ciw.net import main
recording = next(Path("results/control-001").glob("recording-*.json"))
assert main(["run", "results/control-001/experiment.json", "--run", str(recording),
             "--output-dir", "results/control-002"]) == 0
```

Equivalently, pass that recording filename to `net run ... --run ...`.
Re-execution uses a new Session/output directory and fresh CIW
execution/result identities. This is not checkpoint continuation or a claim that
arbitrary engines are deterministic. No command overwrites an existing output
directory or comparison file.

## Boundaries implemented

| Primitive | Implementation | Boundary |
| --- | --- | --- |
| Typed state | `state`, `observation`, strict validators | Model/entity/occurrence, named clock with seconds, quantity, scalar/vector, units, frame, provenance, optional full covariance |
| Capabilities | `CapabilityRegistry` over `InstrumentManifest` and `OperationRegistry` | Declaration is not executable registration; no plugin scan, imports from JSON, or marketplace |
| Experiment graph | `experiment`, `plan_graph`, `run_graph` | At most 64 nodes, sequential DAG, typed named ports, dependency blocking; dispatch through existing Session |
| Observation bus | `ObservationBus`, `observations_from_run` | Bounded in-process append-only stream; pull/snapshot API, not a telemetry database or network subscription service |
| Parameters | `Interval`, `Choice`, `Fixed`, `ParameterSpace` | Bounded finite grids and explicit-seed samples; optimizers/calibrators remain provider operations |
| Assertions | `verify`, `overall` | PASS/FAIL/INDETERMINATE for declared numerical conditions; never physical acceptance or an ordinary result promoted to a verification occurrence |
| Comparison | `compare` | Componentwise RMSE, maximum absolute error and tolerance excess; exact matching time grid, no interpolation |
| Checkpoint/re-execution | `capture_checkpoint`, `restore_checkpoint`, `run_graph` | Provider-owned opaque bytes; explicit fresh-owner restore; graph rerun creates fresh CIW occurrences |
| Artifacts/provenance | `artifact`, `specification_id` | Exact payload SHA256 and separate specification identity; no artifact store or automatic retrieval |
| Units/frames | State, observation and `Port` contracts | Explicit labels, exact matching, full covariance axis/unit/frame bindings; no inferred unit conversion or coordinate transform |
| Inspection | `net inspect` | Supported control records, provider catalogs and existing CIW workspaces; no provider calculation or automatic restore |

Wire names are namespaced `ciw.state.v1`, `ciw.observation.v1`,
`ciw.experiment.v1`, `ciw.artifact.v1`, `ciw.comparison.v1`,
`ciw.verification.v1`, `ciw.checkpoint.v1`, `ciw.parameter-space.v1`,
`ciw.observation-stream.v1` and `ciw.graph-run.v1`. They are additive control
records, not reinterpretations of `run.v1` or the existing operation payloads.
The new `ciw.verification.v1` is explicitly an ordinary assertion record with
`verification_id: null`; it does not allocate a verification occurrence.

## State, covariance and observations

A state contains `identity={model_id,entity_id,execution_id}`, a
`clock={id,time_s}`, a frame, named variables, provenance and optional uncertainty.
An observation contains one named scalar/vector quantity with the same binding.
`execution_id: null` explicitly means no execution occurrence is known for the
retained source. Source projection requires the caller to supply model/entity,
clock and observed/estimated/simulated/reference semantics; it never guesses.
An `observed` projection additionally requires the retained source's
`metadata.provenance.semantics` to be `observed`, an `instrument`, `record_only`
or `measurement_adapter` adapter role, and no conflicting structured source/channel
origin. The fixed
analytic oscillator always refuses an observed projection. Unknown origins,
computed estimates and configuration declarations cannot become acquired data
through a projection argument. Existing reference, simulated and estimated
projections remain available. This checks retained declarations, not sensor
authenticity, calibration or physical validity.

Missing values remain `null`, not zero. Empty vectors, booleans masquerading as
numbers, nonfinite/out-of-budget values and absent units/frames are rejected.
Use explicit scoped frame IDs, for example `projectile-017/world`, and scoped
clock IDs. Equal labels are declarations of equal semantics, not evidence that
two independently chosen origins or coordinate conventions really match.
NET does not claim to resolve geospatial frames or validate arbitrary unit algebra.

Uncertainty reuses the existing `covariance-artifact.v1` validator, including
ordered quantity IDs, units, frame, reference values and full covariance. Vector
axes use names such as `position[0]`. A supplied state covariance must cover every
variable component; partial coverage is refused, rather than completed with
invented zero correlations. No covariance is inferred when uncertainty is absent.

Within one observation stream, timestamps must increase strictly; duplicate or
out-of-order samples refuse. Units, frame, scalar/vector shape and source semantics
cannot change within that stream. Different streams/clocks can interleave. The
bus defaults to 4,096 observations and is capped at 65,536; capacity exhaustion
refuses the append without dropping retained samples. It is thread-safe within
one process, not a distributed ordering system.

## Explicit provider binding and composition

`builtin_registry()` advertises only the installed oscillator's statistics and
spectrum operations. It does **not** claim that Blender, Godot, Bevy, Julia or
FSRT are automatically registered. `builtin_registry(bind=True)` explicitly
binds those existing built-in operations. Other adapters supply an existing
`InstrumentManifest`, a pinned runtime identity, capability names, and input/output
ports to `CapabilityRegistry.advertise`, then explicitly call `registry.bind`
with their trusted `Operation`. The existing trusted payload-validator mechanism
remains required for custom operations. Loading a saved catalog cannot bind code.

An input port has `{schema,unit,frame}`; an output has `{type: port, path: [...]}`
selecting an object from the retained CIW result. Null unit/frame constraints mean
no constraint on that port, not dimensionlessness or an assumed world frame.
Each experiment node has `node_id`, a versioned `operation_id`, literal
`parameters`, named `inputs={name:{node_id,port}}` and `depends_on`.
All operation bindings and edge types are checked before the first dispatch.

```python
from pathlib import Path
from ciw.control_plane import builtin_registry, experiment, run_graph
from ciw.instruments import make_demo_run
from ciw.session import Session

registry = builtin_registry(bind=True)
session = Session(make_demo_run(), Path("results/python-control"), operations=registry.operations)
graph = experiment("inspect-motion", model_id="analytic-damped-oscillator.v1", nodes=[{
    "node_id": "stats", "operation_id": "statistics.v1",
    "parameters": {"channel": "q"}, "inputs": {}, "depends_on": []
}])
report = run_graph(session, graph, registry)
assert report["status"] == "completed"
```

The graph performs no calculation except dispatch. Refusals and output contract
failures block dependent nodes; independent nodes may continue. A post-execution
output mismatch is retained as `output_rejected`, alongside the untouched original
execution/result. Runtime identity is checked before and after provider calls.
This checks declared runtime drift; it is not a process sandbox or build attestation.

Experiment-wide parameters are defaults overridden by node-local parameters.
Neither can silently be overwritten by an input edge. Model IDs and parameter
meanings remain provider declarations. A graph is not a universal scientific model.

## Parameter exploration and assertions

```python
from ciw.control_plane import ParameterSpace, Interval, Choice, Fixed
space = ParameterSpace({
    "mass": Interval(0.1, 1.0, "kg"),
    "solver": Choice(("rk4", "verlet")),
    "gravity": Fixed(9.80665, "m/s^2"),
})
cases = space.grid({"mass": [0.1, 0.5, 1.0]}, max_cases=20)
samples = space.sample(8, seed=48219)
assert len(cases) == 6
```

Pass a domain descriptor or validated assignment to an explicitly selected
provider operation. There is no internal optimization/calibration engine or
automatic run of every case. Default grid budget is 1,024; absolute budget is
10,000. Seeded sampling uses the installed Python random implementation; no
cross-version bitwise promise is made.

`verify` supports `less_than(limit,unit)`, `bounded(minimum,maximum,unit)`,
`conserved(tolerance,unit)`, `close_to(comparison)`, and
`covariance_positive_definite(quantity_ids,units,frame,margin)` policies. Supply
those policy names as keyword arguments after `name, kind, evidence`.
Missing evidence, missing samples and unmatched units return INDETERMINATE.
Conservation requires at least two observations. An empty assertion collection
is INDETERMINATE. The covariance check uses the existing validator and a declared
positive normalized-eigenvalue margin; a marginal numerical result is not PASS.

`compare(left,right,atol=...,rtol=...)` treats the right series as the reference
and applies `abs(left-right) <= atol + rtol*abs(right)` to each component.
Atol is expressed in the shared quantity unit; rtol is dimensionless. Model,
entity, quantity, frame, clock, units and timestamps must match exactly. A missing,
unmatched or incomplete series is INDETERMINATE; mixed identities inside one
series are rejected as malformed. No truncation, interpolation, confidence bound
or physical accuracy is inferred. Covariance is retained but not used to invent
independence in this deterministic difference check.

The CLI compares two explicit `ciw.observation-stream.v1` files, one selected
quantity per side, not arbitrary opaque run IDs. Exit statuses are 0 PASS,
2 FAIL, 3 INDETERMINATE and 1 malformed/refused. Phase, event/distribution,
energy-model, runtime and memory metrics require explicit provider operations;
this increment does not advertise implementations it does not contain.

## Checkpoints, counterfactuals and reproducibility

A `CheckpointProvider` exposes `identity`, `snapshot`, `restore`, `step` and
`observe`. NET stores only the exact snapshot hash/size and the declared model,
logical simulation, owner occurrence, runtime, state revision and clock.
Snapshot capture checks identity stability across the call. Restore validates
bytes, runtime/model/simulation/clock identity, and a fresh owner at revision zero
before explicitly calling the supplied provider. The restored owner must retain
its identity and restore the declared clock. A failed target must be discarded;
there is no generic engine rollback or distributed lease. The original owner
must be stopped before restoring a competing live owner.

These are opt-in hooks for existing provider lifecycles, not a second
SimulationSession. Adapter operations can wrap them in existing CIW execution
records. The hook tests use a labelled lifecycle fixture, **not** a native engine.
This change does not qualify general Blender/Godot/Bevy/Julia checkpoint support.
An experiment's optional `parent_checkpoint` records branch lineage. The adapter
still owns applying branch parameters and continuing the state. No arbitrary
counterfactual simulation is claimed merely because its metadata can be expressed.

Artifact records bind exact bytes, media type, producer, experiment and creation
time. `specification_id` hashes declared model, artifacts, parameters, initial
state, runtime and seed. It is deliberately different from an execution ID.
Hashes do not establish source authenticity, capture shared-library closure,
guarantee replay or authorize publication. NET has no new data lake or artifact
retrieval daemon, and `inspect` never prints `Reproducible: YES` from hashes alone.

## Inspection, tests and integration scope

`net inspect` validates bounded JSON and content seals. Workspace inspection uses
the existing `Session.from_workspace` reader over one frozen input in temporary
scratch storage, so original workspace bytes remain unchanged. Comparison and
assertion inspection rechecks only their retained numerical conditions, not an
external model. Graph inspection checks dependencies, typed ports, parameters,
runtime and execution/result binding consistency. Full operation-specific source
and payload validation belongs to the separate Session workspace reader. None of
these hashes or local checks authenticate an untrusted producer's scientific claims.

Test command: `python -m pytest -q tests/test_control_plane.py`. The focused
workflow runs it on Ubuntu/Windows, Python 3.11/3.12, under existing pinned
project dependencies, and exercises the installed wheel's `net` entry point.
It refuses missing or skipped qualification cases. The tests cover real Session
builtin dispatch/re-execution, labelled typed adapter hooks, full covariance,
no-drop observation behavior, strict input handling, resealed tampering, provider-
free subprocess inspection, three-valued checks and output non-overwrite.
Native engine execution and the full optional-provider platform remain separately
qualified work. Existing active integration branches, provider pins, mathematical
tolerances, private source boundaries, verification/admission, licences and all
existing qualification workflows remain unchanged.
