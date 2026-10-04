# Executable architecture

This page distinguishes the **implemented workbench architecture** from the
broader research architecture being tested around it. For intellectual lineage,
see [Research foundations and attribution](RESEARCH_FOUNDATIONS.md); for the
mathematical organizing vocabulary, see
[Research context](RESEARCH_CONTEXT.md).

For the shared-boundary enforcement audit, recurring `net catalog --audit`
coverage gate, and remaining mathematical obligations, see the
[cyber-physical contract audit](CYBER_PHYSICAL_AUDIT_2026-10-03.md).

> **Research annotation — architectural adaptation.** The distinction between
> canonical state and any one graph representation is motivated in part by
> representation-independent structures in graph/matroid theory, especially the
> graph/graphoid treatment of Novak and Gibbons. NET generalizes that lesson to
> scientific representations; the cited graph-theoretic results do not by
> themselves prove this software architecture.

## Protected architectural identities

The research direction extends the existing implementation rather than
collapsing its identities. The following remain separate:

\[
\boxed{
\text{evidence}
\neq
\text{canonical state}
\neq
\text{representation}
\neq
\text{execution/result}
\neq
\text{verification}
}
\]

The current code already separates operation, execution, result and
verification identities. The additional **canonical state / representation /
morphism / control-plane** vocabulary is a research overlay unless an
individual contract or PR states otherwise.

## Research-layer architecture

The intended extension is:

~~~text
evidence
   │ admission
   ▼
canonical state
   │
   ├── graph / relational projection
   ├── matrix / state-space projection
   ├── spatial / spectral projection
   └── rendered projection
   │
   ▼
typed morphisms
   │
   ▼
candidate experiment / intervention
   │
   ▼
existing NET execution machinery
   │
   ▼
observation → compare → verify → retain
~~~

A separate **Systemic Control Plane** may choose or propose representation,
model, parameter, workflow and execution-backend configurations. It must not
gain evidence-admission authority merely because it can reconfigure execution.

> **Research annotation — project hypothesis.** A future representation should
> declare which queries, interventions and invariants it preserves. For a
> projection \(\pi\) and intervention \(N\), one useful condition to test is
> \(\pi\circ N \simeq \bar N\circ\pi\). This is a NET research contract, not a
> theorem claimed from the cited category-theory or graph-theory literature.

> **Research annotation — established-to-adapted.** Hybrid network analysis
> provides a concrete example in which alternative independent-variable choices
> can yield different equation counts and a minimum topologically complete set.
> NET uses this as motivation for investigating task-specific **operationally
> complete coordinates**; it does not call arbitrary scientific reductions
> "hybrid rank" without the required matroid/network structure.

