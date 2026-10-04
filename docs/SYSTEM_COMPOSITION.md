# Scientific system composition

NET compiles a declared scientific configuration into a typed state schema and
execution graph, runs coupled reference models, and retains distinct numerical
verification and reduction comparisons in its existing Session. This extends
NET's existing substrate, Legibility, Surface provenance fixes and the
correction journal without changing imported instrument packages. The
history-preserving 21-instrument consolidation is published on main through
[PR #126](https://github.com/atomtrapping/Notations-Systems-Terminal/pull/126).
Composition preserves that ancestry and the current import manifest.

## Run the polymer reference

```sh
python -m pip install -e '.[dev]'
net system demo --output-dir results/system-demo
net system inspect results/system-demo/workspace.json
```

Open `results/system-demo/review.html`. The demo compares 128, 64 and 4 thermal
cells, includes a specimen with both ends fixed, and repeats the detailed
configuration in an isolated subprocess. Accepted and failed approximation
criteria are both retained. The constants are illustrative; sensors are
synthetic. No material batch measurements or experimental validation were
supplied. All outputs retain `canonical_admission=false` and
`physical_validation_status=not_assessed`.

| Operation | Function | Contract |
| --- | --- | --- |
| `system.compile.v1` | Source specification to state schema and task graph | Strict registered models, typed quantities, shared geometry, ports, frames, same-step clock, resource bounds and simulation effects |
| `system.simulate.v1` | Retained plan occurrence to coupled candidate | Local, bounded subprocess or explicit digest-pinned OCI deployment |
| `system.verify.v1` | Retained candidate to distinct verification occurrence | Residuals, analytic checks, strain limits and refinement |
| `system.compare.v1` | Two retained candidates to reduction report | Declared mapping, identical physical parameters/clock, divisible uniform meshes, thresholds in K and m |
| `system.study.v1` | Retained plan to temporal accuracy study | Fixed spatial mesh, decreasing timesteps, common sample times including startup, bounded Fourier reference and empirical improvement |

The closed reference family accepts one thermal rod, one axial thermoelastic
model and one observation model. Source files cannot inject Python code,
imports, providers, arbitrary commands or actuator operations. Unsupported
models, units, frames, coupling schemes and cycles are refused. Resources and
retry contracts describe computation; no physical-control timing is implied.

The configuration identity retains geometry, mesh, model versions, parameters,
couplings, frame, clock and validity assumptions. Specification identity also
binds tolerances and resources. Changing cell count changes state dimension.
Representatives are retained; this version supplies no general symmetry quotient,
moduli-space construction or equivariance proof.

## Physics and acceptance scope

Thermal dynamics use conservative, cell-centered finite volumes and implicit
Euler. Fixed-temperature end baths use the half-cell distance. The same
end-of-step fluxes enter local equations, cumulative boundary heat and internal
energy change. The verifier independently reconstructs local and global
balances, the maximum principle and temperature bounds. A Fourier-series
cell-average reference includes a tail bound at specified benchmark times.
Combined mesh and timestep refinement measures reference error. A linear
steady solution checks the discrete operator.

The report explicitly distinguishes balance checks across the retained
trajectory from accuracy at sampled benchmark times. Startup contains a bath
temperature discontinuity; a later-time analytic pass does not bound error at
every earlier time or establish separate convergence orders.

Axial mechanics obey `du/dx = N/(EA) + alpha*(T-Tref)`. The left end is fixed.
The right end is either free under a prescribed force or fixed with its thermal
reaction computed. Prescribing an additional nonzero end force in the fixed
configuration is outside this reference contract. State includes displacement,
strain and stress; verification checks constitutive equilibrium, boundary
constraints and the declared small-strain regime.

The coupling is one-way and quasi-static: mechanical work does not feed back
into heat. Bending, buckling, viscoelasticity, plasticity, fracture, molecular
prediction and temperature-dependent coefficients remain additional models.
Sensor interpolation, bias, Gaussian standard deviations, seed and assumed
independent noise channels are explicit simulation contracts, not calibrated
instrument uncertainty.

Reduction evaluates `R(Phi_fine(t,x))` against `Phi_coarse(t,R(x))` at every
retained time. It uses a declared cell-volume average and coarse-node sampling,
records the discarded structure, and reports maximum temperature/displacement
error with its time range. Acceptance applies only to these conditions and
thresholds. Failed coarse mappings remain visible in the same family.

## Inspect and reproduce

```sh
ciw system compile examples/system-composition/polymer-thermomechanical.json \
  --output results/plan.json
ciw system run examples/system-composition/polymer-thermomechanical.json \
  --engine subprocess --output-dir results/polymer-worker
ciw system verify results/polymer-worker/workspace.json \
  --output-dir results/polymer-fresh-verification
```

Evidence, operation, execution, result and verification identities remain
separate. Every consuming operation must match the exact source occurrence
retained in Session. Reports bind the candidate and plan digests. The numerical
verifier payload owns its fresh `verification-...` ID; existing protocol-v1
results keep null verification IDs and `not_verified` status.

Saved inspection validates structure, seals, report coherence and dependency
bindings without solving or performing fresh numerical verification. Seals
establish local consistency, not independent attestation. Compile-to-run-to-verify
and both comparison inputs appear in NET's dependency projection. Configuration
specifications are retained through the `system-specification` source kind.
Compilation binds the exact retained source descriptor and bytes, alongside
the normalized specification identity. New configurations can extend the same
Session; the original investigation evidence remains immutable.

An accepted correction between retained specification sources makes the old
source and its dependent plans, candidates, verification, studies, comparisons
and claims stale. Their original records and observations remain inspectable;
the replacement source and its new results have separate identities. Staleness
is a dependency projection, not a rewritten numerical report or physical
validation verdict. The declared reviewer identity is not authentication.
Correction aliases follow exact raw evidence identity. Differently encoded
copies of a semantically equal specification have distinct raw evidence and
require explicit correction decisions; semantic equivalence alone does not
withdraw an unrelated source.

Legacy plans bound solely to the initial run's embedded specification remain
historical records with that original dependency. Once an equivalent
specification is imported as a retained source, new compilation requires its
explicit source binding. This prevents bypassing a corrected retained source
without inventing retrospective source relationships.

`ciw system inspect` prints compact reports; `--full` exposes retained details.
`ciw system review WORKSPACE --output review.html` exports synchronized thermal,
displacement and synthetic sensor views. It never reruns a scientific engine.

```sh
ciw system study results/system-demo/workspace.json \
  --output-dir results/temporal-study
```

Temporal studies keep the mesh fixed and assess all common base-clock samples
against the cell-average Fourier solution, including the first heating step.
Each timestep keeps its own plan and generated candidate digests; the compact
study retains error histories rather than complete candidate trajectories.
Those digests cannot reconstruct or independently attest the omitted states.
Reported improvement
is measured at those samples; it does not establish a theoretical convergence
order, unsampled-time accuracy, calibrated sensor uncertainty or experimental
validity. Insufficient analytic tail or work bounds yield explicit unassessed
samples. Startup failures remain visible even when later-time benchmarks pass.

The subprocess path has measured deterministic parity. OCI packaging is in
`containers/system-reference/`, with immutable image digest selection, no network,
resource limits and an explicit worker entrypoint. Local and subprocess execution
support Linux and Windows. Diagnostic draining uses bounded reader threads and
a queue; review files use explicit UTF-8 and LF output. The OCI adapter requires
POSIX user/group identity.

On deadline failure, the adapter terminates the selected worker and allows
bounded cleanup: up to five seconds to reap it and 0.2 seconds per diagnostic
reader. OCI failures additionally allow up to five seconds to remove the unique
task-owned container. These allowances can extend total elapsed time beyond the
requested worker timeout. The trusted reference worker currently spawns no
subprocesses. General descendant process-tree termination and inherited-pipe
cleanup are outside the qualified contract, especially on Windows.

Real OCI qualification is retained for PR head
`63425465fba5360ba6d8ed152f394ab4d34a3120`. The artifact from
[run 37107554510](https://github.com/atomtrapping/Notations-Systems-Terminal/actions/runs/37107554510)
records the tested hosted merge commit
`cf9412345304aa298dffd263feb75c2558fb586e`, tree
`ebeff80243e49e79cfc76cd51c44b45189b0ad2f`; all eight retained scientific-runtime
source hashes match the PR head. Its `checks.json` reports exact
local/subprocess/OCI candidate parity, separate numerical verification,
restoration and an actual container-policy probe. Artifact `11268349341`
records the selected worker repository digest
`sha256:71b4e3265af61d260fbe8ddd2ad65f0f6eb10779c9530ec08bd94d11454cfd05`.
The loopback image reference is a qualification fixture, not a public deployment.
Environment attestation and reproducible image bytes remain unestablished.

These are historical, runtime-bound observations. Integrating composition with
the newer main changes the source context and requires a fresh qualified head,
installed wheel, Linux/Windows checks and actual OCI execution before merging.
This local execution environment has no container engine, so that renewed OCI
qualification runs on provisioned hosted runners.

## Extend the family

Add trusted model descriptors, spaces, mappings and adapters through the existing
registries. Feedback couplings need their own solver, synchronization and
interface-balance contract. Coordinate equivariance and symmetry equivalence
need declared transformation laws. Independent parameter sweeps can consume
compiled configurations; distributed scheduling, checkpoints, actuator control
and interlocks remain further implementation work.

Material acceptance still requires batch/conditioning identity, measured data,
calibration separated from withheld validation, and a predefined validity regime.
Each molecular-to-material-to-component mapping must carry its own information
loss and error evidence.

Primary derivations: [NIST finite volumes](https://pages.nist.gov/fipy/en/stable/numerical/discret.html),
[NIST one-dimensional diffusion](https://github.com/usnistgov/fipy/blob/master/examples/diffusion/mesh1D.py),
and [MIT thermally constrained rod](https://ocw.mit.edu/courses/16-001-unified-engineering-materials-and-structures-fall-2021/mit16_001_f21_pset10_sol.pdf).
This reference implementation does not execute FiPy.
