# Engineering superrepo

The default branch contains 21 public engineering modules alongside the
existing Terminal package, merged through pull request #126. Development
and review share a repository; packages, release identities, deployment choices
and scientific authority remain module-owned.

The import registry is [`instruments/manifest.json`](../instruments/manifest.json).
It records full source commit and tree identities, original repository IDs,
ownership, licences, package versions, build boundaries and existing execution
pins. All 21 public modules are co-located on the default branch with their
original native Git ancestry. The workspace includes the earlier measurement
and inference migration history, the retained Terminal correction loop,
Legibility, and the integrated NET workloads. The
[second-wave record](MONOREPO_WAVE2.md) preserves the earlier seven-module
qualification and its scope.
The Legibility Instrument is composed into the existing `ciw` package;
retained correction inspection and representation compilation remain available
through the same CLI.

## Module map

Paths below are relative to `instruments/`. Repository links identify the
original projects; the manifest owns their exact selected source identities.

| Area | Role | Directory | Original repository | Licence |
| --- | --- | --- | --- | --- |
| Composition | `sra` | `composition/retrieval-agent` | [Retrieval Agent](https://github.com/atomtrapping/Notations-Retrieval-Agent) | MIT |
| Execution | `scr` | `execution/compute-runtime` | [Compute Runtime](https://github.com/atomtrapping/Notations-Compute-Runtime) | Apache-2.0 |
| Measurement | `mcur` | `measurement/calibration` | [Calibration Runtime](https://github.com/atomtrapping/Notations-Calibration-Runtime) | MPL-2.0 |
| Measurement | `tbrt` | `measurement/clocksync` | [ClockSync](https://github.com/atomtrapping/Notations-ClockSync) | MPL-2.0 |
| Measurement | `rci` | `measurement/metrology` | [Metrology Adapter](https://github.com/atomtrapping/Notations-Metrology-Adapter) | MIT |
| Measurement | `stfe` | `measurement/signal-processing` | [Signal Processing Runtime](https://github.com/atomtrapping/Notations-Signal-Processing-RunTime) | MPL-2.0 |
| Inference | `gsie` | `inference/state-inference` | [State Inference Engine](https://github.com/atomtrapping/Notations-State-Inference-Engine) | MPL-2.0 |
| Inference | `cbsr` | `inference/state-recompiler` | [State Recompiler](https://github.com/atomtrapping/Notations-State-Recompiler) | AGPL-3.0 |
| Inference | `fdir` | `inference/faultsense` | [FaultSense](https://github.com/atomtrapping/Notations-FaultSense-RunTime) | MPL-2.0 |
| Verification | `set` | `verification/estimator-bench` | [Estimator Bench](https://github.com/atomtrapping/Notations-Estimator-Bench) | Apache-2.0 |
| Mathematics | `oit` | `mathematics/observability` | [Observability Testbed](https://github.com/atomtrapping/Notations-Observability-Testbed) | MPL-2.0 |
| Mathematics | `edspt` | `mathematics/sensor-design` | [SensorDesign Runtime](https://github.com/atomtrapping/Notations-SensorDesign-RunTime) | MPL-2.0 |
| Mathematics | `sidt` | `mathematics/linear-dynamics` | [Linear Dynamics Testbed](https://github.com/atomtrapping/Notations-Linear-Dynamics-Testbed) | MPL-2.0 |
| Mathematics | `jspt` | `mathematics/sensitivity` | [Sensitivity Testbed](https://github.com/atomtrapping/Notations-Sensitivity-Testbed) | MIT |
| Mathematics | `csg` | `mathematics/surface` | [Surface Runtime](https://github.com/atomtrapping/Notations-Surface-RunTime) | MPL-2.0 |
| Mathematics | `tsde` | `mathematics/polygon-trajectories` | [Polygon Trajectory Experiments](https://github.com/atomtrapping/Polygon-Trajectory-Experiments) | MIT |
| Domain | `fsrt` | `domain/flowstate` | [FlowState](https://github.com/atomtrapping/Notations-FlowState) | MIT |
| Domain | `cse` | `domain/bim-estimator` | [Estimator for BIM](https://github.com/atomtrapping/Notations-Estimator-for-BIM) | MIT |
| Representation | `framemapper` | `representation/frame-mapper` | [FrameMapper Runtime](https://github.com/atomtrapping/Notations-FrameMapper-RunTime) | GPL-3.0 |
| Views | `gsv` | `views/real-time-globe` | [Real-Time Globe](https://github.com/atomtrapping/Notations-Real-Time-Globe) | GPL-3.0 |
| Resources | `ywir` | `resources/yield-weighted` | [Yield-Weighted Runtime](https://github.com/atomtrapping/Notations-Yield-Weighted-Runtime) | MIT |

Eighteen modules have independent Python wheel builds. Compute Runtime retains
its source and release builders; its original Python metadata does not declare
a wheel build backend. The two views retain independent npm packages and
lockfiles. The root wheel continues to package `ciw`; importing source into this
repository does not add every module to the Terminal installation. Dependencies
and lockfiles remain inside their original packages.

## Source and execution identities

Native, non-squashed import merges retain original commit objects and ancestry.
Each imported subtree contains the exact selected original tree, including
module-internal paths, tests, package metadata, licences and notices. Explicitly
retained public branch histories cover existing execution pins that are absent
from a project's current default history. This is not an import of every remote
branch or tag.

| Module | Selected source | Additional retained history and reason |
| --- | --- | --- |
| ClockSync | `13c5fe75c7e829c24bae12fcf8386d4831224d4e` | Native instrument pin `edb4e5b99ec0ce384437e1c0f1c820ef04598e33` is retained as an original Git parent for the existing instrument gate. The normal calibrated-process runtime `40507060ca7a9126a9d641999b994a757eef3bfd` remains unchanged. |
| Estimator Bench | `928ae6a76d4f853aa8306fef8207f81244b7066f` | Public calibrated-window replay history `2f838f4e196f453efc3a59045b0b3ec4b5680296` is retained as an original Git parent. Its exact replay provider remains distinct from the normal SET runtime `5e7bda36f521a5c1b0082b512f35e29803bffafc`. |
| Surface | `1f7bbe380651e8df82db1760d880330aee3dc229` | Default `e8f0938ab243a1905792ba2c43439cb4f40cd4be` is retained; its documentation migration fails original documentation assertions. The selected snapshot preserves the unchanged passing source tests. Native micro-tool pin `0b00e837c2df3206a3d38b497799f85b72de80f7` is retained as a separate original parent for the existing NET gate. |
| FlowState | `3144e3e694419b0c8579938e8d28523174e36abd` | Default `e13e46facc125682776f165ce1b0460b4ff9410a` is retained; its README rename fails the original package identity assertion. The immediate parent retains identical numerical source, tests, locks and fixtures. |
| FrameMapper | `b788489373cbbeebf69667ddf06c048855e22836` | Default `bdfcb041836e86ab1ce93688b127f8960e8d08cf` is retained; its documentation-only change fails the original route policy. The selected snapshot preserves that policy and package implementation. |
| Compute Runtime | `da2dc23857dd8665473d3b8d32e6b7452866e749` | Public provider-host branch retains existing NET native pins. Separate default `98ab2f312cb7f9f4dbb18b25f762593eba56653e` and native DSP pin `91a6d3b37f28623332acd485e9f8a12953acf71e` are retained as original parents. |
| Metrology | `a67cfef9f132d2a756186f34dcc718404084126e` | Public CIW adapter history ending at `f863bdd69d49224e0cdc871943bbb052e5b0a975` retains the older qualified adapter alongside the current 0.2.0 package. |
| Real-Time Globe | `6f1b339dfa103bc45b40f34170043bc23a010c57` | Public geographic-view integration history `06c47bd851d8ea8b363a6bbe60a96c7448cbe12e` is retained separately. The default npm package has no declared NET execution pin. |

Existing declarations in `src/ciw/` continue to select exact historical
execution revisions. The current imported package and its historical provider
execution are separate qualification targets. Calibration, clocks,
calibrated-observable inference, identified design, telemetry, geometry and
declared workloads retain their own bindings. Updating one requires a separate
compatibility change with checks for the affected compositions.

`provider_worktrees` in [`scripts/monorepo.py`](../scripts/monorepo.py) materializes
temporary, standalone detached worktrees from retained history. Existing
`PinnedSubprocessAdapter` checks remain unchanged: the expected repository root,
commit, file bytes and interpreter still determine executable identity. An
imported directory or the superrepo's `HEAD` does not substitute for that pin.
Explicit external provider checkout support remains available.
The ClockSync instrument, CSR micro-tool and DSP lanes retain their original pins and native test
contracts through reviewed side histories. Provider source bytes are checked
before and after execution; changes to upstream repository visibility do not
change the tested revision or authorize a replacement.

The public calibrated-window CI lane uses these retained histories through
`python scripts/check_public_provider_gate.py calibrated-window --output-dir results/calibrated-window`.
It runs the unchanged installed-package gate at all five original provider pins,
with source checks before and after execution. The SET side history is required;
missing ancestry refuses instead of downloading or substituting another revision.
This route requires no private-provider credential. Its configured CI matrix is
not a claim of completed qualification; successful run evidence must identify the
actual tested source. Preserve the additional Git parent when merging this change.


FlowState's public source and history are now retained locally. Both its
calibrated-observable generator pin and CBSR's separate public comparison pin
can be provisioned from that history. They remain distinct revisions. The npm
views have no invented Python runtime binding; web build qualification does not
establish attached-browser interoperability.

## Operator commands

Use Python 3.12 or later for the complete public qualification. The web lane
requires Node 24 or later, the Surface locked lane requires `uv`, and the bounded
Compute Runtime lane requires Cargo; the configured Linux CI uses Rust 1.90.0.
Cargo, its compiler and Rustdoc must all be available in a consistent toolchain.
Package dependencies are installed into
temporary environments. Dependency downloads require public registry access;
the view tests and builds subsequently use their original locks offline.

From the repository root:

```sh
python scripts/superrepo.py list
python scripts/superrepo.py audit
python scripts/superrepo.py doctor
python scripts/superrepo.py check --output-dir results/superrepo
```

`doctor` observes source/history and the selected tools without installing
dependencies or running scientific qualification. Its fresh preflight identity
is separate from source revisions and verification receipts. `available` means
the observed prerequisites are present; registry access, package builds and
composed workflows still require `check`. Use `--group` to inspect selected lanes,
and `--json` for the complete observations and configured CI matrices.

Both commands accept `--cargo`, `--node-bin` and `--uv` for trusted tools outside
the default `PATH`. Cargo's directory is forwarded to the child environment so
its sibling compiler and Rustdoc can run; explicit `RUSTC` and `RUSTDOC` settings
remain authoritative. Node's directory supplies both Node and npm. `--uv` selects
Surface's locked-environment builder.
Doctor disables Rustup's automatic toolchain installation during version probes.
The web lane is qualified on Linux. Doctor recognizes npm launchers with an
explicit Node shebang; opaque Windows launchers are reported as refused until
their binding is supported and separately qualified.

For module development, use the [controlled source-update workflow](MODULE_UPDATES.md).
It records a clean committed module draft as a native source commit, audits the
candidate and creates a new review branch. Package/composition qualification and
any runtime-pin changes remain explicit subsequent work.

For temporary worktrees, builds and runtime files outside synchronized storage,
choose an existing private directory with `--temp-root`:

```sh
task_temp_root="$(mktemp -d /tmp/notations-qualification.XXXXXX)"
python scripts/superrepo.py check --temp-root "$task_temp_root" --output-dir results/superrepo
rmdir "$task_temp_root"
```

The resolved directory is passed to child gates through `TMPDIR`, `TEMP` and
`TMP`; reports stay in `--output-dir`. Without this option the inherited temporary
directory settings remain unchanged. Cleanup failures still fail qualification.

The audit verifies exact source trees and original-history reachability,
working file bytes and modes, package/licence identities, preserved execution
bindings and unexpected untracked source files. Module records must also retain
their repository identifier, historical repository label, ownership declaration
and import status. These declarations are provenance metadata; the audit does
not authenticate legal ownership. Cache exclusions must not admit executable
source merely because a nested directory has a cache or environment name.

All seven qualification gates bind the actual Terminal source tree before and
after execution. The aggregate checks each child's source identity and retains
its report byte hash. Fresh verification occurrences remain distinct from
source identities and from historical qualification evidence.

Run the underlying gates
individually to review a particular boundary:

| Gate | Command | Scope |
| --- | --- | --- |
| Measurement | `python scripts/check_monorepo.py --output-dir results/monorepo` | Independent Calibration and ClockSync wheels; original clock-to-calibration adapter composition. |
| Inference | `python scripts/check_monorepo_inference.py --output-dir results/monorepo-inference` | Original seven-module suites, exact public comparison dependencies and actual eight-provider calibrated-observable session/replay. |
| Mathematics and measurement | `python scripts/check_monorepo_math.py --output-dir results/monorepo-math` | SensorDesign, Linear Dynamics, Sensitivity, Metrology, Signal Processing and Polygon source/installed suites; CPU JAX checks; exact legacy measurement-chain compositions. |
| FlowState | `python scripts/check_monorepo_flowstate.py --output-dir results/monorepo-flowstate` | Independent wheel numerics and examples; unchanged default source suite, including extended tests. |
| Surface | `python scripts/check_monorepo_surface.py --output-dir results/monorepo-surface` | Full scientific suite using an installed wheel; regenerated reports, figures and boundary artifacts; five determinism cycles; original locked contract checks and exact NET curved-path replay. |
| Operations and resources | `python scripts/check_monorepo_operations.py --output-dir results/monorepo-operations` | Retrieval, Yield-Weighted and BIM package checks; identified-design and BIM session/replay; declared schematic and actual bounded CPU heat execution; budget/refusal checks. |
| Views | `python scripts/check_monorepo_web.py --output-dir results/monorepo-web` | Independent locked npm tests, type checks and production builds for FrameMapper and Globe. |

Budget qualification is called by the operations gate; `check_monorepo_budget.py`
is a helper, not a separate CLI. Gate output includes `report.json`,
`commands.log`, original test receipts and composition artifacts where relevant.
Reports record the actual checkout and execution identities, dependency
versions, scope, failures and individually unqualified checks.

The original measurement gate also supports Python 3.11. Explicit
`--set-root`, `--flowstate-root` and `--sensitivity-root` options on the relevant
gates retain standalone checkout support. The measurement gate defaults to a
temporary worktree at SET's retained legacy exchange revision; it no longer
downloads that source from GitHub. Both local and explicit external SET roots
retain exact revision, working-byte and index checks, and reports identify the
selected binding route. Operations accepts `--cargo` to bind
an explicit trusted Cargo executable; Surface accepts `--uv` for its original
locked contract lane. The aggregate runner supports repeated `--group` options
and explicit `--cargo` and `--node-bin` toolchain bindings. For example:

```sh
python scripts/superrepo.py check --group measurement --group math --output-dir results/measurement-and-math
```

FlowState's default selection follows its original `not slow` configuration.
To run its additional public slow reproduction lane:

```sh
python scripts/check_monorepo_flowstate.py --full-reproduction --output-dir results/monorepo-flowstate-full
```

That option does not provision private acquisition data. The workflow exposes
it as an optional manual lane, separate from the normal default-suite matrix.
FlowState's original `uv.lock` is preserved; its gate reports an explicit test
environment rather than claiming frozen-uv reproduction. Surface separately
runs its original locked contract lane with `uv sync --locked`; that lane's
report records the original lock and the environment actually reproduced.

Nested module workflows are retained source configuration; GitHub discovers
the root [monorepo workflow](../.github/workflows/monorepo.yml). Its jobs use
complete Git history and retain per-lane evidence. Configured Python/platform
matrices are not evidence of remote execution. Observed results belong in
the retained qualification reports, bound to their exact checkout.

## Terminal source identity

The shared import audit binds NET's tracked source bytes and index to its
commit alongside every declared original module tree and execution pin. Each
qualification lane audits before and after execution. Tracked byte changes,
staged metadata drift and ignored executable shadow sources are refused.
Directory names such as `venv`, `__pycache__` and `.egg-info` do not authorize
untracked source. Only bytecode backed by an existing Python source file and specifically named
pytest cache and package metadata files with regular file and ancestor types
are permitted inside source boundaries; gate output remains outside executable source directories.

The aggregate coordinator removes Python import/test and Git repository-selection
overrides before launching child gates and retains failed reports for subprocess errors,
including audit timeouts. Co-location does not freeze or replace independently
qualified Terminal work.

## Authority and qualification limits

The existing [consolidation architecture](CONSOLIDATION.md),
[execution responsibilities](EXECUTION_RESPONSIBILITIES.md) and
[integration coverage](INTEGRATION_COVERAGE.md) continue to govern interfaces.
Evidence, operation, execution, result, verification and admission identities
remain separate. Candidate estimates remain distinct from admitted state;
mathematical instruments retain numerical scope, uncertainty and refusal rules.

Actual composed workflows retain original analytic fixtures, held/refused
cases and numerical replay checks. Fresh replays retain new occurrence
identities. Existing receipts mark verification `independent: false` and
admission `not_performed`. Advisory budget admission is distinct from real
provider billing or settlement. Quantity-only BIM conditioning is distinct from
geometry authority. Passing package or numerical tests does not establish
physical measurement validation or device permission.

Unqualified dependencies remain visible: the exact original private GTE
comparisons in CBSR; private DAF reproduction checks in FlowState; optional BIM
private PLSR checks and the unavailable JSPT Grams guest interface. Public USD/IFC SDKs and the original three-model corpus are qualified separately; six implemented public JSPT covariance/quantity checks retain their own scope. Gate reports name
their skipped tests and reasons, and reject unexpected skips. Public slow
FlowState reproduction is qualified only when its separate lane actually runs.
Private Periodic Space integration paths remain unqualified. Native-host
Windows/Julia workers, GPU/prover paths, live provider calls and attached browser
integrations require their own evidence. Surface preserves its original
individually named documentation and platform skips; these concern absent prose
claims or unavailable extended precision and are recorded separately from
private provider exclusions.

## Private boundaries, licences and transition

Private Data Intake, Periodic Space, Telemetry and PLSR remain external.
State Ledger's missing licence and CNC Machine MCP's missing licence and
executable implementation prevent their import in this public consolidation.
The README-only Scientific Language Runtime and Inference Schematics Engine
remain backlog entries. `1792` and `A Man of Two Worlds` retain their own product
repositories, assets, content, state authority and releases.

The root AGPL licence and each module's original MIT, MPL, Apache, GPL or AGPL
licence, notices and inherited attribution remain in force. Preserved package
metadata includes upstream omissions and differing licence field forms. The
current [asset permission policy](licensing/README.md) is retained; it does not
blanket-relicense imported software or existing assets. Combined distribution
and future asset enrolment remain separate decisions under their existing terms.

Keep source repositories available while consumers migrate. An import does not
publish new package releases, redirect existing consumers or archive sources.
Future imports must declare public visibility, exact source/history identities,
licences, ownership, build/release boundaries and compatibility evidence before
expanding the registry.

## Native Git transfer

The merged default branch retains the original histories and native import
commits. Qualification requires a complete checkout, as configured in CI.
For an existing shallow checkout, retrieve the retained history before auditing:

```sh
git fetch --unshallow origin
python scripts/superrepo.py audit
```

Use merge commits for future import branches. Squash, rebase or file-content
recreation loses the required ancestry. Keep native source commits
reachable after merging so provider worktrees and the source audit continue to
operate.