For the current public component inventory and integration boundaries, see the
[Notation Systems stack map](STACK.md) and [this component's role](STACK_ROLE.md).

This document describes the implementation in this repository. Exact wire and
record fields are specified in [PROTOCOL.md](PROTOCOL.md); supported operations
and their qualifications are listed in [INSTRUMENTS.md](INSTRUMENTS.md).
The [execution responsibility map](EXECUTION_RESPONSIBILITIES.md) relates the
implemented Python host to Julia, native providers and selected proof paths,
and distinguishes future execution and equipment boundaries.

## Interactive simulation and authoring targets

Godot, Bevy and Blender are first-class optional integration targets for
interactive simulation and computational authoring. This is a workload extension,
not a requirement to install the engines, a new control plane or a replacement
for existing scientific computation and engineering simulation workflows.
The [interactive simulation guide](INTERACTIVE_SIMULATION.md) separates the
current inspection/projector source from planned authoring and runtime adapters,
and specifies the first all-three acceptance case.

NET retains investigation and execution history. Each engine-owned simulation
retains its own world and clock; provider-owned scientific simulations preserve
the existing CIW/provider state-commit boundary. A new adapter must declare which
mode it supports rather than silently transferring state ownership. Blender
produces separately retained authoring artifacts, not implicit physical truth.
Existing Godot and Bevy retained-record projectors remain projectors; they do not
acquire game-state mutation or simulation authority through this extension.

GSC representation, CSE/BIM estimation, CSR geometry and FSRT fluid operations
are optional specialist capabilities, not compulsory stages in an engine tick.
Their mathematics and implementations remain in their own repositories.
The proposed adapters reuse the session, operation and execution boundaries below;
no new operation, schema, CLI or engine qualification is established by this page.

## Components

| Component | Implemented responsibility |
| --- | --- |
| `src/ciw/core/` | Structural scientific-record validation, content identities and typed covariance artifacts |
| `src/ciw/adapters/` | Declared manifests, explicit adapter registry, offline payload readers and bounded pinned subprocess execution |
| `src/ciw/operations/` | Versioned operation registration, captured inputs and selections, retained execution/refusal and result envelopes |
| `src/ciw/session.py` | Shared selection, immutable retained results, workspace save/reopen and dependency validation |
| `src/ciw/server.py` | Local WebSocket transport for the authoritative session |
| `src/ciw/cli.py` | Terminal commands for analysis, service access and supported investigations |
| `godot/` | Optional oscillator 2D/3D rendering and interaction client |
| `src/ciw/adapter-runtimes.json` | Explicit current and historical source pins for external numerical providers |

Scientific engines remain in their authoritative repositories. The workbench
maps inputs, checks declared record structure and source relationships, retains
provenance, and invokes explicitly bound providers. Saved manifests and runtime
metadata do not register code or authorize execution.

## Scientific records and identities

`run.v1` retains timestamps, declared units and frames, full-resolution channels
and provenance. Generic records embed a `ciw.instrument-manifest.v1`. Missing
observations remain explicit; they are not converted to zero or fabricated by
rendering. Render geometry is a representation and never a calculation input.
Adapter resolution uses the instrument and embedded manifest, not `run_schema`
alone. An unknown instrument needs a valid embedded manifest; its record-only
reader validates declared units and frames without executing a provider. An
operation absent from an adapter's declared list is refused as
`unsupported_operation`; a listed operation with no bound adapter is refused as
`adapter_unavailable`. Manifest `role` currently accepts any nonempty string;
it is not the closed role vocabulary enforced for registered operations.

Operation executions use `ciw.execution.v1`; successful operations produce a
separate `ciw.operation-result.v1`. A refused invocation has an execution record
and no result. Evidence, operation, execution, result and verification identities
serve different purposes and are never interchangeable. Numerical checks,
content digests and replay equality do not create verification identities.

Operation identities have the form `<name>.v<n>` with a positive integer version;
`.v2` is supported as well as `.v1`. A syntactically valid identity does not bind
code. Trusted process setup registers operations and saved-payload validators.
The runner captures effective parameters and selection, passes detached inputs
to the provider outside the session lock, validates and seals its response, and
retains refusals without a result. The session publishes those records under its
lock. Malformed requests are rejected before admission without an execution;
failures after `operation.execute` accepts a request become retained refusals.
Legacy `analysis.*` results keep their original flat format.

The original oscillator workspace format remains readable. Workspaces retaining
operation executions use version 2. Reopening validates source/result bindings
and retained dependencies before writing a destination. Explicit replay retains
prior results and creates new execution/result identities. JSPT dependencies
must resolve to a retained result and its exact named covariance; missing,
mismatched or cyclic references are refused before any destination write.

### Admission and retained outcomes

```mermaid
flowchart TD
    Request["Operation request"] --> Gate{"Valid request shape?"}
    Gate -->|No| Reject["Reject without execution"]
    Gate -->|Yes| Capture["Capture parameters and selection"]
    Capture --> Bind{"Supported operation and binding?"}
    Bind -->|No| Refusal["Retained execution refusal"]
    Bind -->|Yes| Invoke["Provider outside session lock"]
    Invoke --> Response{"Valid successful response?"}
    Response -->|No| Refusal
    Response -->|Yes| Seal["Seal execution and result"]
    Seal --> Publish["Publish under session lock"]
    Refusal --> Publish
```

The gate labels summarize the operation runner, not a new status vocabulary.
A refused accepted invocation retains its execution but has no successful
result. Input capture and result publication bound the provider call; they do
not turn the session lock into a process sandbox.

## Selection and clients

The session owns the current channel, playback cursor, half-open analysis
interval, coordinate frame and selection revision. Playback and analysis support
are separate. Selection updates carrying a stale revision are rejected.
Operations retain the selection under which they ran. Each domain operation
specifies its supported interval semantics; GTE requires the interval to contain
its entire retained batch.

The terminal and optional viewport are clients of the same service. Closing the
viewport does not stop the backend. The current Godot client supports oscillator
representations and declines unsupported external-instrument view contracts.

## Runtime binding and persistence

External providers use an explicitly supplied clean checkout and interpreter.
The subprocess adapter verifies source revision/tree, interpreter digest and
recorded dependency versions, bounds execution time and output, and accepts a
versioned finite-JSON response or refusal. These checks detect identity drift;
they are not a sandbox or a proof of scientific correctness. A nonzero child
exit is `RUNTIME_FAILED`; an unavailable executable, process or interpreter probe is
`RUNTIME_UNAVAILABLE`; pipes left open after the child exits are `RUNTIME_IO`.

Current and historical bindings are allowlisted by the workbench. Replaying a
saved investigation requires its supported source pin and matching runtime.
Saved content cannot extend the allowlist, choose arbitrary executable imports,
or silently substitute the latest engine.

The service uses a local filesystem workspace. The default bind is loopback;
container publication and native-process controls are documented in
[deploy/](../deploy/README.md). Public network authentication, a production
multi-user service, streaming acquisition and general binary transport are not
implemented.

### Reopen and replay are separate actions

```mermaid
flowchart TD
    Saved["Saved investigation"] --> Validate{"Bindings and dependencies valid?"}
    Validate -->|No| Refuse["Refuse before destination write"]
    Validate -->|Yes| Mode{"Requested action"}
    Mode -->|Reopen| Inspect["Inspect retained records and IDs"]
    Mode -->|Replay| Runtime{"Matching supported runtime?"}
    Runtime -->|No| Refuse
    Runtime -->|Yes| Execute["Reexecute retained operations"]
    Execute --> Fresh["New execution and result IDs"]
    Fresh --> History["Retain prior and new results"]
```

Reopening calls no numerical provider. A saved manifest cannot supply an
executable or extend the trusted allowlist. The separate telemetry container
adds scoped content and numerical replay checks described in
[TELEMETRY.md](TELEMETRY.md); its verification receipts are not a property of
every shared-workspace replay. More stack views are in the
[diagram atlas](DIAGRAMS.md).

## Calibration and uncertainty

Calibration applicability at acquisition is retained independently of expiry at
serving time. Present expiry does not rewrite historically applicable evidence.
The [covariance contract](COVARIANCE.md) describes ordered quantities, units,
frames, reference values, full matrices, assumptions and source identities.
Schema validation does not infer independence, unit conversions, uncertainty
completeness, metrological traceability or physical validity.

The live `session.get`, `result.list` and `result.get` read surfaces accept an
optional timezone-aware `evaluated_at` timestamp. For measurement-chain runs,
serving status appears alongside the response data, leaving the saved result
unchanged; it is never hashed into evidence. Oscillator responses do not acquire
calibration status. See [PROTOCOL.md](PROTOCOL.md) for the exact field placement.

The implemented covariance path uses `ciw.rci-source.v2`,
`fsrt.tank-reconstruct.v2` and `jspt.covariance-propagate.v1`.
`ciw covariance` operates on a saved investigation and `ciw covariance-replay`
re-executes its retained propagation inputs through the pinned provider. The
original `fsrt.tank-reconstruct.v1` path and supported historical runtime pins
remain readable and replayable under their documented bindings.

Each provider retains its own scientific scope. FSRT's physical balance check
is not an NIS/NEES qualification; JSPT does not establish that a supplied Jacobian
is correct; GTE's native tangent covariance is conditional on exact declared
geometry; PLSR's terminal bundle remains separate from shared investigations.
Their operating guides state the supported paths and limitations.

## Verification

Python tests cover record and operation integrity, selection/replay behavior,
refusals, covariance admission and explicit runtime bindings. Integration gates
exercise pinned scientific repositories. Godot checks exercise import and the
implemented client protocol. See [DEVELOPMENT.md](DEVELOPMENT.md) for commands.
Computational test success does not certify a physical measurement or device.
