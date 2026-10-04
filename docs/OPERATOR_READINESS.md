# Operator readiness

Use `net start` to check the local installation before binding additional
instruments. NET keeps evidence, operation, execution and verification
identities separate throughout execution, inspection and replay.

```sh
net doctor --profile core
net catalog
net start --output-dir results/first-use-001
```

Read `results/first-use-001/readiness.json`. The receipt reports the named checks
and artifact paths: `oscillator`, `thermal/original`, `thermal/replay`, and
`atmosphere` beneath that output directory. These are bounded synthetic
reference workflows. A successful receipt establishes their local completion,
numerical checks and retained-record behavior in that run.

## What each check establishes

| Surface | What it establishes | Next requirement |
| --- | --- | --- |
| `net catalog` | Available command surfaces and declared provider requirements. | Select a workflow and supply its inputs and bindings. Listing does not execute or qualify it. |
| `net doctor --profile core` | Local Python, package and required dependency metadata satisfy the core profile. | Execute the first-use check. Preflight does not establish scientific or binary qualification. |
| `net start --output-dir NEW` | The named synthetic execution, check, replay and saved-inspection steps completed locally. | Use each specialist workflow's own operating and qualification guide. |
| `net workbench --output-dir STATE` | One persistent local session for sources, operations and retained history. | Bind specialist providers explicitly before new execution or replay. Reopening alone runs no provider. |
| `net provision --monorepo ROOT --workflow KIND --output-dir NEW` | Exact public provider checkouts and an explicit bindings file for the selected workflow. | Install its declared dependencies and pass the bindings to an explicit run. Provisioning is not numerical qualification. |

The monorepo contains 21 preserved public module snapshots. A source snapshot
may differ from a workflow's execution pin. Source presence, an exact local
binding, a successful execution and a qualified numerical result are separate
states. Held private or unlicensed components do not become available through
the public catalog or a synthetic first-use receipt.

## Provision a public scientific workflow

Run provisioning from a Git checkout of the monorepo. It uses the selected
workflow's existing execution pins rather than silently upgrading them to the
current imported module snapshot. Keep provider checkouts outside the monorepo
working tree. The following example uses a sibling directory:

```sh
net provision --monorepo . --workflow calibrated-observable --plan
net provision --monorepo . --workflow calibrated-observable --output-dir ../net-providers/calibrated-001
```

`--plan` reads requirements without creating checkouts. Actual provisioning
creates exact retained public checkouts and `bindings.json`; it does not fetch
private providers, install packages, compile engines or run the experiment.
The detached checkouts share retained Git objects with the declared monorepo.
Keep that source checkout available at the same path. Provisioning retains a
report even if checkout creation fails; use a new output directory for retry.

Follow the [calibrated-observation guide](CALIBRATED_OBSERVABLE.md) for its
dependency closure and supported synthetic source. Then pass the bindings file
to an explicit scientific execution:

```sh
net science run --workspace results/workbench/workspace.json --kind calibrated-observable --source examples/calibrated-observable/source.json --label reservoir-demo --bindings-file ../net-providers/calibrated-001/bindings.json --output-dir results/calibrated-001
```

Stop or checkpoint the live workbench before operating on its saved workspace
offline. Use the resulting workspace and returned bundle ID for inspection or
fresh replay; keep each replay's output directory separate. A bindings file is
operator configuration. Loading saved scientific results does not activate its
providers automatically.

For an assembled live process session, use the role-named checkout and startup
bindings documented in [shared workbench assembly](WORKBENCH_ASSEMBLY.md).

## Native engines and optional capabilities

Native DSP needs an explicitly built FIR library and its exact SHA-256 binding;
see [DSP instrument](DSP_INSTRUMENT.md). Native interop, Julia workers, proof
engines, GPU instrumentation, Godot and Blender each have their own runtime and
host requirements. Source code in the monorepo does not supply a compiled
engine or a source-to-binary attestation.

The `legibility` doctor profile checks the signed-representation extra without
importing its cryptographic provider. The doctor profiles `declared-workloads`, `native-interop`,
`interval-requirement`, `reaction-catalyst` and `reaction-cantera` inspect their
explicit local checkout/engine or runtime-binding arguments. For the exact
flags, run `net doctor --help`. Missing dependencies return a failed preflight
with a classification such as `dependency_unavailable`; preserve that report
and provision the named requirement before retrying.

The [installed public-tool guide](RUN_NET.md) adds signed-representation and
impact checks. The [fluid guide](FLUID_DYNAMICS.md) and
[polymer guide](POLYMER_PROCESSING.md) cover their retained operator paths.

Bounded [atmosphere](ATMOSPHERIC_ENGINE.md) and
[impact](IMPACT_TESTBED.md) examples can run without those external engines.
Their numerical verification applies to their declared idealized equations and
operating envelopes. Their read-only `verify` commands recompute diagnostics
while retaining the original scientific verification occurrence identity.
Inspection restores records without recomputation.

## Keep the record usable

Use a new output directory for each explicit execution or replay. Retain the
original workspace and immutable artifacts; compare numerical outputs
separately from occurrence identities. Save a live session with
`ciw send workspace.save` before interruption, and reopen it through
`net workbench` to continue inspection.

Invalid or changed retained bindings must refuse rather than fabricate a
successful result. A failure report is evidence of that attempt; a later
successful retry does not rewrite it. See [integration coverage](INTEGRATION_COVERAGE.md)
for implemented workloads and their remaining gates, and
[monorepo qualification](MONOREPO.md) for the independent public test lanes.

Synthetic workflow completion does not establish physical calibration,
measurement accuracy, weather prediction, industrial-state admission or
machinery-control authority. Those claims require their own measured evidence
and explicit commissioning or admission process.

## One candidate wheel across hosted platforms

The [installed operator workflow](../.github/workflows/operator-readiness.yml)
builds one portable wheel and uploads it with `operator-wheel.json`. Linux and
Windows consume that same artifact. Each consumer checks its SHA-256 against
the build job's separately supplied digest before installation. The installed
journey checks the complete `ciw/` source/resource byte inventory before and
after execution and binds its harness checkout to the same candidate revision.
Changed, missing, additional, editable or source-shadowed package files refuse.

This exact-artifact lane installs with `pip install --no-compile` and launches
instrument subprocesses with `-I -B`. Pre-existing package bytecode is refused:
matching source files alone cannot establish which cached code would execute.
Ordinary NET installations do not need this stricter qualification setup.

The two platform artifacts retain `qualification.json`, command logs, the
wheel manifest, first-use workspaces and the original/fresh curved-path replay
records. The `operator-candidate-wheel` artifact holds the actual wheel; artifacts
have a 14-day retention period. The `operator-acceptance` job fails if the build
or either platform journey fails, is cancelled, or is skipped. The source
contract-test receipts retain individually reported optional/platform skips;
these do not qualify excluded checks.

The manifest identifies the artifact selected by the build workflow; it is not
a reproducible-build proof or source-to-binary attestation. This lane qualifies
its declared synthetic operator journey and public-provider reproduction only.
It does not close independent human acceptance, private-provider/runtime gates,
physical validation, admission or actuation authority. Historical reports keep
their original artifact, platform and provider identities.
